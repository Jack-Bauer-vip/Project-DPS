"""Small desktop-facing helpers for the standalone factor research page."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from qteasy_research.pretrade.factor_scoring import formula_md5
from qteasy_research.pretrade.storage import ResearchStore


DEFAULT_FACTOR_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "factor_id": "momentum_60d",
        "name": "60日动量",
        "category": "momentum",
        "hypothesis": "过去一段时间相对强势的资产，短中期可能延续相对表现；需要关注拥挤和回撤风险。",
        "formula": "close.pct_change(60)",
        "direction": 1,
        "default_horizon": "medium",
        "horizons": ["short", "medium", "long"],
        "supported_asset_types": ["STOCK", "ETF"],
        "value_scope": "asset",
        "missing_policy": "exclude",
        "status": "ACTIVE",
    },
    {
        "factor_id": "momentum_120d",
        "name": "120日动量",
        "category": "momentum",
        "hypothesis": "过去约六个月相对强势的资产，中期可能延续相对表现；需要结合回撤、波动和交易成本确认。",
        "formula": "close.pct_change(120)",
        "direction": 1,
        "default_horizon": "medium",
        "horizons": ["medium", "long"],
        "supported_asset_types": ["STOCK", "ETF"],
        "value_scope": "asset",
        "missing_policy": "exclude",
        "status": "ACTIVE",
    },
    {
        "factor_id": "liquidity_turnover",
        "name": "20日流动性",
        "category": "liquidity",
        "hypothesis": "成交额较高且稳定的资产通常更容易执行，交易冲击成本相对较低；该因子不代表收益率预测。",
        "formula": "log1p(amount).rolling(20).mean()",
        "direction": 1,
        "default_horizon": "medium",
        "horizons": ["short", "medium", "long"],
        "supported_asset_types": ["STOCK", "ETF"],
        "value_scope": "asset",
        "missing_policy": "exclude",
        "status": "ACTIVE",
    },
    {
        "factor_id": "low_volatility_20d",
        "name": "20日低波动",
        "category": "low_volatility",
        "hypothesis": "在可比资产中，较低的短期波动通常对应更低的风险压力，但不代表绝对收益更高。",
        "formula": "-close.pct_change().rolling(20).std()",
        "direction": 1,
        "default_horizon": "medium",
        "horizons": ["short", "medium", "long"],
        "supported_asset_types": ["STOCK", "ETF"],
        "value_scope": "asset",
        "missing_policy": "exclude",
        "status": "ACTIVE",
    },
    {
        "factor_id": "pb_value",
        "name": "PB价值",
        "category": "value",
        "hypothesis": "在同类资产中，较低市净率可能提供价值暴露；必须结合行业和基本面质量判断。",
        "formula": "-pb",
        "direction": 1,
        "default_horizon": "medium",
        "horizons": ["medium", "long"],
        "supported_asset_types": ["STOCK"],
        "value_scope": "asset",
        "missing_policy": "exclude",
        "status": "ACTIVE",
    },
)


def initialize_default_factor_definitions(store_root: str | Path) -> list[dict[str, Any]]:
    """Register missing starter factors without enabling any activation profile."""

    store = ResearchStore(store_root)
    existing = {item["factor_id"] for item in store.list_factor_definitions()}
    created: list[dict[str, Any]] = []
    for definition in DEFAULT_FACTOR_DEFINITIONS:
        if definition["factor_id"] in existing:
            continue
        payload = dict(definition)
        payload["formula_hash"] = formula_md5(payload["formula"])
        created.append(store.upsert_factor_definition(payload))
    return created


def get_factor_data_status(store_root: str | Path, factor_id: str) -> dict[str, Any]:
    root = Path(store_root)
    factor_dir = root / "data" / "factor_values"
    if not factor_dir.exists():
        factor_dir = root / "factor_values"
    parquet_path = factor_dir / f"{factor_id}.parquet"
    manifest_path = factor_dir / f"{factor_id}.manifest.json"
    status: dict[str, Any] = {
        "factor_id": factor_id,
        "parquet_path": str(parquet_path),
        "manifest_path": str(manifest_path),
        "parquet_status": "missing",
        "manifest_status": "missing",
        "rows": 0,
        "as_of": None,
        "value_semantics": None,
        "error": None,
    }
    if not parquet_path.exists():
        return status
    try:
        frame = pd.read_parquet(parquet_path, columns=["date", "asset_code", "value", "available_at"])
        status["parquet_status"] = "available"
        status["rows"] = int(len(frame))
        dates = pd.to_datetime(frame["date"], errors="coerce").dropna()
        status["as_of"] = dates.max().date().isoformat() if not dates.empty else None
    except Exception as exc:
        status["parquet_status"] = "invalid"
        status["error"] = f"{type(exc).__name__}: {exc}"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            status["manifest_status"] = "valid" if manifest.get("value_semantics") in {"neutralized_exposure", "asset_exposure"} else "invalid"
            status["value_semantics"] = manifest.get("value_semantics")
        except Exception as exc:
            status["manifest_status"] = "invalid"
            status["error"] = f"{type(exc).__name__}: {exc}"
    return status


__all__ = [
    "DEFAULT_FACTOR_DEFINITIONS",
    "initialize_default_factor_definitions",
    "get_factor_data_status",
]
