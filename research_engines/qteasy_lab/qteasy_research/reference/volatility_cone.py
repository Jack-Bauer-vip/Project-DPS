"""波动率锥与网格参考间距建议。

波动率锥输出各窗口年化波动率的历史分位分布；``suggest_reference_spread``
把当前年化波动率映射为价格比例的参考间距（供系统A网格参考，人工使用）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qteasy_research.reference.config import CONE_PERCENTILES, VOLATILITY_WINDOWS

# 年化系数（日频）。
_TRADING_DAYS = 252.0

# vol_rank 分档乘子：<0.2→0.8, <0.4→1.0, <0.6→1.15, <0.8→1.35, 其余→1.6。
_SPREAD_MULTIPLIERS: tuple[tuple[float, float], ...] = (
    (0.2, 0.8),
    (0.4, 1.0),
    (0.6, 1.15),
    (0.8, 1.35),
    (float("inf"), 1.6),
)


def _rolling_vol(returns: pd.Series, window: int) -> pd.Series:
    min_periods = max(window // 2, 2)
    return returns.rolling(window, min_periods=min_periods).std() * np.sqrt(_TRADING_DAYS)


def build_volatility_cone(
    returns: pd.Series,
    windows: tuple[int, ...] = VOLATILITY_WINDOWS,
    percentiles: tuple[int, ...] = CONE_PERCENTILES,
) -> pd.DataFrame:
    """返回 long 表：``window_days, percentile, value``（年化波动率）。"""
    rows: list[dict] = []
    for window in windows:
        vol = _rolling_vol(returns, window).dropna()
        if vol.empty:
            continue
        for pctile in percentiles:
            rows.append({
                "window_days": window,
                "percentile": pctile,
                "value": float(vol.quantile(pctile / 100.0)),
            })
    return pd.DataFrame(rows, columns=["window_days", "percentile", "value"])


def current_vol_rank(returns: pd.Series, window: int) -> float | None:
    """当前滚动波动率在整个历史分布中的百分位（0~1）。"""
    vol = _rolling_vol(returns, window).dropna()
    if vol.empty:
        return None
    current = vol.iloc[-1]
    return float((vol <= current).mean())


def suggest_reference_spread(
    current_vol_annualized: float | None,
    vol_rank: float | None,
    window: int = 60,
) -> float | None:
    """参考间距（价格比例）：``daily_vol × 3.0``，按 vol_rank 分档乘子。

    - ``daily_vol = current_vol_annualized / sqrt(252)``
    - 基础间距 ``= daily_vol × 3.0``
    - vol_rank 乘子：<0.2→0.8 / <0.4→1.0 / <0.6→1.15 / <0.8→1.35 / 其余→1.6
    - 返回值如 0.012 表示 1.2%；输入无效时返回 None。
    """
    if (
        current_vol_annualized is None
        or not np.isfinite(current_vol_annualized)
        or current_vol_annualized <= 0
    ):
        return None
    daily_vol = current_vol_annualized / np.sqrt(_TRADING_DAYS)
    base = daily_vol * 3.0
    rank = vol_rank if (vol_rank is not None and np.isfinite(vol_rank)) else 0.5
    multiplier = 1.0
    for threshold, mult in _SPREAD_MULTIPLIERS:
        if rank < threshold:
            multiplier = mult
            break
    return round(float(base * multiplier), 4)
