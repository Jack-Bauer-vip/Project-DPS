"""系统A active 资产池读取与本地行情对齐。

- ``read_active_assets``：动态读 A ``config/asset_pool.csv`` 的 active 行，
  不硬编码数量（以文件为准）。
- ``align_pool_price_history``：本地 ``fund_daily.csv`` 优先，缺失走
  ``CompositeProvider`` 在线补齐；返回 ``{asset_id: DataFrame}``。
- ``report_pool_gaps``：缺失资产标记 ``quality_level="D"`` + warning，**不虚构**。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from qteasy_research.pretrade.providers import (
    CompositeProvider,
    LocalCsvProvider,
    SqliteProvider,
    TushareProvider,
    AkshareProvider,
)
from qteasy_research.pretrade.symbols import resolve_identity
from qteasy_research.reference.asset_names import resolve_display_name
from qteasy_research.reference.config import SYSTEM_A_ASSET_POOL, SYSTEM_B_DATA_ROOT


def read_active_assets(path: str | Path | None = None) -> pd.DataFrame:
    """读 A ``asset_pool.csv``，返回 status=="active" 的行。

    返回列：``asset_id, code, name, type, exchange, theme``。

    ``name`` 列做 B 侧展示名规范化：A 侧 name 存在截断/错字，按 ``asset_id``
    用 ``asset_names.resolve_display_name`` 覆盖为完整正确展示名（不修改 A 配置）。
    """
    csv_path = Path(path) if path else SYSTEM_A_ASSET_POOL
    if not csv_path.exists():
        raise FileNotFoundError(f"系统A资产池不存在：{csv_path}")
    frame = pd.read_csv(csv_path)
    required = {"asset_id", "code", "status"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"asset_pool.csv 缺少字段：{sorted(missing)}")
    active = frame.loc[frame["status"].astype(str).str.strip().str.lower() == "active"].copy()
    columns = ["asset_id", "code", "name", "type", "exchange", "theme"]
    for column in columns:
        if column not in active.columns:
            active[column] = None
    active["name"] = [
        resolve_display_name(str(asset_id).strip(), name)
        for asset_id, name in zip(active["asset_id"], active["name"])
    ]
    return active[columns].reset_index(drop=True)


def _build_composite_provider(data_dir: Path) -> CompositeProvider:
    """构造本地优先的行情 provider 链（Local → SQLite → Tushare → AKShare）。"""
    local = LocalCsvProvider(data_dir)
    providers = [local]
    if (data_dir / "research.sqlite3").exists() or (data_dir.parent / "research_store").exists():
        try:
            providers.append(SqliteProvider(data_dir.parent / "research_store"))
        except Exception:
            pass
    if _tushare_available():
        providers.append(TushareProvider())
    providers.append(AkshareProvider())
    return CompositeProvider(providers)


def _tushare_available() -> bool:
    import os

    return bool(os.getenv("TUSHARE_TOKEN", "").strip())


def align_pool_price_history(
    assets: pd.DataFrame,
    *,
    data_dir: str | Path | None = None,
    online_ok: bool = True,
) -> dict[str, pd.DataFrame]:
    """对 active 资产取行情，返回 ``{asset_id: DataFrame(trade_date, close, vol, amount)}``。

    - 本地 ``fund_daily.csv`` 优先；缺失且 ``online_ok`` 时走在线补齐。
    - 返回帧统一为 ``trade_date, close, vol, amount`` 列（升序、去重）。
    """
    data_root = Path(data_dir) if data_dir else SYSTEM_B_DATA_ROOT
    local = LocalCsvProvider(data_root)
    # 本地 fund_daily 全量预载，避免逐标的重复读盘。
    local_frame = _read_local_daily(data_root)

    result: dict[str, pd.DataFrame] = {}
    for _, row in assets.iterrows():
        asset_id = str(row["asset_id"]).strip()
        frame = _frame_for_code(local_frame, asset_id)
        source = "local"
        if frame.empty and online_ok:
            frame = _online_price(local, asset_id)
            source = "online"
        result[asset_id] = _normalize_price_frame(frame, source)
    return result


def _read_local_daily(data_dir: Path) -> pd.DataFrame:
    path = data_dir / "fund_daily.csv"
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path)
    return frame.rename(columns={"volume": "vol"}) if "volume" in frame.columns else frame


def _frame_for_code(frame: pd.DataFrame, asset_id: str) -> pd.DataFrame:
    if frame.empty or "ts_code" not in frame.columns:
        return pd.DataFrame()
    return frame.loc[frame["ts_code"].astype(str).str.upper() == asset_id.upper()].copy()


def _online_price(local: LocalCsvProvider, asset_id: str) -> pd.DataFrame:
    """在线补齐单个标的行情（失败返回空帧）。"""
    try:
        identity = resolve_identity(asset_id, fund_metadata=local._fund_metadata())
        result = local.get_price_history(identity)
        if result.ok:
            return result.data
    except Exception:
        pass
    return pd.DataFrame()


def _normalize_price_frame(frame: pd.DataFrame, source: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["trade_date", "close", "vol", "amount"])
    cleaned = frame.copy()
    if "trade_date" in cleaned.columns:
        cleaned["trade_date"] = pd.to_datetime(cleaned["trade_date"], errors="coerce")
    if "close" in cleaned.columns:
        cleaned["close"] = pd.to_numeric(cleaned["close"], errors="coerce")
    for column in ("vol", "amount"):
        if column not in cleaned.columns:
            cleaned[column] = None
        else:
            cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")
    cleaned = cleaned.dropna(subset=["trade_date", "close"])
    cleaned = cleaned.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    cleaned = cleaned[cleaned["close"] > 0]
    if source == "online":
        cleaned["source"] = "online"
    else:
        cleaned["source"] = "local"
    return cleaned[["trade_date", "close", "vol", "amount", "source"]]


def report_pool_gaps(aligned: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    """汇总缺失资产（空行情帧）为 quality_level="D" 条目，不虚构数据。"""
    gaps: list[dict[str, Any]] = []
    for asset_id, frame in aligned.items():
        if frame.empty:
            gaps.append({
                "asset_id": asset_id,
                "quality_level": "D",
                "available": False,
                "warning": f"{asset_id} 行情缺失，未虚构数据",
            })
        else:
            gaps.append({
                "asset_id": asset_id,
                "quality_level": "A" if frame["source"].iloc[-1] == "local" else "B",
                "available": True,
                "warning": None,
            })
    return gaps
