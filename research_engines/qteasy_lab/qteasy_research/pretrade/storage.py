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

    @staticmethod
    def write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    @staticmethod
    def read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))
