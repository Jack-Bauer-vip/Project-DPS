"""月度宏观场景表：逐月末识别宏观状态三元组。

复用 ``GlobalEtfEngine._macro_state`` 的 point-in-time 逻辑（``available_at``
点内过滤），对每月末打场景标签。失败月份标记 ``macro_unavailable``，
**不自动中性化**（延续"不把缺失当作中性"的安全语义）。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd

from qteasy_research.core.global_etf_engine import GlobalEtfEngine

_MACRO_SERIES = ("DGS30", "DGS10", "DGS2", "DFII10")


def _month_ends(data_root: Path) -> list[pd.Timestamp]:
    """从宏观序列取所有月末观测日（升序去重）。"""
    frames: list[pd.Series] = []
    for series_id in _MACRO_SERIES:
        path = data_root / "processed" / "global_macro" / f"{series_id}.csv"
        if not path.exists():
            continue
        frame = pd.read_csv(path)
        if "observation_date" not in frame.columns:
            continue
        dates = pd.to_datetime(frame["observation_date"], errors="coerce").dropna()
        if dates.empty:
            continue
        frames.append(dates)
    if not frames:
        return []
    # frames 是 Series 列表（索引为行号而非日期）；转 DatetimeIndex 后
    # resample 是 Series/DataFrame 的方法，需再 to_series()。
    all_dates = pd.DatetimeIndex(pd.concat(frames)).sort_values()
    month_ends = all_dates.to_series().resample("ME").last().index
    return [pd.Timestamp(value) for value in month_ends]


def build_monthly_scenario_table(data_root: str | Path) -> pd.DataFrame:
    """逐月末构造宏观场景表。

    返回 DataFrame 列：``month, states(list), rate_proxy, macro_unavailable,
    rate_up/rate_down/rate_stable, curve_inverted/curve_normal,
    real_yield_up/real_yield_down/real_yield_stable``（布尔）。
    """
    root = Path(data_root)
    scenarios: list[dict] = []
    # GlobalEtfEngine 不允许 data_root 下存在 factor_values；B 的 data 目录符合。
    with tempfile.TemporaryDirectory() as td:
        engine = GlobalEtfEngine(root, td)
        for month_end in _month_ends(root):
            record: dict = {"month": month_end.date().isoformat()}
            try:
                states, rate_proxy, _, _ = engine._macro_state(month_end)
                record.update({
                    "states": states,
                    "rate_proxy": rate_proxy,
                    "macro_unavailable": False,
                })
                for state in _all_states():
                    record[state] = state in states
            except Exception as exc:
                record.update({
                    "states": [],
                    "rate_proxy": None,
                    "macro_unavailable": True,
                    "macro_error": f"{type(exc).__name__}: {exc}",
                })
                for state in _all_states():
                    record[state] = False
            scenarios.append(record)
    if not scenarios:
        return pd.DataFrame(columns=["month", "states", "rate_proxy", "macro_unavailable"])
    return pd.DataFrame(scenarios)


def _all_states() -> tuple[str, ...]:
    return (
        "rate_up", "rate_down", "rate_stable",
        "curve_inverted", "curve_normal",
        "real_yield_up", "real_yield_down", "real_yield_stable",
    )


def scenario_monthly_returns(
    asset_frame: pd.DataFrame,
    scenario_table: pd.DataFrame,
) -> pd.DataFrame:
    """把资产月度收益与宏观场景对齐（month 取月末）。

    ``asset_frame`` 需含 ``observation_date``（或 ``trade_date``）与 ``value``（或 ``close``）。
    返回 ``month, asset_return, states`` 行。
    """
    if asset_frame.empty or scenario_table.empty:
        return pd.DataFrame(columns=["month", "asset_return", "states"])
    date_col = "observation_date" if "observation_date" in asset_frame.columns else "trade_date"
    value_col = "value" if "value" in asset_frame.columns else "close"
    frame = asset_frame.copy()
    frame[date_col] = pd.to_datetime(frame[date_col], errors="coerce")
    frame[value_col] = pd.to_numeric(frame[value_col], errors="coerce")
    frame = frame.dropna(subset=[date_col, value_col])
    frame = frame.set_index(date_col)[value_col].resample("ME").last().pct_change().dropna()
    frame = frame.rename("asset_return").reset_index()
    # reset_index 后日期列名仍为 date_col（observation_date/trade_date），此处构造 month 键。
    frame["month"] = pd.to_datetime(frame[date_col]).dt.strftime("%Y-%m-%d")
    merged = frame.merge(scenario_table, on="month", how="left")
    return merged[["month", "asset_return", "states"]]
