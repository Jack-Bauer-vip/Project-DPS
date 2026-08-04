"""投前研究项目、版本、数据快照和文件缓存存储。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from qteasy_research.pretrade.schemas import (
    AssetProfile,
    AssetFactorResult,
    PortfolioAssetContext,
    ResearchReportDraft,
    ResearchDecision,
    ResearchNote,
    ResearchProject,
    ResearchProjectType,
    ResearchAssetReference,
    ResearchProjectStatus,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _decode(value: str | None) -> Any:
    return json.loads(value) if value else None


class ResearchStore:
    """SQLite 元数据索引 + 文件系统内容缓存。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "research.sqlite3"
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_run (
                    run_id TEXT PRIMARY KEY,
                    research_case_id TEXT NOT NULL,
                    code TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    project_id TEXT,
                    parent_run_id TEXT,
                    version_no INTEGER NOT NULL DEFAULT 1,
                    is_frozen INTEGER NOT NULL DEFAULT 0,
                    deleted_at TEXT,
                    deleted_by TEXT,
                    delete_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    result_path TEXT,
                    error TEXT
                )
            """)
            # 兼容阶段一已生成的旧 research.sqlite3。
            existing = {row[1] for row in connection.execute("PRAGMA table_info(research_run)")}
            for column, definition in {
                "project_id": "TEXT",
                "parent_run_id": "TEXT",
                "version_no": "INTEGER NOT NULL DEFAULT 1",
                "is_frozen": "INTEGER NOT NULL DEFAULT 0",
                "deleted_at": "TEXT",
                "deleted_by": "TEXT",
                "delete_reason": "TEXT",
            }.items():
                if column not in existing:
                    connection.execute(f"ALTER TABLE research_run ADD COLUMN {column} {definition}")

            for table in (
                "research_stage", "research_source_snapshot", "research_evidence",
                "research_metric_snapshot", "research_benchmark_snapshot",
                "research_strategy_fit", "research_report", "research_followup",
            ):
                connection.execute(f"""
                    CREATE TABLE IF NOT EXISTS {table} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)

            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_report_draft (
                    draft_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    project_id TEXT,
                    manual_summary TEXT NOT NULL DEFAULT '',
                    manual_conclusion TEXT NOT NULL DEFAULT '',
                    risk_judgment TEXT NOT NULL DEFAULT '',
                    falsification_conditions TEXT NOT NULL DEFAULT '',
                    followup_plan TEXT NOT NULL DEFAULT '',
                    author TEXT NOT NULL DEFAULT 'user',
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                )
            """)

            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_project (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    code TEXT NOT NULL,
                    objective TEXT,
                    horizon TEXT NOT NULL DEFAULT 'medium',
                    project_type TEXT NOT NULL DEFAULT 'ASSET_PROFILE',
                    strategy_name TEXT,
                    settings TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    closed_at TEXT,
                    archived_at TEXT
                )
            """)

            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_technical_snapshot (
                    technical_snapshot_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    code TEXT NOT NULL,
                    as_of TEXT,
                    input_snapshot_id TEXT,
                    data_hash TEXT,
                    config_hash TEXT NOT NULL,
                    formula_version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            project_columns = {row[1] for row in connection.execute("PRAGMA table_info(research_project)")}
            for column, definition in {
                "project_type": "TEXT NOT NULL DEFAULT 'ASSET_PROFILE'",
                "strategy_name": "TEXT",
                "settings": "TEXT NOT NULL DEFAULT '{}'",
            }.items():
                if column not in project_columns:
                    connection.execute(f"ALTER TABLE research_project ADD COLUMN {column} {definition}")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_project_asset (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    code TEXT NOT NULL,
                    asset_type TEXT,
                    name TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, code)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_project_asset_reference (
                    reference_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    asset_project_id TEXT NOT NULL,
                    asset_code TEXT NOT NULL,
                    asset_version_no INTEGER,
                    role TEXT NOT NULL DEFAULT 'candidate',
                    weight_limit REAL,
                    created_at TEXT NOT NULL,
                    deleted_at TEXT,
                    UNIQUE(project_id, asset_project_id)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_asset_profile (
                    code TEXT PRIMARY KEY,
                    name TEXT,
                    asset_type TEXT NOT NULL,
                    exchange TEXT,
                    benchmark TEXT,
                    fixed_metadata TEXT NOT NULL DEFAULT '{}',
                    source TEXT,
                    as_of TEXT,
                    profile_version INTEGER NOT NULL DEFAULT 1,
                    collected_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_asset_profile_snapshot (
                    profile_snapshot_id TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    profile_version INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    source TEXT,
                    as_of TEXT,
                    collected_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_asset_factor_snapshot (
                    factor_snapshot_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    code TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    as_of TEXT,
                    formula_version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_factor_research (
                    factor_research_id TEXT PRIMARY KEY,
                    factor_id TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    as_of TEXT,
                    formula_version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_asset_factor_exposure (
                    exposure_id TEXT PRIMARY KEY,
                    asset_code TEXT NOT NULL,
                    factor_id TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    as_of TEXT,
                    formula_version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_factor_match (
                    match_id TEXT PRIMARY KEY,
                    asset_code TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    as_of TEXT,
                    formula_version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS factor_definition (
                    factor_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    formula TEXT NOT NULL,
                    formula_hash TEXT NOT NULL,
                    direction INTEGER NOT NULL DEFAULT 1,
                    default_horizon TEXT NOT NULL DEFAULT 'medium',
                    supported_asset_types TEXT NOT NULL DEFAULT '[]',
                    value_scope TEXT NOT NULL DEFAULT 'asset',
                    missing_policy TEXT NOT NULL DEFAULT 'exclude',
                    hypothesis TEXT NOT NULL DEFAULT '',
                    research_summary TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS factor_activation_profile (
                    profile_id TEXT PRIMARY KEY,
                    factor_id TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    weight REAL NOT NULL DEFAULT 1.0,
                    max_drawdown_override REAL NOT NULL DEFAULT -0.15,
                    suspend_on_breach INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'ENABLED',
                    suspended_at TEXT,
                    suspend_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(factor_id, asset_type, horizon)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS etf_underlying_mapping (
                    mapping_id TEXT PRIMARY KEY,
                    etf_code TEXT NOT NULL,
                    underlying_type TEXT NOT NULL,
                    underlying_code TEXT NOT NULL,
                    effective_date TEXT NOT NULL,
                    expiry_date TEXT,
                    source TEXT NOT NULL DEFAULT '',
                    confidence REAL,
                    created_at TEXT NOT NULL,
                    UNIQUE(etf_code, effective_date)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS macro_regime_config (
                    config_id TEXT PRIMARY KEY,
                    factor_id TEXT,
                    category TEXT,
                    regime TEXT NOT NULL DEFAULT 'default',
                    modifier REAL NOT NULL DEFAULT 1.0,
                    effective_date TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(factor_id, category, regime, effective_date)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS global_etf_definition (
                    asset_code TEXT PRIMARY KEY,
                    research_asset_code TEXT NOT NULL,
                    trade_asset_code TEXT,
                    name TEXT NOT NULL DEFAULT '',
                    asset_type TEXT NOT NULL DEFAULT 'GLOBAL_ETF',
                    source TEXT NOT NULL DEFAULT '',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS global_etf_activation_profile (
                    profile_id TEXT PRIMARY KEY,
                    asset_code TEXT NOT NULL,
                    horizon TEXT NOT NULL DEFAULT 'medium',
                    enabled INTEGER NOT NULL DEFAULT 0,
                    frequency TEXT NOT NULL DEFAULT 'monthly',
                    status TEXT NOT NULL DEFAULT 'ENABLED',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(asset_code, horizon)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS global_etf_macro_rule (
                    rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_code TEXT NOT NULL,
                    macro_state TEXT NOT NULL,
                    modifier REAL NOT NULL,
                    sample_start TEXT,
                    sample_end TEXT,
                    sample_count INTEGER,
                    confidence TEXT,
                    approved_by TEXT NOT NULL DEFAULT 'manual',
                    approved_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    effective_date TEXT,
                    expiry_date TEXT,
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    UNIQUE(asset_code, macro_state, effective_date)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS global_etf_score_snapshot (
                    snapshot_id TEXT PRIMARY KEY,
                    target_date TEXT NOT NULL,
                    asset_code TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_global_etf_approved_rule
                ON global_etf_macro_rule(asset_code, macro_state)
                WHERE status='APPROVED'
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_global_etf_rule_lookup ON global_etf_macro_rule(asset_code, macro_state, status, effective_date)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_global_etf_snapshot_lookup ON global_etf_score_snapshot(asset_code, target_date)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS global_etf_trade_mapping (
                    mapping_id           TEXT PRIMARY KEY,
                    research_asset_code  TEXT NOT NULL,
                    trade_asset_code     TEXT NOT NULL,
                    trade_asset_name     TEXT NOT NULL DEFAULT '',
                    trade_market         TEXT NOT NULL DEFAULT '',
                    currency             TEXT NOT NULL DEFAULT 'CNY',
                    fx_pair              TEXT NOT NULL DEFAULT '',
                    fx_rule              TEXT NOT NULL DEFAULT 'static',
                    exchange_rate        REAL,
                    management_fee       REAL NOT NULL DEFAULT 0,
                    trading_cost_bps     REAL NOT NULL DEFAULT 0,
                    tracking_error       REAL,
                    premium_discount     REAL,
                    market_timezone      TEXT NOT NULL DEFAULT '',
                    trading_hours        TEXT NOT NULL DEFAULT '',
                    holiday_risk         TEXT NOT NULL DEFAULT '',
                    priority             INTEGER NOT NULL DEFAULT 1,
                    status               TEXT NOT NULL DEFAULT 'ACTIVE',
                    created_at           TEXT NOT NULL,
                    updated_at           TEXT NOT NULL,
                    UNIQUE(research_asset_code, trade_asset_code)
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_global_etf_trade_mapping_lookup ON global_etf_trade_mapping(research_asset_code, status, priority)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS factor_decision (
                    decision_id TEXT PRIMARY KEY,
                    factor_id TEXT NOT NULL,
                    profile_id TEXT,
                    action TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS factor_monitoring_snapshot (
                    snapshot_id TEXT PRIMARY KEY,
                    factor_id TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    long_return REAL,
                    short_return REAL,
                    long_short_return REAL,
                    cumulative_nav REAL,
                    drawdown REAL,
                    max_drawdown REAL,
                    status TEXT NOT NULL DEFAULT 'OK',
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_portfolio_asset_context (
                    context_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    code TEXT NOT NULL,
                    horizon TEXT NOT NULL DEFAULT 'medium',
                    weight REAL NOT NULL DEFAULT 0,
                    role TEXT NOT NULL DEFAULT 'candidate',
                    buy_condition TEXT NOT NULL DEFAULT '',
                    sell_condition TEXT NOT NULL DEFAULT '',
                    take_profit_condition TEXT NOT NULL DEFAULT '',
                    stop_loss_condition TEXT NOT NULL DEFAULT '',
                    hypothesis TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT,
                    UNIQUE(project_id, code)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_project_note (
                    note_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    title TEXT,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_project_decision (
                    decision_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    decision_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    author TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_project_revision (
                    revision_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_data_snapshot (
                    snapshot_id TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    data_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    as_of TEXT,
                    collected_at TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    payload_path TEXT NOT NULL,
                    freshness_days INTEGER NOT NULL DEFAULT 0,
                    official INTEGER NOT NULL DEFAULT 0,
                    conflict INTEGER NOT NULL DEFAULT 0,
                    reused INTEGER NOT NULL DEFAULT 0,
                    request TEXT NOT NULL DEFAULT '{}',
                    fields TEXT NOT NULL DEFAULT '[]',
                    quality TEXT NOT NULL DEFAULT '{}',
                    content_hash TEXT
                )
            """)
            snapshot_columns = {row[1] for row in connection.execute("PRAGMA table_info(research_data_snapshot)")}
            for column, definition in {
                "request": "TEXT NOT NULL DEFAULT '{}'",
                "fields": "TEXT NOT NULL DEFAULT '[]'",
                "quality": "TEXT NOT NULL DEFAULT '{}'",
                "content_hash": "TEXT",
            }.items():
                if column not in snapshot_columns:
                    connection.execute(f"ALTER TABLE research_data_snapshot ADD COLUMN {column} {definition}")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_cache_index (
                    cache_key TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    data_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    as_of TEXT,
                    collected_at TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    payload_path TEXT NOT NULL,
                    expires_at TEXT,
                    status TEXT NOT NULL DEFAULT 'AVAILABLE'
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_research_run_cache ON research_run(cache_key)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_project_run ON research_run(project_id, version_no)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_lookup ON research_data_snapshot(code, data_type, source)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_asset_factor_code ON research_asset_factor_snapshot(code, horizon, created_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_context_code ON research_portfolio_asset_context(code, project_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_factor_research_lookup ON research_factor_research(factor_id, horizon, created_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_factor_exposure_lookup ON research_asset_factor_exposure(asset_code, factor_id, horizon, created_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_factor_match_lookup ON research_factor_match(asset_code, horizon, created_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_factor_activation_lookup ON factor_activation_profile(asset_type, horizon, enabled, status)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_etf_mapping_lookup ON etf_underlying_mapping(etf_code, effective_date)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_factor_monitoring_lookup ON factor_monitoring_snapshot(factor_id, as_of)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS data_market_daily (
                    row_id TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    pre_close REAL,
                    volume REAL,
                    amount REAL,
                    source TEXT NOT NULL,
                    snapshot_id TEXT,
                    available_at TEXT,
                    revision_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    is_latest INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    UNIQUE(code, trade_date, source, revision_id)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS data_asset_metadata (
                    row_id TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    field_value TEXT,
                    source TEXT NOT NULL,
                    as_of TEXT,
                    available_at TEXT,
                    revision_id TEXT NOT NULL,
                    snapshot_id TEXT,
                    is_latest INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    UNIQUE(code, field_name, source, revision_id)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS data_fundamental_snapshot (
                    row_id TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    report_period TEXT,
                    value REAL,
                    unit TEXT,
                    source TEXT NOT NULL,
                    release_date TEXT,
                    available_at TEXT,
                    revision_id TEXT NOT NULL,
                    snapshot_id TEXT,
                    quality_level TEXT NOT NULL DEFAULT 'C',
                    created_at TEXT NOT NULL,
                    UNIQUE(code, metric, report_period, source, revision_id)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS data_factor_snapshot (
                    snapshot_id TEXT PRIMARY KEY,
                    factor_id TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    as_of TEXT,
                    file_path TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    content_hash TEXT,
                    formula_hash TEXT,
                    value_semantics TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS data_update_job (
                    job_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    datasets TEXT NOT NULL DEFAULT '[]',
                    codes TEXT NOT NULL DEFAULT '[]',
                    update_policy TEXT NOT NULL DEFAULT 'incremental',
                    status TEXT NOT NULL,
                    rows_added INTEGER NOT NULL DEFAULT 0,
                    rows_updated INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    error TEXT
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS data_update_attempt (
                    attempt_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    dataset TEXT NOT NULL,
                    code TEXT,
                    success INTEGER NOT NULL DEFAULT 0,
                    request TEXT NOT NULL DEFAULT '{}',
                    message TEXT NOT NULL DEFAULT '',
                    as_of TEXT,
                    rows_returned INTEGER NOT NULL DEFAULT 0,
                    latency_ms REAL,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_data_market_lookup ON data_market_daily(code, trade_date, is_latest)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_data_metadata_lookup ON data_asset_metadata(code, field_name, is_latest)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_data_factor_lookup ON data_factor_snapshot(factor_id, asset_type, horizon, created_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_data_job_lookup ON data_update_job(started_at, status)")
            definition_columns = {row[1] for row in connection.execute("PRAGMA table_info(factor_definition)")}
            for column, definition in {
                "hypothesis": "TEXT NOT NULL DEFAULT ''",
                "research_summary": "TEXT NOT NULL DEFAULT '{}'",
            }.items():
                if column not in definition_columns:
                    connection.execute(f"ALTER TABLE factor_definition ADD COLUMN {column} {definition}")

    # ---------- project CRUD ----------

    def create_project(self, project: ResearchProject, *, metadata: dict[str, Any] | None = None) -> ResearchProject:
        created = project.created_at or _now()
        project.created_at = created
        project.updated_at = project.updated_at or created
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_project
                (project_id,name,code,objective,horizon,project_type,strategy_name,settings,status,created_at,updated_at,closed_at,archived_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (project.project_id, project.name, project.code, project.objective, project.horizon,
                 project.project_type, project.strategy_name, _json(project.settings), project.status,
                 project.created_at, project.updated_at, project.closed_at, project.archived_at),
            )
            if metadata:
                connection.execute(
                    """INSERT OR REPLACE INTO research_project_asset
                    (project_id,code,asset_type,name,metadata,created_at)
                    VALUES (?,?,?,?,?,?)""",
                    (project.project_id, project.code, metadata.get("asset_type"), metadata.get("name"),
                     _json(metadata), created),
                )
        self.record_revision(project.project_id, "CREATE_PROJECT", asdict(project))
        return project

    def get_project(self, project_id: str) -> ResearchProject:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM research_project WHERE project_id=?", (project_id,)).fetchone()
        if not row:
            raise KeyError(f"研究项目不存在：{project_id}")
        return self._project_from_row(row)

    def list_projects(self, status: str | None = None) -> list[ResearchProject]:
        with self._connect() as connection:
            if status:
                rows = connection.execute(
                    "SELECT * FROM research_project WHERE status=? ORDER BY updated_at DESC", (status,)
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM research_project ORDER BY updated_at DESC").fetchall()
        return [self._project_from_row(row) for row in rows]

    @staticmethod
    def _project_from_row(row: sqlite3.Row) -> ResearchProject:
        data = dict(row)
        data["settings"] = _decode(data.get("settings")) or {}
        return ResearchProject(**data)

    # ---------- asset profile / factor / portfolio context ----------

    def save_asset_profile(self, profile: AssetProfile) -> AssetProfile:
        """保存当前基础档案，并在固定资料变化时生成新的档案快照。"""
        collected_at = profile.collected_at or _now()
        payload = {
            "code": profile.code,
            "name": profile.name,
            "asset_type": profile.asset_type,
            "exchange": profile.exchange,
            "benchmark": profile.benchmark,
            "fixed_metadata": profile.fixed_metadata,
        }
        content_hash = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
        with self._connect() as connection:
            current = connection.execute(
                "SELECT * FROM research_asset_profile WHERE code=?", (profile.code,)
            ).fetchone()
            previous_snapshot = connection.execute(
                """SELECT content_hash, profile_version FROM research_asset_profile_snapshot
                WHERE code=? ORDER BY collected_at DESC LIMIT 1""", (profile.code,)
            ).fetchone()
            if current:
                version = int(current["profile_version"])
            else:
                version = 0
            if previous_snapshot and previous_snapshot["content_hash"] == content_hash:
                version = int(previous_snapshot["profile_version"])
            else:
                version += 1
                connection.execute(
                    """INSERT INTO research_asset_profile_snapshot
                    (profile_snapshot_id,code,profile_version,content_hash,payload,source,as_of,collected_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (uuid.uuid4().hex, profile.code, version, content_hash, _json(payload),
                     profile.source, profile.as_of, collected_at),
                )
            connection.execute(
                """INSERT OR REPLACE INTO research_asset_profile
                (code,name,asset_type,exchange,benchmark,fixed_metadata,source,as_of,profile_version,collected_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (profile.code, profile.name, profile.asset_type, profile.exchange, profile.benchmark,
                 _json(profile.fixed_metadata), profile.source, profile.as_of, version, collected_at),
            )
        profile.profile_version = version
        profile.collected_at = collected_at
        return profile

    def get_asset_profile(self, code: str) -> AssetProfile | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM research_asset_profile WHERE code=?", (code,)).fetchone()
        if not row:
            return None
        data = dict(row)
        return AssetProfile(
            code=data["code"], name=data.get("name"), asset_type=data.get("asset_type", "UNKNOWN"),
            exchange=data.get("exchange"), benchmark=data.get("benchmark"),
            fixed_metadata=_decode(data.get("fixed_metadata")) or {}, source=data.get("source"),
            as_of=data.get("as_of"), profile_version=int(data.get("profile_version") or 1),
            collected_at=data.get("collected_at"),
        )

    def list_asset_profile_snapshots(self, code: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_asset_profile_snapshot WHERE code=? ORDER BY profile_version DESC",
                (code,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = _decode(item.get("payload")) or {}
            result.append(item)
        return result

    def save_asset_factor_snapshot(
        self,
        *,
        run_id: str,
        code: str,
        horizon: str,
        as_of: str | None,
        formula_version: str,
        payload: dict[str, Any],
    ) -> str:
        snapshot_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_asset_factor_snapshot
                (factor_snapshot_id,run_id,code,horizon,as_of,formula_version,payload,created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (snapshot_id, run_id, code, horizon, as_of, formula_version, _json(payload), _now()),
            )
        return snapshot_id

    def list_asset_factor_snapshots(self, code: str, *, horizon: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            if horizon:
                rows = connection.execute(
                    """SELECT * FROM research_asset_factor_snapshot
                    WHERE code=? AND horizon=? ORDER BY created_at DESC LIMIT ?""",
                    (code, horizon, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM research_asset_factor_snapshot
                    WHERE code=? ORDER BY created_at DESC LIMIT ?""",
                    (code, limit),
                ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = _decode(item.get("payload")) or {}
            result.append(item)
        return result

    def save_factor_research(
        self,
        *,
        factor_id: str,
        horizon: str,
        as_of: str | None,
        formula_version: str,
        payload: dict[str, Any],
    ) -> str:
        research_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_factor_research
                (factor_research_id,factor_id,horizon,as_of,formula_version,payload,created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (research_id, factor_id, horizon, as_of, formula_version, _json(payload), _now()),
            )
        return research_id

    def list_factor_research(self, factor_id: str | None = None, *, horizon: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if factor_id:
            clauses.append("factor_id=?")
            values.append(factor_id)
        if horizon:
            clauses.append("horizon=?")
            values.append(horizon)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM research_factor_research {where} ORDER BY created_at DESC LIMIT ?",
                (*values, limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = _decode(item.get("payload")) or {}
            result.append(item)
        return result

    def save_factor_exposure(
        self,
        *,
        asset_code: str,
        factor_id: str,
        horizon: str,
        as_of: str | None,
        formula_version: str,
        payload: dict[str, Any],
    ) -> str:
        exposure_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_asset_factor_exposure
                (exposure_id,asset_code,factor_id,horizon,as_of,formula_version,payload,created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (exposure_id, asset_code, factor_id, horizon, as_of, formula_version, _json(payload), _now()),
            )
        return exposure_id

    def list_factor_exposures(self, asset_code: str, *, factor_id: str | None = None, horizon: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses = ["asset_code=?"]
        values: list[Any] = [asset_code]
        if factor_id:
            clauses.append("factor_id=?")
            values.append(factor_id)
        if horizon:
            clauses.append("horizon=?")
            values.append(horizon)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM research_asset_factor_exposure WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?",
                (*values, limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = _decode(item.get("payload")) or {}
            result.append(item)
        return result

    def save_factor_match(
        self,
        *,
        asset_code: str,
        horizon: str,
        as_of: str | None,
        formula_version: str,
        payload: dict[str, Any],
    ) -> str:
        match_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_factor_match
                (match_id,asset_code,horizon,as_of,formula_version,payload,created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (match_id, asset_code, horizon, as_of, formula_version, _json(payload), _now()),
            )
        return match_id

    def list_factor_matches(self, asset_code: str, *, horizon: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses = ["asset_code=?"]
        values: list[Any] = [asset_code]
        if horizon:
            clauses.append("horizon=?")
            values.append(horizon)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM research_factor_match WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?",
                (*values, limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = _decode(item.get("payload")) or {}
            result.append(item)
        return result

    # ---------- production factor configuration ----------

    def upsert_factor_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        payload = dict(definition)
        payload.setdefault("supported_asset_types", [])
        payload.setdefault("value_scope", "asset")
        payload.setdefault("missing_policy", "exclude")
        payload.setdefault("status", "ACTIVE")
        payload.setdefault("created_at", now)
        payload["updated_at"] = now
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT created_at FROM factor_definition WHERE factor_id=?",
                (payload["factor_id"],),
            ).fetchone()
            if previous:
                payload["created_at"] = previous["created_at"]
            connection.execute(
                """INSERT OR REPLACE INTO factor_definition
                (factor_id,name,category,formula,formula_hash,direction,default_horizon,
                 supported_asset_types,value_scope,missing_policy,hypothesis,research_summary,
                 status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    payload["factor_id"], payload["name"], payload["category"], payload["formula"],
                    payload["formula_hash"], int(payload.get("direction", 1)),
                    payload.get("default_horizon", "medium"), _json(payload["supported_asset_types"]),
                    payload["value_scope"], payload["missing_policy"], payload.get("hypothesis", ""),
                    _json(payload.get("research_summary", {})), payload["status"],
                    payload["created_at"], payload["updated_at"],
                ),
            )
        return payload

    def list_factor_definitions(self, *, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM factor_definition"
        values: tuple[Any, ...] = ()
        if status:
            query += " WHERE status=?"
            values = (status,)
        query += " ORDER BY factor_id"
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["supported_asset_types"] = _decode(item.get("supported_asset_types")) or []
            item["research_summary"] = _decode(item.get("research_summary")) or {}
            result.append(item)
        return result

    def upsert_factor_activation(self, profile: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        payload = dict(profile)
        payload.setdefault("profile_id", uuid.uuid4().hex)
        payload.setdefault("enabled", 0)
        payload.setdefault("weight", 1.0)
        payload.setdefault("max_drawdown_override", -0.15)
        payload.setdefault("suspend_on_breach", 1)
        payload.setdefault("status", "ENABLED")
        payload.setdefault("created_at", now)
        payload["updated_at"] = now
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT profile_id,created_at FROM factor_activation_profile WHERE factor_id=? AND asset_type=? AND horizon=?",
                (payload["factor_id"], payload["asset_type"], payload["horizon"]),
            ).fetchone()
            if previous:
                payload["profile_id"] = previous["profile_id"]
                payload["created_at"] = previous["created_at"]
            connection.execute(
                """INSERT OR REPLACE INTO factor_activation_profile
                (profile_id,factor_id,asset_type,horizon,enabled,weight,max_drawdown_override,
                 suspend_on_breach,status,suspended_at,suspend_reason,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    payload["profile_id"], payload["factor_id"], payload["asset_type"], payload["horizon"],
                    int(payload["enabled"]), float(payload["weight"]), float(payload["max_drawdown_override"]),
                    int(payload["suspend_on_breach"]), payload["status"], payload.get("suspended_at"),
                    payload.get("suspend_reason"), payload["created_at"], payload["updated_at"],
                ),
            )
        return payload

    def list_factor_activations(
        self, *, asset_type: str | None = None, horizon: str | None = None, enabled_only: bool = False
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if asset_type:
            clauses.append("p.asset_type=?")
            values.append(asset_type)
        if horizon:
            clauses.append("p.horizon=?")
            values.append(horizon)
        if enabled_only:
            clauses.extend(["p.enabled=1", "p.status='ENABLED'"])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT p.*, d.name, d.category, d.formula, d.formula_hash, d.direction,
                    d.supported_asset_types, d.value_scope, d.missing_policy
                    , d.hypothesis, d.research_summary
                    FROM factor_activation_profile p
                    JOIN factor_definition d ON d.factor_id=p.factor_id
                    {where} ORDER BY p.factor_id""",
                values,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["supported_asset_types"] = _decode(item.get("supported_asset_types")) or []
            item["research_summary"] = _decode(item.get("research_summary")) or {}
            result.append(item)
        return result

    def upsert_etf_underlying_mapping(self, mapping: dict[str, Any]) -> dict[str, Any]:
        payload = dict(mapping)
        payload.setdefault("mapping_id", uuid.uuid4().hex)
        payload.setdefault("source", "")
        payload.setdefault("created_at", _now())
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT mapping_id FROM etf_underlying_mapping WHERE etf_code=? AND effective_date=?",
                (payload["etf_code"], payload["effective_date"]),
            ).fetchone()
            if previous:
                payload["mapping_id"] = previous["mapping_id"]
            connection.execute(
                """INSERT OR REPLACE INTO etf_underlying_mapping
                (mapping_id,etf_code,underlying_type,underlying_code,effective_date,expiry_date,
                 source,confidence,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    payload["mapping_id"], payload["etf_code"], payload["underlying_type"],
                    payload["underlying_code"], payload["effective_date"], payload.get("expiry_date"),
                    payload["source"], payload.get("confidence"), payload["created_at"],
                ),
            )
        return payload

    def get_etf_underlying(self, etf_code: str, target_date: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM etf_underlying_mapping
                WHERE etf_code=? AND effective_date<=?
                  AND (expiry_date IS NULL OR expiry_date>=?)
                ORDER BY effective_date DESC LIMIT 1""",
                (etf_code, target_date, target_date),
            ).fetchone()
        return dict(row) if row else None

    def upsert_macro_modifier(self, modifier: dict[str, Any]) -> dict[str, Any]:
        payload = dict(modifier)
        payload.setdefault("config_id", uuid.uuid4().hex)
        payload.setdefault("regime", "default")
        payload.setdefault("modifier", 1.0)
        payload.setdefault("created_at", _now())
        with self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO macro_regime_config
                (config_id,factor_id,category,regime,modifier,effective_date,created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    payload["config_id"], payload.get("factor_id"), payload.get("category"),
                    payload["regime"], float(payload["modifier"]), payload.get("effective_date"),
                    payload["created_at"],
                ),
            )
        return payload

    def get_macro_modifier(
        self, *, factor_id: str, category: str, target_date: str, regime: str | None = None
    ) -> float:
        regime_value = regime or "default"
        with self._connect() as connection:
            row = connection.execute(
                """SELECT modifier FROM macro_regime_config
                WHERE factor_id=? AND regime=?
                  AND (effective_date IS NULL OR effective_date<=?)
                ORDER BY effective_date DESC LIMIT 1""",
                (factor_id, regime_value, target_date),
            ).fetchone()
            if not row:
                row = connection.execute(
                    """SELECT modifier FROM macro_regime_config
                    WHERE factor_id IS NULL AND category=? AND regime=?
                      AND (effective_date IS NULL OR effective_date<=?)
                    ORDER BY effective_date DESC LIMIT 1""",
                    (category, regime_value, target_date),
                ).fetchone()
        return float(row["modifier"]) if row else 1.0

    # ---------- global ETF macro research ----------

    def upsert_global_etf_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        payload = dict(definition)
        payload.setdefault("asset_type", "GLOBAL_ETF")
        payload.setdefault("source", "")
        payload.setdefault("metadata", {})
        payload.setdefault("status", "ACTIVE")
        payload.setdefault("created_at", now)
        payload["updated_at"] = now
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT created_at FROM global_etf_definition WHERE asset_code=?",
                (payload["asset_code"],),
            ).fetchone()
            if previous:
                payload["created_at"] = previous["created_at"]
            connection.execute(
                """INSERT OR REPLACE INTO global_etf_definition
                (asset_code,research_asset_code,trade_asset_code,name,asset_type,source,
                 metadata,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    payload["asset_code"], payload["research_asset_code"], payload.get("trade_asset_code"),
                    payload.get("name", ""), payload["asset_type"], payload["source"],
                    _json(payload["metadata"]), payload["status"], payload["created_at"], payload["updated_at"],
                ),
            )
        return payload

    def list_global_etf_definitions(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM global_etf_definition"
        if enabled_only:
            query += " WHERE status='ACTIVE'"
        query += " ORDER BY asset_code"
        with self._connect() as connection:
            rows = connection.execute(query).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = _decode(item.get("metadata")) or {}
            result.append(item)
        return result

    def upsert_global_etf_activation(self, profile: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        payload = dict(profile)
        payload.setdefault("profile_id", uuid.uuid4().hex)
        payload.setdefault("horizon", "medium")
        payload.setdefault("enabled", 0)
        payload.setdefault("frequency", "monthly")
        payload.setdefault("status", "ENABLED")
        payload.setdefault("created_at", now)
        payload["updated_at"] = now
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT profile_id,created_at FROM global_etf_activation_profile WHERE asset_code=? AND horizon=?",
                (payload["asset_code"], payload["horizon"]),
            ).fetchone()
            if previous:
                payload["profile_id"] = previous["profile_id"]
                payload["created_at"] = previous["created_at"]
            connection.execute(
                """INSERT OR REPLACE INTO global_etf_activation_profile
                (profile_id,asset_code,horizon,enabled,frequency,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (
                    payload["profile_id"], payload["asset_code"], payload["horizon"], int(payload["enabled"]),
                    payload["frequency"], payload["status"], payload["created_at"], payload["updated_at"],
                ),
            )
        return payload

    def list_global_etf_activations(
        self, *, horizon: str | None = None, enabled_only: bool = False
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if horizon:
            clauses.append("horizon=?")
            values.append(horizon)
        if enabled_only:
            clauses.extend(["enabled=1", "status='ENABLED'"])
        query = "SELECT * FROM global_etf_activation_profile"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY asset_code"
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(query, values).fetchall()]

    def upsert_global_etf_macro_rule(self, rule: dict[str, Any]) -> dict[str, Any]:
        payload = dict(rule)
        payload.setdefault("status", "DRAFT")
        payload.setdefault("approved_by", "manual")
        if payload.get("status") == "APPROVED" and not payload.get("approved_at"):
            payload["approved_at"] = _now()
        with self._connect() as connection:
            existing = connection.execute(
                """SELECT rule_id FROM global_etf_macro_rule
                WHERE asset_code=? AND macro_state=? AND effective_date IS ?""",
                (payload["asset_code"], payload["macro_state"], payload.get("effective_date")),
            ).fetchone()
            if existing:
                payload["rule_id"] = existing["rule_id"]
                connection.execute(
                    """UPDATE global_etf_macro_rule SET modifier=?,sample_start=?,sample_end=?,sample_count=?,
                    confidence=?,approved_by=?,approved_at=?,effective_date=?,expiry_date=?,status=? WHERE rule_id=?""",
                    (
                        float(payload["modifier"]), payload.get("sample_start"), payload.get("sample_end"),
                        payload.get("sample_count"), payload.get("confidence"), payload["approved_by"],
                        payload.get("approved_at"), payload.get("effective_date"), payload.get("expiry_date"),
                        payload["status"], payload["rule_id"],
                    ),
                )
            else:
                cursor = connection.execute(
                    """INSERT INTO global_etf_macro_rule
                    (asset_code,macro_state,modifier,sample_start,sample_end,sample_count,confidence,
                     approved_by,approved_at,effective_date,expiry_date,status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        payload["asset_code"], payload["macro_state"], float(payload["modifier"]),
                        payload.get("sample_start"), payload.get("sample_end"), payload.get("sample_count"),
                        payload.get("confidence"), payload["approved_by"], payload.get("approved_at"),
                        payload.get("effective_date"), payload.get("expiry_date"), payload["status"],
                    ),
                )
                payload["rule_id"] = cursor.lastrowid
        return payload

    def list_global_etf_macro_rules(
        self, *, asset_code: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if asset_code:
            clauses.append("asset_code=?")
            values.append(asset_code)
        if status:
            clauses.append("status=?")
            values.append(status)
        query = "SELECT * FROM global_etf_macro_rule"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY asset_code, macro_state, effective_date"
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(query, values).fetchall()]

    def get_global_etf_macro_rules(
        self, *, asset_code: str, macro_states: list[str], target_date: str
    ) -> list[dict[str, Any]]:
        if not macro_states:
            return []
        placeholders = ",".join("?" for _ in macro_states)
        values: list[Any] = [asset_code, *macro_states, target_date, target_date]
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM global_etf_macro_rule
                WHERE asset_code=? AND macro_state IN ({placeholders}) AND status='APPROVED'
                  AND (effective_date IS NULL OR effective_date<=?)
                  AND (expiry_date IS NULL OR expiry_date>=?)
                ORDER BY macro_state""",
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def save_global_etf_score_snapshot(self, payload: dict[str, Any]) -> str:
        snapshot_id = payload.get("snapshot_id") or uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO global_etf_score_snapshot
                (snapshot_id,target_date,asset_code,payload,created_at)
                VALUES (?,?,?,?,?)""",
                (snapshot_id, payload["target_date"], payload["asset_code"], _json(payload), _now()),
            )
        return snapshot_id

    # ---- 全球 ETF 研究资产 ↔ 交易资产映射 ----

    _TRADE_MAPPING_STATUS_VALUES = {"ACTIVE", "INACTIVE"}
    _FX_RULE_VALUES = {"static", "manual", "realtime", "estimate"}

    def upsert_global_etf_trade_mapping(self, mapping: dict[str, Any]) -> dict[str, Any]:
        """新增或更新研究资产↔交易资产映射。

        保留 mapping_id 与 created_at（按 research_asset_code+trade_asset_code 查回）；
        校验研究资产存在性与字段取值范围；不自动生成交易指令、不联动其他表。
        """
        now = _now()
        payload = dict(mapping)
        payload.setdefault("mapping_id", uuid.uuid4().hex)
        payload.setdefault("trade_asset_name", "")
        payload.setdefault("trade_market", "")
        payload.setdefault("currency", "CNY")
        payload.setdefault("fx_pair", "")
        payload.setdefault("fx_rule", "static")
        payload.setdefault("management_fee", 0.0)
        payload.setdefault("trading_cost_bps", 0.0)
        payload.setdefault("market_timezone", "")
        payload.setdefault("trading_hours", "")
        payload.setdefault("holiday_risk", "")
        payload.setdefault("priority", 1)
        payload.setdefault("status", "ACTIVE")
        payload.setdefault("created_at", now)
        payload["updated_at"] = now

        research_asset = payload["research_asset_code"]
        trade_asset = payload["trade_asset_code"]
        if not research_asset or not trade_asset:
            raise ValueError("研究资产代码与交易资产代码为必填项")
        if payload["status"] not in self._TRADE_MAPPING_STATUS_VALUES:
            raise ValueError(f"status 必须是 {sorted(self._TRADE_MAPPING_STATUS_VALUES)}，收到：{payload['status']}")
        if not isinstance(payload["priority"], int) or payload["priority"] < 1:
            raise ValueError("priority 必须是大于等于 1 的整数")
        if payload["management_fee"] < 0 or payload["trading_cost_bps"] < 0:
            raise ValueError("management_fee 与 trading_cost_bps 不能为负")
        if payload["fx_rule"] not in self._FX_RULE_VALUES:
            raise ValueError(f"fx_rule 必须是 {sorted(self._FX_RULE_VALUES)}，收到：{payload['fx_rule']}")
        if payload["fx_rule"] == "static":
            rate = payload.get("exchange_rate")
            if rate is None or float(rate) <= 0:
                raise ValueError("fx_rule=static 时必须提供正数 exchange_rate")

        with self._connect() as connection:
            definition = connection.execute(
                "SELECT asset_code FROM global_etf_definition WHERE asset_code=?",
                (research_asset,),
            ).fetchone()
            if definition is None:
                raise ValueError(f"研究资产不存在：{research_asset}，请先注册 global_etf_definition")
            previous = connection.execute(
                """SELECT mapping_id, created_at FROM global_etf_trade_mapping
                WHERE research_asset_code=? AND trade_asset_code=?""",
                (research_asset, trade_asset),
            ).fetchone()
            if previous:
                payload["mapping_id"] = previous["mapping_id"]
                payload["created_at"] = previous["created_at"]
            connection.execute(
                """INSERT OR REPLACE INTO global_etf_trade_mapping
                (mapping_id,research_asset_code,trade_asset_code,trade_asset_name,trade_market,
                 currency,fx_pair,fx_rule,exchange_rate,management_fee,trading_cost_bps,
                 tracking_error,premium_discount,market_timezone,trading_hours,holiday_risk,
                 priority,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    payload["mapping_id"], payload["research_asset_code"], payload["trade_asset_code"],
                    payload["trade_asset_name"], payload["trade_market"], payload["currency"],
                    payload["fx_pair"], payload["fx_rule"], payload.get("exchange_rate"),
                    float(payload["management_fee"]), float(payload["trading_cost_bps"]),
                    payload.get("tracking_error"), payload.get("premium_discount"),
                    payload["market_timezone"], payload["trading_hours"], payload["holiday_risk"],
                    payload["priority"], payload["status"], payload["created_at"], payload["updated_at"],
                ),
            )
        return payload

    def list_global_etf_trade_mappings(
        self,
        *,
        research_asset_code: str | None = None,
        trade_asset_code: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """按研究资产/交易资产/状态过滤；ORDER BY research_asset_code, priority, trade_asset_code。"""
        clauses: list[str] = []
        values: list[Any] = []
        if research_asset_code:
            clauses.append("research_asset_code=?")
            values.append(research_asset_code)
        if trade_asset_code:
            clauses.append("trade_asset_code=?")
            values.append(trade_asset_code)
        if status:
            clauses.append("status=?")
            values.append(status)
        query = "SELECT * FROM global_etf_trade_mapping"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY research_asset_code, priority, trade_asset_code"
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(query, values).fetchall()]

    def get_global_etf_trade_mappings(
        self,
        research_asset_code: str,
        *,
        status: str = "ACTIVE",
    ) -> list[dict[str, Any]]:
        """取某研究资产的可用交易资产映射（默认仅 ACTIVE），按 priority 升序。"""
        return self.list_global_etf_trade_mappings(
            research_asset_code=research_asset_code,
            status=status,
        )

    def delete_global_etf_trade_mapping(self, mapping_id: str) -> None:
        """删除一条交易资产映射（映射是当前配置，无软删；INACTIVE 已表达停用）。"""
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM global_etf_trade_mapping WHERE mapping_id=?",
                (mapping_id,),
            )

    def save_factor_monitoring_snapshot(self, payload: dict[str, Any]) -> str:
        snapshot_id = payload.get("snapshot_id") or uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO factor_monitoring_snapshot
                (snapshot_id,factor_id,as_of,long_return,short_return,long_short_return,
                 cumulative_nav,drawdown,max_drawdown,status,payload,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    snapshot_id, payload["factor_id"], payload["as_of"], payload.get("long_return"),
                    payload.get("short_return"), payload.get("long_short_return"), payload.get("cumulative_nav"),
                    payload.get("drawdown"), payload.get("max_drawdown"), payload.get("status", "OK"),
                    _json(payload), _now(),
                ),
            )
        return snapshot_id

    def suspend_factor(self, profile_id: str, *, reason: str, as_of: str) -> None:
        now = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT factor_id FROM factor_activation_profile WHERE profile_id=?", (profile_id,)
            ).fetchone()
            if not row:
                raise KeyError(profile_id)
            connection.execute(
                """UPDATE factor_activation_profile
                SET status='SUSPENDED', suspended_at=?, suspend_reason=?, updated_at=?
                WHERE profile_id=?""",
                (as_of, reason, now, profile_id),
            )
            connection.execute(
                """INSERT INTO factor_decision
                (decision_id,factor_id,profile_id,action,reason,payload,created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (uuid.uuid4().hex, row["factor_id"], profile_id, "SUSPEND", reason, _json({"as_of": as_of}), now),
            )

    def restore_factor(self, profile_id: str, *, reason: str = "manual restore") -> None:
        now = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT factor_id FROM factor_activation_profile WHERE profile_id=?", (profile_id,)
            ).fetchone()
            if not row:
                raise KeyError(profile_id)
            connection.execute(
                """UPDATE factor_activation_profile
                SET status='ENABLED', suspended_at=NULL, suspend_reason=NULL, updated_at=?
                WHERE profile_id=?""",
                (now, profile_id),
            )
            connection.execute(
                """INSERT INTO factor_decision
                (decision_id,factor_id,profile_id,action,reason,payload,created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (uuid.uuid4().hex, row["factor_id"], profile_id, "RESTORE", reason, "{}", now),
            )

    def upsert_portfolio_asset_context(self, context: PortfolioAssetContext) -> PortfolioAssetContext:
        self._assert_project_writable(context.project_id)
        now = _now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT context_id,created_at FROM research_portfolio_asset_context WHERE project_id=? AND code=? AND deleted_at IS NULL",
                (context.project_id, context.code),
            ).fetchone()
            context.context_id = existing["context_id"] if existing else (context.context_id or uuid.uuid4().hex)
            context.created_at = existing["created_at"] if existing else (context.created_at or now)
            context.updated_at = now
            connection.execute(
                """INSERT OR REPLACE INTO research_portfolio_asset_context
                (context_id,project_id,code,horizon,weight,role,buy_condition,sell_condition,
                 take_profit_condition,stop_loss_condition,hypothesis,enabled,created_at,updated_at,deleted_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
                (context.context_id, context.project_id, context.code, context.horizon, context.weight,
                 context.role, context.buy_condition, context.sell_condition, context.take_profit_condition,
                 context.stop_loss_condition, context.hypothesis, int(context.enabled), context.created_at, context.updated_at),
            )
        self.record_revision(context.project_id, "UPSERT_PORTFOLIO_ASSET_CONTEXT", asdict(context))
        return context

    def delete_portfolio_asset_context(self, project_id: str, code: str) -> None:
        """逻辑删除组合中的资产引用，保留历史审计记录。"""
        self._assert_project_writable(project_id)
        now = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_portfolio_asset_context WHERE project_id=? AND code=? AND deleted_at IS NULL",
                (project_id, code),
            ).fetchone()
            if not row:
                return
            connection.execute(
                "UPDATE research_portfolio_asset_context SET deleted_at=?, updated_at=? WHERE context_id=?",
                (now, now, row["context_id"]),
            )
        payload = dict(row)
        payload["deleted_at"] = now
        self.record_revision(project_id, "DELETE_PORTFOLIO_ASSET_CONTEXT", payload)

    def list_portfolio_asset_contexts(self, *, project_id: str | None = None, code: str | None = None) -> list[PortfolioAssetContext]:
        clauses = ["deleted_at IS NULL"]
        values: list[Any] = []
        if project_id:
            clauses.append("project_id=?")
            values.append(project_id)
        if code:
            clauses.append("code=?")
            values.append(code)
        query = f"SELECT * FROM research_portfolio_asset_context WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [PortfolioAssetContext(**dict(row)) for row in rows]

    def list_runs_for_code(self, code: str, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        with self._connect() as connection:
            where = "" if include_deleted else " AND deleted_at IS NULL"
            rows = connection.execute(
                f"SELECT * FROM research_run WHERE code=? AND result_path IS NOT NULL{where} ORDER BY version_no DESC",
                (code,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_project(self, project_id: str, **changes: Any) -> ResearchProject:
        project = self.get_project(project_id)
        allowed = {"name", "code", "objective", "horizon", "project_type", "strategy_name", "settings", "status"}
        changes = {key: value for key, value in changes.items() if key in allowed}
        if not changes:
            return project
        if project.status in {ResearchProjectStatus.CLOSED.value, ResearchProjectStatus.ARCHIVED.value}:
            raise ValueError("已关闭或已归档项目不可直接修改，请创建新版本")
        changes["updated_at"] = _now()
        if "settings" in changes:
            changes["settings"] = _json(changes["settings"])
        assignments = ", ".join(f"{key}=?" for key in changes)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE research_project SET {assignments} WHERE project_id=?",
                (*changes.values(), project_id),
            )
        self.record_revision(project_id, "UPDATE_PROJECT", changes)
        return self.get_project(project_id)

    def add_asset_reference(
        self,
        project_id: str,
        asset_project_id: str,
        *,
        asset_version_no: int | None = None,
        role: str = "candidate",
        weight_limit: float | None = None,
    ) -> ResearchAssetReference:
        self._assert_project_writable(project_id)
        project = self.get_project(project_id)
        asset_project = self.get_project(asset_project_id)
        if project.project_type != ResearchProjectType.STRATEGY_PORTFOLIO.value:
            raise ValueError("只有策略/组合项目可以关联资产档案")
        if asset_project.project_type != ResearchProjectType.ASSET_PROFILE.value:
            raise ValueError("被关联项目必须是单标的资产档案")
        reference = ResearchAssetReference(
            reference_id=uuid.uuid4().hex,
            project_id=project_id,
            asset_project_id=asset_project_id,
            asset_code=asset_project.code,
            asset_version_no=asset_version_no,
            role=role,
            weight_limit=weight_limit,
            created_at=_now(),
        )
        with self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO research_project_asset_reference
                (reference_id,project_id,asset_project_id,asset_code,asset_version_no,role,weight_limit,created_at,deleted_at)
                VALUES (?,?,?,?,?,?,?,?,NULL)""",
                (reference.reference_id, reference.project_id, reference.asset_project_id, reference.asset_code,
                 reference.asset_version_no, reference.role, reference.weight_limit, reference.created_at),
            )
        self.record_revision(project_id, "ADD_ASSET_REFERENCE", asdict(reference))
        return reference

    def list_asset_references(self, project_id: str, *, include_deleted: bool = False) -> list[ResearchAssetReference]:
        where = "" if include_deleted else " AND deleted_at IS NULL"
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM research_project_asset_reference WHERE project_id=?{where} ORDER BY created_at", (project_id,)
            ).fetchall()
        return [ResearchAssetReference(**dict(row)) for row in rows]

    def delete_asset_reference(self, reference_id: str) -> None:
        with self._connect() as connection:
            row = connection.execute("SELECT project_id FROM research_project_asset_reference WHERE reference_id=?", (reference_id,)).fetchone()
        if not row:
            raise KeyError(f"资产档案引用不存在：{reference_id}")
        self._assert_project_writable(row["project_id"])
        with self._connect() as connection:
            connection.execute("UPDATE research_project_asset_reference SET deleted_at=? WHERE reference_id=?", (_now(), reference_id))
        self.record_revision(row["project_id"], "DELETE_ASSET_REFERENCE", {"reference_id": reference_id})

    def set_project_status(self, project_id: str, status: str) -> ResearchProject:
        valid = {item.value for item in ResearchProjectStatus}
        if status not in valid:
            raise ValueError(f"无效的研究项目状态：{status}")
        project = self.get_project(project_id)
        now = _now()
        changes: dict[str, Any] = {"status": status, "updated_at": now}
        if status == ResearchProjectStatus.CLOSED.value:
            changes["closed_at"] = now
        if status == ResearchProjectStatus.ARCHIVED.value:
            changes["archived_at"] = now
        with self._connect() as connection:
            assignments = ", ".join(f"{key}=?" for key in changes)
            connection.execute(f"UPDATE research_project SET {assignments} WHERE project_id=?", (*changes.values(), project_id))
        self.record_revision(project_id, "SET_STATUS", {"from": project.status, "to": status})
        return self.get_project(project_id)

    # ---------- notes and decisions ----------

    def add_note(self, project_id: str, content: str, *, title: str | None = None) -> ResearchNote:
        self._assert_project_writable(project_id)
        note = ResearchNote(note_id=uuid.uuid4().hex, project_id=project_id, title=title, content=content, created_at=_now(), updated_at=_now())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO research_project_note VALUES (?,?,?,?,?,?,?)",
                (note.note_id, note.project_id, note.title, note.content, note.created_at, note.updated_at, None),
            )
        self.record_revision(project_id, "ADD_NOTE", asdict(note))
        return note

    def list_notes(self, project_id: str, *, include_deleted: bool = False) -> list[ResearchNote]:
        where = "" if include_deleted else " AND deleted_at IS NULL"
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM research_project_note WHERE project_id=?{where} ORDER BY updated_at DESC", (project_id,)
            ).fetchall()
        return [ResearchNote(**dict(row)) for row in rows]

    def update_note(self, note_id: str, content: str, *, title: str | None = None) -> ResearchNote:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM research_project_note WHERE note_id=?", (note_id,)).fetchone()
        if not row:
            raise KeyError(f"研究笔记不存在：{note_id}")
        self._assert_project_writable(row["project_id"])
        now = _now()
        with self._connect() as connection:
            connection.execute("UPDATE research_project_note SET title=?,content=?,updated_at=? WHERE note_id=?", (title, content, now, note_id))
        self.record_revision(row["project_id"], "UPDATE_NOTE", {"note_id": note_id, "title": title, "content": content})
        return next(note for note in self.list_notes(row["project_id"]) if note.note_id == note_id)

    def delete_note(self, note_id: str) -> None:
        with self._connect() as connection:
            row = connection.execute("SELECT project_id FROM research_project_note WHERE note_id=?", (note_id,)).fetchone()
        if not row:
            raise KeyError(f"研究笔记不存在：{note_id}")
        self._assert_project_writable(row["project_id"])
        now = _now()
        with self._connect() as connection:
            connection.execute("UPDATE research_project_note SET deleted_at=?,updated_at=? WHERE note_id=?", (now, now, note_id))
        self.record_revision(row["project_id"], "DELETE_NOTE", {"note_id": note_id})

    def add_decision(self, project_id: str, content: str, *, decision_type: str = "manual_confirmation", author: str = "user") -> ResearchDecision:
        self._assert_project_writable(project_id)
        decision = ResearchDecision(decision_id=uuid.uuid4().hex, project_id=project_id, content=content, decision_type=decision_type, author=author, created_at=_now(), updated_at=_now())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO research_project_decision VALUES (?,?,?,?,?,?,?,?)",
                (decision.decision_id, decision.project_id, decision.decision_type, decision.content, decision.author, decision.created_at, decision.updated_at, None),
            )
        self.record_revision(project_id, "ADD_DECISION", asdict(decision))
        return decision

    def list_decisions(self, project_id: str, *, include_deleted: bool = False) -> list[ResearchDecision]:
        where = "" if include_deleted else " AND deleted_at IS NULL"
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM research_project_decision WHERE project_id=?{where} ORDER BY updated_at DESC", (project_id,)
            ).fetchall()
        return [ResearchDecision(**dict(row)) for row in rows]

    def update_decision(self, decision_id: str, content: str, *, decision_type: str | None = None, author: str | None = None) -> ResearchDecision:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM research_project_decision WHERE decision_id=?", (decision_id,)).fetchone()
        if not row:
            raise KeyError(f"研究结论不存在：{decision_id}")
        self._assert_project_writable(row["project_id"])
        now = _now()
        values = (decision_type or row["decision_type"], content, author or row["author"], now, decision_id)
        with self._connect() as connection:
            connection.execute("UPDATE research_project_decision SET decision_type=?,content=?,author=?,updated_at=? WHERE decision_id=?", values)
        self.record_revision(row["project_id"], "UPDATE_DECISION", {"decision_id": decision_id, "content": content})
        return next(item for item in self.list_decisions(row["project_id"]) if item.decision_id == decision_id)

    def delete_decision(self, decision_id: str) -> None:
        with self._connect() as connection:
            row = connection.execute("SELECT project_id FROM research_project_decision WHERE decision_id=?", (decision_id,)).fetchone()
        if not row:
            raise KeyError(f"研究结论不存在：{decision_id}")
        self._assert_project_writable(row["project_id"])
        now = _now()
        with self._connect() as connection:
            connection.execute("UPDATE research_project_decision SET deleted_at=?,updated_at=? WHERE decision_id=?", (now, now, decision_id))
        self.record_revision(row["project_id"], "DELETE_DECISION", {"decision_id": decision_id})

    def _assert_project_writable(self, project_id: str) -> None:
        project = self.get_project(project_id)
        if project.status in {ResearchProjectStatus.CLOSED.value, ResearchProjectStatus.ARCHIVED.value}:
            raise ValueError("已关闭或已归档项目为只读")

    def record_revision(self, project_id: str, action: str, payload: Any, *, actor: str = "system") -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO research_project_revision VALUES (?,?,?,?,?,?)",
                (uuid.uuid4().hex, project_id, action, actor, _json(payload), _now()),
            )

    # ---------- research runs and reusable cache ----------

    def create_run(
        self,
        *,
        run_id: str,
        case_id: str,
        code: str,
        cache_key: str,
        project_id: str | None = None,
        parent_run_id: str | None = None,
        version_no: int = 1,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_run
                (run_id,research_case_id,code,status,cache_key,project_id,parent_run_id,version_no,is_frozen,created_at,updated_at,result_path,error)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, case_id, code, "IDENTIFYING", cache_key, project_id, parent_run_id, version_no, 0, _now(), _now(), None, None),
            )

    def find_cached(self, cache_key: str, *, project_id: str | None = None) -> Path | None:
        with self._connect() as connection:
            if project_id:
                row = connection.execute(
                    """SELECT result_path FROM research_run
                    WHERE project_id=? AND cache_key=? AND result_path IS NOT NULL AND deleted_at IS NULL
                    ORDER BY version_no DESC LIMIT 1""", (project_id, cache_key)
                ).fetchone()
            else:
                row = connection.execute(
                    """SELECT result_path FROM research_run
                    WHERE cache_key=? AND result_path IS NOT NULL AND deleted_at IS NULL
                    ORDER BY created_at DESC LIMIT 1""", (cache_key,)
                ).fetchone()
        path = Path(row["result_path"]) if row else None
        return path if path and path.exists() else None

    def latest_project_run(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM research_run WHERE project_id=? AND deleted_at IS NULL ORDER BY version_no DESC LIMIT 1", (project_id,)
            ).fetchone()
        return dict(row) if row else None

    def next_project_version_no(self, project_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version_no), 0) AS max_version FROM research_run WHERE project_id=?",
                (project_id,),
            ).fetchone()
        return int(row["max_version"] or 0) + 1

    def list_project_versions(self, project_id: str, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        with self._connect() as connection:
            where = "" if include_deleted else " AND deleted_at IS NULL"
            rows = connection.execute(
                f"SELECT * FROM research_run WHERE project_id=?{where} ORDER BY version_no DESC", (project_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_run(self, run_id: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
        with self._connect() as connection:
            where = "" if include_deleted else " AND deleted_at IS NULL"
            row = connection.execute(
                f"SELECT * FROM research_run WHERE run_id=?{where}", (run_id,)
            ).fetchone()
        return dict(row) if row else None

    def delete_run(self, run_id: str, *, author: str = "user", reason: str | None = None) -> dict[str, Any]:
        row = self.get_run(run_id)
        if not row:
            raise KeyError(f"研究版本不存在或已删除：{run_id}")
        deleted_at = _now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE research_run SET deleted_at=?,deleted_by=?,delete_reason=?,updated_at=? WHERE run_id=?",
                (deleted_at, author, reason or "", deleted_at, run_id),
            )
        if row.get("project_id"):
            self.record_revision(row["project_id"], "DELETE_RESEARCH_REPORT", {
                "run_id": run_id, "version_no": row.get("version_no"),
                "author": author, "reason": reason or "", "deleted_at": deleted_at,
            })
        row.update({"deleted_at": deleted_at, "deleted_by": author, "delete_reason": reason or ""})
        return row

    def restore_run(self, run_id: str) -> dict[str, Any]:
        row = self.get_run(run_id, include_deleted=True)
        if not row:
            raise KeyError(f"研究版本不存在：{run_id}")
        if not row.get("deleted_at"):
            return row
        restored_at = _now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE research_run SET deleted_at=NULL,deleted_by=NULL,delete_reason=NULL,updated_at=? WHERE run_id=?",
                (restored_at, run_id),
            )
        if row.get("project_id"):
            self.record_revision(row["project_id"], "RESTORE_RESEARCH_REPORT", {
                "run_id": run_id, "version_no": row.get("version_no"), "restored_at": restored_at,
            })
        row.update({"deleted_at": None, "deleted_by": None, "delete_reason": None, "updated_at": restored_at})
        return row

    def set_run_frozen(self, run_id: str, frozen: bool = True) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE research_run SET is_frozen=?,updated_at=? WHERE run_id=?", (int(frozen), _now(), run_id))

    def save_stage(self, run_id: str, stage: Any) -> None:
        payload = asdict(stage) if hasattr(stage, "__dataclass_fields__") else stage
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO research_stage(run_id,payload,created_at) VALUES (?,?,?)",
                (run_id, _json(payload), _now()),
            )

    def save_payload(self, table: str, run_id: str, payload: Any) -> None:
        allowed = {
            "research_source_snapshot", "research_evidence", "research_metric_snapshot",
            "research_benchmark_snapshot", "research_strategy_fit", "research_report",
            "research_followup",
        }
        if table not in allowed:
            raise ValueError(f"不允许写入研究表：{table}")
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO {table}(run_id,payload,created_at) VALUES (?,?,?)",
                (run_id, _json(payload), _now()),
            )

    def save_technical_snapshot(
        self,
        *,
        run_id: str,
        code: str,
        as_of: str | None,
        input_snapshot_id: str | None,
        data_hash: str | None,
        config_hash: str,
        formula_version: str,
        payload: dict[str, Any],
    ) -> str:
        technical_snapshot_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_technical_snapshot
                (technical_snapshot_id,run_id,code,as_of,input_snapshot_id,data_hash,config_hash,formula_version,payload,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    technical_snapshot_id,
                    run_id,
                    code,
                    as_of,
                    input_snapshot_id,
                    data_hash,
                    config_hash,
                    formula_version,
                    _json(payload),
                    _now(),
                ),
            )
        return technical_snapshot_id

    def list_technical_snapshots(self, code: str, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM research_technical_snapshot
                WHERE code=? ORDER BY created_at DESC LIMIT ?""",
                (code, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_result(self, result: dict[str, Any], *, cache_key: str, result_path: Path) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE research_run SET status=?,updated_at=?,result_path=?,error=? WHERE run_id=?",
                (result.get("run_status"), _now(), str(result_path), result.get("error"), result["run_id"]),
            )

    def save_report_draft(self, draft: ResearchReportDraft) -> ResearchReportDraft:
        run = self.get_run(draft.run_id)
        if not run:
            raise KeyError(f"研究版本不存在或已删除：{draft.run_id}")
        now = _now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM research_report_draft WHERE run_id=? AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT 1",
                (draft.run_id,),
            ).fetchone()
            draft.draft_id = existing["draft_id"] if existing else (draft.draft_id or uuid.uuid4().hex)
            draft.project_id = run.get("project_id")
            draft.created_at = existing["created_at"] if existing else (draft.created_at or now)
            draft.updated_at = now
            draft.status = "DRAFT"
            draft.deleted_at = None
            connection.execute(
                """INSERT OR REPLACE INTO research_report_draft
                (draft_id,run_id,project_id,manual_summary,manual_conclusion,risk_judgment,
                 falsification_conditions,followup_plan,author,status,created_at,updated_at,deleted_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
                (draft.draft_id, draft.run_id, draft.project_id, draft.manual_summary,
                 draft.manual_conclusion, draft.risk_judgment, draft.falsification_conditions,
                 draft.followup_plan, draft.author, draft.status, draft.created_at, draft.updated_at),
            )
        if draft.project_id:
            self.record_revision(draft.project_id, "SAVE_RESEARCH_REPORT_DRAFT", {
                "run_id": draft.run_id, "draft_id": draft.draft_id, "author": draft.author,
            })
        return draft

    def get_report_draft(self, run_id: str, *, include_deleted: bool = False) -> ResearchReportDraft | None:
        where = "" if include_deleted else " AND deleted_at IS NULL"
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT * FROM research_report_draft WHERE run_id=?{where} ORDER BY updated_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        return ResearchReportDraft(**dict(row)) if row else None

    def discard_report_draft(self, run_id: str) -> None:
        draft = self.get_report_draft(run_id)
        if not draft:
            return
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE research_report_draft SET deleted_at=?,updated_at=? WHERE draft_id=?",
                (now, now, draft.draft_id),
            )
        if draft.project_id:
            self.record_revision(draft.project_id, "DISCARD_RESEARCH_REPORT_DRAFT", {
                "run_id": run_id, "draft_id": draft.draft_id,
            })

    def save_data_snapshot(
        self,
        *,
        code: str,
        data_type: str,
        source: str,
        as_of: str | None,
        payload: Any,
        request: dict[str, Any] | None = None,
        freshness_days: int = 0,
        official: bool = False,
        conflict: bool = False,
        reused: bool = False,
        fields: list[str] | None = None,
        quality: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        collected_at = _now()
        raw = payload.to_json(orient="records", date_format="iso") if isinstance(payload, pd.DataFrame) else _json(payload)
        cache_key = hashlib.sha256(_json({"code": code, "data_type": data_type, "source": source, "as_of": as_of, "request": request or {}}).encode()).hexdigest()
        snapshot_id = uuid.uuid4().hex
        path = self.root / "cache" / data_type / code / f"{snapshot_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, pd.DataFrame):
            stored = {"kind": "dataframe", "records": json.loads(payload.to_json(orient="records", date_format="iso"))}
        else:
            stored = {"kind": "json", "value": payload}
        self.write_json(path, stored)
        content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        expires_at = None
        if freshness_days > 0:
            expires_at = (datetime.now(timezone.utc) + timedelta(days=freshness_days)).isoformat()
        record = {
            "snapshot_id": snapshot_id, "code": code, "data_type": data_type, "source": source,
            "as_of": as_of, "collected_at": collected_at, "cache_key": cache_key,
            "payload_path": str(path), "freshness_days": freshness_days,
            "official": int(official), "conflict": int(conflict), "reused": int(reused),
            "request": _json(request or {}), "fields": _json(fields or []),
            "quality": _json(quality or {}), "content_hash": content_hash,
        }
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_data_snapshot
                (snapshot_id,code,data_type,source,as_of,collected_at,cache_key,payload_path,freshness_days,official,conflict,reused,request,fields,quality,content_hash)
                VALUES (:snapshot_id,:code,:data_type,:source,:as_of,:collected_at,:cache_key,:payload_path,:freshness_days,:official,:conflict,:reused,:request,:fields,:quality,:content_hash)""", record,
            )
            connection.execute(
                """INSERT OR REPLACE INTO research_cache_index
                (cache_key,code,data_type,source,as_of,collected_at,snapshot_id,payload_path,expires_at,status)
                VALUES (:cache_key,:code,:data_type,:source,:as_of,:collected_at,:snapshot_id,:payload_path,:expires_at,'AVAILABLE')""",
                {**record, "expires_at": expires_at},
            )
        return record

    def list_snapshots(self, code: str, *, data_type: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as connection:
            if data_type:
                rows = connection.execute(
                    "SELECT * FROM research_data_snapshot WHERE code=? AND data_type=? ORDER BY collected_at DESC", (code, data_type)
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM research_data_snapshot WHERE code=? ORDER BY collected_at DESC", (code,)
                ).fetchall()
        return [dict(row) for row in rows]

    def cache_status(self, code: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM research_cache_index WHERE code=? ORDER BY collected_at DESC", (code,)
            ).fetchall()
        now = datetime.now(timezone.utc)
        output = []
        for row in rows:
            item = dict(row)
            expires_at = item.get("expires_at")
            item["status"] = "EXPIRED" if expires_at and datetime.fromisoformat(expires_at) < now else item["status"]
            output.append(item)
        return output

    # ---------- managed local data ----------

    def create_data_update_job(
        self,
        *,
        mode: str,
        datasets: list[str],
        codes: list[str],
        update_policy: str = "incremental",
    ) -> str:
        job_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO data_update_job
                (job_id,mode,datasets,codes,update_policy,status,started_at)
                VALUES (?,?,?,?,?,'RUNNING',?)""",
                (job_id, mode, _json(datasets), _json(codes), update_policy, _now()),
            )
        return job_id

    def finish_data_update_job(
        self,
        job_id: str,
        *,
        status: str,
        rows_added: int = 0,
        rows_updated: int = 0,
        error: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE data_update_job
                SET status=?,rows_added=?,rows_updated=?,finished_at=?,error=?
                WHERE job_id=?""",
                (status, rows_added, rows_updated, _now(), error, job_id),
            )

    def save_data_update_attempt(self, attempt: dict[str, Any]) -> dict[str, Any]:
        payload = dict(attempt)
        payload.setdefault("attempt_id", uuid.uuid4().hex)
        payload.setdefault("success", 0)
        payload.setdefault("request", {})
        payload.setdefault("message", "")
        payload.setdefault("rows_returned", 0)
        payload.setdefault("created_at", _now())
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO data_update_attempt
                (attempt_id,job_id,source,dataset,code,success,request,message,as_of,rows_returned,latency_ms,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    payload["attempt_id"], payload["job_id"], payload["source"], payload["dataset"],
                    payload.get("code"), int(payload.get("success", 0)), _json(payload.get("request") or {}),
                    payload.get("message", ""), payload.get("as_of"), int(payload.get("rows_returned", 0)),
                    payload.get("latency_ms"), payload["created_at"],
                ),
            )
        return payload

    def upsert_market_daily(
        self,
        frame: pd.DataFrame,
        *,
        code: str,
        asset_type: str,
        source: str,
        snapshot_id: str | None = None,
        available_at: str | None = None,
    ) -> dict[str, int]:
        if frame.empty or "trade_date" not in frame.columns or "close" not in frame.columns:
            return {"added": 0, "updated": 0}
        columns = ["open", "high", "low", "close", "pre_close", "vol", "amount"]
        data = frame.copy()
        data["trade_date"] = pd.to_datetime(data["trade_date"], errors="coerce")
        data = data.dropna(subset=["trade_date", "close"]).sort_values("trade_date")
        added = updated = 0
        with self._connect() as connection:
            for record in data.to_dict("records"):
                trade_date = pd.Timestamp(record["trade_date"]).date().isoformat()
                values = {column: record.get(column) for column in columns}
                values = {
                    key: (None if value is None or pd.isna(value) else float(value))
                    for key, value in values.items()
                }
                content_hash = hashlib.sha256(
                    _json({"code": code, "trade_date": trade_date, "source": source, **values}).encode("utf-8")
                ).hexdigest()
                row_id = hashlib.sha256(
                    f"{code}|{trade_date}|{source}|{content_hash}".encode("utf-8")
                ).hexdigest()
                existing = connection.execute(
                    "SELECT row_id FROM data_market_daily WHERE row_id=?", (row_id,)
                ).fetchone()
                if existing:
                    continue
                connection.execute(
                    "UPDATE data_market_daily SET is_latest=0 WHERE code=? AND trade_date=? AND source=?",
                    (code, trade_date, source),
                )
                connection.execute(
                    """INSERT INTO data_market_daily
                    (row_id,code,asset_type,trade_date,open,high,low,close,pre_close,volume,amount,
                     source,snapshot_id,available_at,revision_id,content_hash,is_latest,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row_id, code, asset_type, trade_date, values["open"], values["high"], values["low"],
                        values["close"], values["pre_close"], values["vol"], values["amount"], source,
                        snapshot_id, available_at or trade_date, content_hash, content_hash, 1, _now(),
                    ),
                )
                added += 1
                if connection.execute(
                    "SELECT COUNT(*) FROM data_market_daily WHERE code=? AND trade_date=? AND source=? AND row_id<>?",
                    (code, trade_date, source, row_id),
                ).fetchone()[0]:
                    updated += 1
        return {"added": added, "updated": updated}

    def upsert_asset_metadata(
        self,
        metadata: dict[str, Any],
        *,
        code: str,
        asset_type: str,
        source: str,
        as_of: str | None = None,
        available_at: str | None = None,
        snapshot_id: str | None = None,
    ) -> int:
        count = 0
        with self._connect() as connection:
            for field_name, value in metadata.items():
                if field_name in {"ts_code", "code"}:
                    continue
                serialized = _json(value) if isinstance(value, (dict, list, tuple)) else str(value) if value is not None else None
                revision_id = hashlib.sha256(
                    _json({"code": code, "field": field_name, "value": serialized, "source": source}).encode("utf-8")
                ).hexdigest()
                row_id = hashlib.sha256(
                    f"{code}|{field_name}|{source}|{revision_id}".encode("utf-8")
                ).hexdigest()
                if connection.execute("SELECT 1 FROM data_asset_metadata WHERE row_id=?", (row_id,)).fetchone():
                    continue
                connection.execute(
                    "UPDATE data_asset_metadata SET is_latest=0 WHERE code=? AND field_name=? AND source=?",
                    (code, field_name, source),
                )
                connection.execute(
                    """INSERT INTO data_asset_metadata
                    (row_id,code,asset_type,field_name,field_value,source,as_of,available_at,revision_id,snapshot_id,is_latest,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
                    (row_id, code, asset_type, field_name, serialized, source, as_of, available_at or as_of, revision_id, snapshot_id, _now()),
                )
                count += 1
        return count

    def latest_market_date(self, code: str, *, source: str | None = None) -> str | None:
        clauses = ["code=?", "is_latest=1"]
        values: list[Any] = [code]
        if source:
            clauses.append("source=?")
            values.append(source)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT MAX(trade_date) AS as_of FROM data_market_daily WHERE {' AND '.join(clauses)}", values
            ).fetchone()
        return row["as_of"] if row and row["as_of"] else None

    def query_asset_metadata(self, *, code: str, limit: int = 500) -> pd.DataFrame:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT code,asset_type,field_name,field_value,source,as_of,available_at
                FROM data_asset_metadata WHERE code=? AND is_latest=1
                ORDER BY field_name LIMIT ?""",
                (code, limit),
            ).fetchall()
        return pd.DataFrame([dict(row) for row in rows])

    def query_market_daily(
        self,
        *,
        code: str | None = None,
        asset_type: str | None = None,
        start: str | None = None,
        end: str | None = None,
        as_of_date: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> pd.DataFrame:
        clauses = ["is_latest=1"]
        values: list[Any] = []
        for field, value in (("code", code), ("asset_type", asset_type)):
            if value:
                clauses.append(f"{field}=?")
                values.append(value.upper() if field == "asset_type" else value)
        if start:
            clauses.append("trade_date>=?")
            values.append(start)
        if end:
            clauses.append("trade_date<=?")
            values.append(end)
        if as_of_date:
            clauses.append("(available_at IS NULL OR available_at<=?)")
            values.append(as_of_date)
        query = f"""SELECT code,asset_type,trade_date,open,high,low,close,pre_close,volume,amount,source,available_at
                    FROM data_market_daily WHERE {' AND '.join(clauses)}
                    ORDER BY trade_date,code LIMIT ? OFFSET ?"""
        values.extend([limit, offset])
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return pd.DataFrame([dict(row) for row in rows])

    def list_data_catalog(self, *, dataset: str | None = None, code: str | None = None) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        if dataset in {None, "market"}:
            clauses = ["is_latest=1"]
            values: list[Any] = []
            if code:
                clauses.append("code=?")
                values.append(code)
            with self._connect() as connection:
                rows = connection.execute(
                    f"""SELECT code,asset_type,source,MIN(trade_date) AS start_date,
                    MAX(trade_date) AS end_date,COUNT(*) AS row_count,MAX(created_at) AS updated_at
                    FROM data_market_daily WHERE {' AND '.join(clauses)}
                    GROUP BY code,asset_type,source ORDER BY code""", values,
                ).fetchall()
            output.extend({"dataset": "market", **dict(row)} for row in rows)
        if dataset in {None, "factor"}:
            clauses = []
            values = []
            if code:
                clauses.append("factor_id=?")
                values.append(code)
            with self._connect() as connection:
                rows = connection.execute(
                    f"SELECT * FROM data_factor_snapshot{' WHERE ' + ' AND '.join(clauses) if clauses else ''} ORDER BY created_at DESC",
                    values,
                ).fetchall()
            output.extend({"dataset": "factor", **dict(row)} for row in rows)
        return output

    def save_factor_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        payload = dict(snapshot)
        payload.setdefault("snapshot_id", uuid.uuid4().hex)
        payload.setdefault("created_at", _now())
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO data_factor_snapshot
                (snapshot_id,factor_id,asset_type,horizon,as_of,file_path,manifest_path,row_count,content_hash,formula_hash,value_semantics,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    payload["snapshot_id"], payload["factor_id"], payload["asset_type"], payload["horizon"],
                    payload.get("as_of"), payload["file_path"], payload["manifest_path"], int(payload.get("row_count", 0)),
                    payload.get("content_hash"), payload.get("formula_hash"), payload.get("value_semantics"), payload["created_at"],
                ),
            )
        return payload

    @staticmethod
    def write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    @staticmethod
    def read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))
