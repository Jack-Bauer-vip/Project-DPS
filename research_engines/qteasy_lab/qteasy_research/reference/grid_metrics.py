"""网格指标工具（供 grid_suggestion 锚点/间距建议 与 grid_backtest_eval 回测评估共用）。

- ``atr_pct``: ATR(period) / 最新 close（间距主公式的波幅输入）。
- ``ema_series``: 慢衰减 EMA（动态锚参考）。
- ``adx``: Wilder 平滑 ADX(period)（市场制度判趋势）。
- ``market_regime``: ADX + ATR% 四象限 → (regime, regime_multiplier)。
- ``swing_levels``: 摆动点检测（滚动窗口极值）→ swing_high/low/mid。
- ``fib_levels``: 摆动高低点间的斐波那契回撤位。
- ``weekly_dynamic_anchor``: 周频动态锚（几何均值 + 滞后阈值 h + 单周最大移动 u）。
- ``anchor_stability``: 锚点稳定性评分（变异系数 + 可选滚动波动比）。

计算口径与 ``reference/volatility_cone.py``、``reference/grid_suggestion.py`` 对齐。
机器产出全 ASCII，零中文策略名。
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

# 年化系数（日频），与 volatility_cone._TRADING_DAYS 一致。
_TRADING_DAYS = 252.0

# 市场制度四象限：ADX > 该阈值判「趋势」，否则「震荡」；ATR% >= 该值判「高波」。
ADX_TREND_THRESHOLD = 25.0
ATR_HIGH_THRESHOLD = 0.01  # 1.0%
# 四象限间距乘子（Smart ATR 实证区间 0.7~1.3）。
REGIME_MULTIPLIERS: dict[str, float] = {
    "trending_highvol": 1.2,
    "trending_lowvol": 0.9,
    "choppy_highvol": 1.3,
    "choppy_lowvol": 0.7,
}
# 锚点稳定性等级阈值（score）。
STABILITY_HIGH = 70.0
STABILITY_MEDIUM = 45.0
# 稳定性评分归一到 cv=0.20 时降到 0。
_STABILITY_CV_SATURATION = 0.20


def _clean_series(series: pd.Series | list[float] | np.ndarray | None) -> pd.Series:
    """统一为 float Series（数值化 + dropna）。空/无效 → 空 Series。"""
    if series is None:
        return pd.Series(dtype=float)
    return pd.to_numeric(pd.Series(series), errors="coerce").dropna().astype(float)


def atr_pct(
    close: pd.Series | list[float],
    high: pd.Series | list[float] | None = None,
    low: pd.Series | list[float] | None = None,
    period: int = 20,
) -> float | None:
    """ATR(period) / 最新 close（价格比例）。high/low 缺失退化 |close-prev_close|。

    ``TR = max(high-low, |high-prev_close|, |low-prev_close|)``；与旧
    ``grid_suggestion._atr_pct`` 同口径（迁移）。历史不足 / 最新 close 非法 → None。
    """
    close = _clean_series(close)
    if close.empty or len(close) < period + 1:
        return None
    latest_close = float(close.iloc[-1])
    if latest_close <= 0:
        return None
    if high is not None and low is not None:
        high = _clean_series(high)
        low = _clean_series(low)
        n = min(len(close), len(high), len(low))
        if n < period + 1:
            return None
        close, high, low = close.iloc[-n:], high.iloc[-n:], low.iloc[-n:]
        prev_close = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
    else:
        tr = (close - close.shift(1)).abs()
    tr = tr.dropna()
    if len(tr) < period:
        return None
    atr = float(tr.tail(period).mean())
    value = atr / latest_close
    return round(value, 6) if np.isfinite(value) else None


def ema_series(
    close: pd.Series | list[float],
    span: int = 120,
) -> pd.Series:
    """慢衰减 EMA（span 大 → 衰减慢）；空输入 → 空 Series。"""
    close = _clean_series(close)
    if close.empty:
        return pd.Series(dtype=float)
    return close.ewm(span=max(int(span), 2), adjust=False).mean()


def adx(
    high: pd.Series | list[float],
    low: pd.Series | list[float],
    close: pd.Series | list[float],
    period: int = 14,
) -> float | None:
    """Wilder 平滑 ADX(period)，返回最新值；历史不足 period×2 → None。

    - TR = max(high-low, |high-prev_close|, |low-prev_close|)
    - +DM = up_move 大于 down_move 且 >0 时记 up_move，否则 0；-DM 对称。
    - Wilder 平滑 = ewm(alpha=1/period)；DI± = 100×平滑 DM / 平滑 TR。
    - DX = 100×|DI+−DI−|/(DI++DI−)；ADX = Wilder 平滑 DX。
    """
    high = _clean_series(high)
    low = _clean_series(low)
    close = _clean_series(close)
    n = min(len(high), len(low), len(close))
    if n < period * 2:
        return None
    high, low, close = high.iloc[-n:], low.iloc[-n:], close.iloc[-n:]
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    up_move = high - prev_high
    down_move = prev_low - low
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=high.index,
    )
    alpha = 1.0 / max(int(period), 1)
    atr_s = tr.ewm(alpha=alpha, adjust=False).mean()
    tr_denom = atr_s.replace(0.0, np.nan)
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / tr_denom
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / tr_denom
    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx_s = dx.ewm(alpha=alpha, adjust=False).mean()
    latest = adx_s.iloc[-1]
    return float(latest) if np.isfinite(latest) else None


def market_regime(
    adx_value: float | None,
    atr_pct_value: float | None,
) -> tuple[str, float | None]:
    """ADX + ATR% 四象限 → (regime, regime_multiplier)；输入缺失 → ("unknown", None)。"""
    if adx_value is None or atr_pct_value is None or not np.isfinite(adx_value) or not np.isfinite(atr_pct_value):
        return "unknown", None
    trending = adx_value > ADX_TREND_THRESHOLD
    high_vol = atr_pct_value >= ATR_HIGH_THRESHOLD
    if trending and high_vol:
        regime = "trending_highvol"
    elif trending:
        regime = "trending_lowvol"
    elif high_vol:
        regime = "choppy_highvol"
    else:
        regime = "choppy_lowvol"
    return regime, REGIME_MULTIPLIERS[regime]


def swing_levels(
    high: pd.Series | list[float],
    low: pd.Series | list[float],
    window: int = 200,
) -> tuple[float | None, float | None, float | None]:
    """摆动点检测：最近 window 根 K 线内高点极大 / 低点极小 → (swing_high, swing_low, swing_mid)。

    简化滚动窗口极值（参考锚用，不作趋势确认）；历史不足 → (None, None, None)。
    """
    high = _clean_series(high)
    low = _clean_series(low)
    n = min(len(high), len(low))
    if n < max(2, int(window)):
        return None, None, None
    lookback = max(2, int(window))
    window_high = float(high.iloc[-lookback:].max())
    window_low = float(low.iloc[-lookback:].min())
    if not (np.isfinite(window_high) and np.isfinite(window_low)):
        return None, None, None
    swing_mid = round((window_high + window_low) / 2.0, 6)
    return round(window_high, 6), round(window_low, 6), swing_mid


def fib_levels(
    swing_low: float | None,
    swing_high: float | None,
) -> list[dict[str, Any]]:
    """摆动高低点间斐波那契回撤位（0.236/0.382/0.5/0.618/0.786）。无效输入 → []。"""
    if swing_low is None or swing_high is None:
        return []
    if not (np.isfinite(swing_low) and np.isfinite(swing_high)) or swing_high <= swing_low:
        return []
    span = swing_high - swing_low
    return [
        {"ratio": ratio, "level": round(swing_low + ratio * span, 6)}
        for ratio in (0.236, 0.382, 0.5, 0.618, 0.786)
    ]


def weekly_dynamic_anchor(
    closes: pd.Series | list[float],
    prev_anchor: float | None = None,
    weights: list[float] | None = None,
    h: float = 0.0,
    u: float | None = None,
) -> dict[str, Any]:
    """周频动态锚（Codex 研究方案）：几何均值 + 滞后阈值 h + 单周最大移动 u。

    - raw = exp(Σ w_i·ln(C_i))（几何均值，适合相对价格网格；weights 缺省等权）。
    - prev_anchor 为 None → 仅返回 raw（无历史，不施加滞后/上限）。
    - h > 0：|raw/prev_anchor − 1| < h → anchor 保持 prev_anchor（死区，防噪音小移动）。
    - u 非 None 且 >0：M = prev_anchor·exp(clip(ln(raw/prev_anchor), −u, +u))（防追涨杀跌）。

    返回 ``{"anchor", "raw", "drift", "moved"}``；输入空/非法 → anchor=None。
    """
    closes = _clean_series(closes)
    if closes.empty or (closes <= 0).any():
        return {"anchor": None, "raw": None, "drift": None, "moved": None}
    logs = np.log(closes)
    if weights:
        w = np.asarray(weights, dtype=float)
        if len(w) != len(logs):
            w = np.ones(len(logs)) / len(logs)
        elif w.sum() > 0:
            w = w / w.sum()
        raw = float(np.exp(np.dot(w, logs)))
    else:
        raw = float(np.exp(np.mean(logs)))
    if prev_anchor is None or not np.isfinite(prev_anchor) or prev_anchor <= 0:
        return {
            "anchor": round(raw, 6),
            "raw": round(raw, 6),
            "drift": None,
            "moved": False,
        }
    drift = raw / prev_anchor - 1.0
    if h and abs(drift) < h:
        return {
            "anchor": round(float(prev_anchor), 6),
            "raw": round(raw, 6),
            "drift": round(drift, 6),
            "moved": False,
        }
    if u is not None and u > 0:
        log_m = math.log(raw / prev_anchor)
        anchor = prev_anchor * math.exp(max(-u, min(u, log_m)))
    else:
        anchor = raw
    return {
        "anchor": round(float(anchor), 6),
        "raw": round(raw, 6),
        "drift": round(drift, 6),
        "moved": True,
    }


def anchor_stability(
    values: list[float | None],
    rolling_vol_ratio: float | None = None,
) -> dict[str, Any]:
    """锚点稳定性评分：变异系数 + 可选锚点滚动波动惩罚。

    - score = 100 × (1 − min(cv / 0.20, 1))；cv = std/mean（各窗口锚点离散度）。
    - rolling_vol_ratio > 1（主锚滚动波动高于价格波动）视为锚漂移，按
      ``score × max(0, 2 − ratio)`` 惩罚。
    - 返回 ``{"score", "grade", "cv", "basis"}``；有效锚点不足 2 个 → score=None。
    """
    valid = [v for v in values if isinstance(v, (int, float)) and np.isfinite(v)]
    if len(valid) < 2:
        return {"score": None, "grade": "unknown", "cv": None, "basis": "insufficient"}
    mean = float(np.mean(valid))
    if mean <= 0:
        return {"score": None, "grade": "unknown", "cv": None, "basis": "non_positive"}
    cv = float(np.std(valid) / mean)
    score = 100.0 * (1.0 - min(cv / _STABILITY_CV_SATURATION, 1.0))
    basis = "cv"
    if rolling_vol_ratio is not None and np.isfinite(rolling_vol_ratio) and rolling_vol_ratio > 1.0:
        score *= max(0.0, 2.0 - rolling_vol_ratio)
        basis += ", rolling_vol"
    if score >= STABILITY_HIGH:
        grade = "high"
    elif score >= STABILITY_MEDIUM:
        grade = "medium"
    else:
        grade = "low"
    return {
        "score": round(score, 2),
        "grade": grade,
        "cv": round(cv, 6),
        "basis": basis,
    }


__all__ = [
    "ADX_TREND_THRESHOLD",
    "ATR_HIGH_THRESHOLD",
    "REGIME_MULTIPLIERS",
    "STABILITY_HIGH",
    "STABILITY_MEDIUM",
    "atr_pct",
    "ema_series",
    "adx",
    "market_regime",
    "swing_levels",
    "fib_levels",
    "weekly_dynamic_anchor",
    "anchor_stability",
]
