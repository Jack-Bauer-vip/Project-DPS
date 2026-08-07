"""网格参考表（B1-1）：逐资产输出当前波动率、波动率锥分位与建议网格间距。

每资产一行宽表，供系统A人工设定网格参考间距（价格比例，如 0.012 = 1.2%）。
所有字段 ``approval_policy="REFERENCE_ONLY"``：仅供人工参考，永不自动 APPROVED。

**降级策略**：历史不足的窗口显式写 ``""``（空字符串）并 ``confidence="low"``，
不让系统A读到 NaN 误判为"无约束"；``suggested_reference_spread`` 使用
``current_vol_{60d}`` 与 ``vol_rank_{60d}``（``volatility_cone.suggest_reference_spread``
已支持 NaN 输入返回 None → 本模块显式写 ``""``）。
机器输出零中文策略名。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from qteasy_research.pretrade.metrics import analyze_price_history
from qteasy_research.reference.config import CONE_PERCENTILES, VOLATILITY_WINDOWS
from qteasy_research.reference.volatility_cone import (
    build_volatility_cone,
    current_vol_rank,
    suggest_reference_spread,
)

_WINDOWS = VOLATILITY_WINDOWS
# 锥分位只输出 p5/p50/p95 三个代表分位。
_CONE_COLS: tuple[int, ...] = (5, 50, 95)
# 缺失值显式写空字符串，避免系统A读到 NaN。
_EMPTY = ""


def _asset_returns(frame: pd.DataFrame) -> pd.Series:
    """日收益序列（丢弃无穷值）。"""
    return frame["close"].pct_change().replace([np.inf, -np.inf], np.nan).dropna()


def _quality_of(frame: pd.DataFrame) -> str:
    """数据质量等级：本地→A，在线补齐→B，空帧→D（与 report_pool_gaps 对齐）。"""
    if frame.empty:
        return "D"
    if "source" in frame.columns and frame["source"].iloc[-1] == "online":
        return "B"
    return "A"


def _cone_columns(returns: pd.Series, window: int) -> dict[str, float | None]:
    """单窗口波动率锥分位列：{f"cone_{window}_p5": …, p50, p95}，缺失返回空 dict。"""
    cone = build_volatility_cone(returns, windows=(window,), percentiles=CONE_PERCENTILES)
    subset = cone.loc[cone["window_days"] == window]
    if subset.empty:
        return {}
    by_p = {int(p): float(value) for p, value in zip(subset["percentile"], subset["value"])}
    return {f"cone_{window}_p{pctile}": by_p.get(pctile) for pctile in _CONE_COLS}


def build_grid_reference(
    assets: pd.DataFrame,
    aligned: dict[str, pd.DataFrame],
    bench_frames: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """构建网格参考表（每资产一行宽表）。

    参数：
        assets: 资产池 DataFrame（含 asset_id/name），来自 ``read_active_assets``。
        aligned: ``{asset_id: DataFrame(trade_date, close, …)}`` 对齐行情。
        bench_frames: 预留基准行情参数（当前网格参考不消费基准，保持签名可扩展）。

    返回列：
        ``asset_id``、``current_vol_{20,60,120,252}d``、``vol_rank_{20,60,120,252}d``、
        ``cone_{20,60,120,252}_p5/p50/p95``、``suggested_reference_spread``、
        ``confidence``、``data_quality``、``warnings``。
    """
    rows: list[dict[str, Any]] = []
    for _, row in assets.iterrows():
        asset_id = str(row["asset_id"]).strip()
        frame = aligned.get(asset_id, pd.DataFrame())
        rows.append(_grid_row(asset_id, frame))
    columns = [
        "asset_id",
        *[f"current_vol_{window}d" for window in _WINDOWS],
        *[f"vol_rank_{window}d" for window in _WINDOWS],
        *[f"cone_{window}_p{pctile}" for window in _WINDOWS for pctile in _CONE_COLS],
        "suggested_reference_spread",
        "confidence",
        "data_quality",
        "warnings",
    ]
    return pd.DataFrame(rows, columns=columns)


def _grid_row(asset_id: str, frame: pd.DataFrame) -> dict[str, Any]:
    """单资产行：默认全空 + low，逐窗口填充可用值。"""
    base: dict[str, Any] = {
        "asset_id": asset_id,
        "confidence": "low",
        "data_quality": _quality_of(frame),
        "warnings": "",
    }
    for window in _WINDOWS:
        base[f"current_vol_{window}d"] = _EMPTY
        base[f"vol_rank_{window}d"] = _EMPTY
    for window in _WINDOWS:
        for pctile in _CONE_COLS:
            base[f"cone_{window}_p{pctile}"] = _EMPTY
    base["suggested_reference_spread"] = _EMPTY

    if frame.empty:
        base["warnings"] = f"{asset_id} 行情缺失，未虚构数据"
        return base

    metrics = analyze_price_history(frame)
    returns = _asset_returns(frame)
    warnings: list[str] = []
    # 60d 窗口值用于建议间距；其余窗口独立降级。
    vol_60: float | None = None
    rank_60: float | None = None

    for window in _WINDOWS:
        vol = metrics["rolling"].get(f"volatility_{window}d")
        if vol is None or not np.isfinite(vol):
            warnings.append(f"历史不足 {window}d")
            continue
        base[f"current_vol_{window}d"] = round(float(vol), 4)
        rank = current_vol_rank(returns, window)
        base[f"vol_rank_{window}d"] = "" if rank is None else round(float(rank), 4)
        for key, value in _cone_columns(returns, window).items():
            if value is not None:
                base[key] = round(float(value), 4)
        if window == 60:
            vol_60 = float(vol)
            rank_60 = rank

    spread = suggest_reference_spread(vol_60, rank_60)
    base["suggested_reference_spread"] = "" if spread is None else spread
    base["confidence"] = "high" if not warnings else "low"
    base["warnings"] = "; ".join(warnings)
    return base
