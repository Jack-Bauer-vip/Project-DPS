"""参数网格扫描（阶段三）：间距 → 换手/成本权衡曲线（离线研究工具）。

对给定价格序列，按网格间距 ``d = base_spread × multiplier`` 回放模拟：
起始价 ``p0`` 定网格线 ``p0×(1+d)^k``，价格每跨越一次相邻网格线记 1 次触发
（买或卖），输出年化触发次数与估计成本，供系统A侧网格参数人工研究。

**不进每日决策包**（独立 CLI 调用，只写 B 本地 ``reports/``，A 侧无对应消费端）。

**成本模型**独立镜像 ``pretrade.schemas.TransactionCostConfig`` 数值（不反向
import pretrade）：单次 round-trip bps = ``2×(commission+half_spread) + stamp_tax
+ impact``，默认按 ETF 免印花税（``etf_stamp_tax=False``，与 pretrade 默认一致）。

**降级链**：空序列 → 空列表；历史 < ``min_history``（默认 20 天）或无法推导
基准间距 → 单行 ``spread/trigger/cost 全 None + confidence="low"``（不虚构扫描结果）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qteasy_research.reference.volatility_cone import (
    current_vol_rank,
    suggest_reference_spread,
)

# 年化系数（日频），与 volatility_cone._TRADING_DAYS 一致。
_TRADING_DAYS = 252.0
# 缺省基准间距的波动率窗口（60 日），与 suggest_reference_spread 默认一致。
_DERIVE_WINDOW = 60
# 默认成本参数镜像 pretrade.schemas.TransactionCostConfig（参考实现，独立复制，
# 不反向 import pretrade）。
_DEFAULT_COSTS: dict[str, float] = {
    "commission_rate": 0.0003,
    "stamp_tax_rate": 0.0005,
    "half_spread_rate": 0.0002,
    "impact_coefficient": 0.0005,
    "etf_stamp_tax": False,  # 默认按 ETF 免印花税（与 pretrade 默认一致）。
}
_DEFAULT_MULTIPLIERS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
_MIN_HISTORY = 20


def round_trip_cost_bps(costs: dict[str, float] | None = None) -> float:
    """单次 round-trip 成本（基点）。镜像 pretrade 成本模型。

    ``2×(commission+half_spread) + stamp_tax + impact``；``etf_stamp_tax``
    为 False 时印花税计 0（ETF 默认）。
    """
    cfg = {**_DEFAULT_COSTS, **(costs or {})}
    commission = float(cfg["commission_rate"])
    half_spread = max(float(cfg["half_spread_rate"]), 0.0)
    stamp = float(cfg["stamp_tax_rate"]) if bool(cfg.get("etf_stamp_tax")) else 0.0
    impact = float(cfg["impact_coefficient"])
    rate = 2.0 * (commission + half_spread) + stamp + impact
    return round(rate * 10000.0, 2)


def _clean_close(close: pd.Series | pd.DataFrame | list[float] | None) -> pd.Series:
    """统一为正值价格 Series（DataFrame 取 close 列；无 close 列视为空）。"""
    if close is None:
        return pd.Series(dtype=float)
    if isinstance(close, pd.DataFrame):
        if "close" not in close.columns:
            return pd.Series(dtype=float)
        close = close["close"]
    series = pd.to_numeric(pd.Series(close), errors="coerce").dropna().astype(float)
    return series[series > 0]


def _grid_triggers(close: pd.Series, spread: float) -> int:
    """回放间距 ``spread`` 网格：网格索引每变化一次记 1 次触发（买或卖）。"""
    p0 = close.iloc[0]
    if p0 <= 0 or spread <= 0:
        return 0
    growth = np.log1p(spread)
    index: int | None = None
    triggers = 0
    for price in close:
        grid_index = int(round(np.log(price / p0) / growth))
        if index is None:
            index = grid_index
            continue
        if grid_index != index:
            triggers += 1
            index = grid_index
    return triggers


def _derive_base_spread(close: pd.Series) -> float | None:
    """缺省基准间距：60 日年化波动率 ×3.0 × vol_rank 乘子（复用 volatility_cone）。"""
    returns = close.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    if returns.empty:
        return None
    window_std = returns.rolling(
        _DERIVE_WINDOW, min_periods=max(_DERIVE_WINDOW // 2, 2)
    ).std()
    if window_std.empty or np.isnan(window_std.iloc[-1]):
        return None
    current_vol = float(window_std.iloc[-1]) * np.sqrt(_TRADING_DAYS)
    rank = current_vol_rank(returns, _DERIVE_WINDOW)
    return suggest_reference_spread(current_vol, rank)


def sweep_spread_grid(
    close: pd.Series | pd.DataFrame | list[float],
    *,
    base_spread: float | None = None,
    multipliers: tuple[float, ...] = _DEFAULT_MULTIPLIERS,
    costs: dict[str, float] | None = None,
    min_history: int = _MIN_HISTORY,
) -> list[dict]:
    """间距网格扫描：返回换手/成本权衡曲线。

    参数：
        close: 价格序列（Series / DataFrame(close) / list）。
        base_spread: 基准间距（价格比例，如 0.012 = 1.2%）；None 时从
            60 日年化波动率推导（``suggest_reference_spread``）。
        multipliers: 网格间距 = 基准 × 乘子。
        costs: 成本参数覆盖（镜像 TransactionCostConfig）；缺省用默认值。
        min_history: 历史少于该天数不模拟（confidence=low）。

    返回：
        ``[{spread, trigger_count, annualized_trigger_count,
        estimated_cost_bps, confidence}]``，按 multipliers 顺序。
    """
    series = _clean_close(close)
    if series.empty:
        return []
    if len(series) < min_history:
        return [{
            "spread": None,
            "trigger_count": None,
            "annualized_trigger_count": None,
            "estimated_cost_bps": None,
            "confidence": "low",
        }]
    if base_spread is None:
        base_spread = _derive_base_spread(series)
        if base_spread is None:
            return [{
                "spread": None,
                "trigger_count": None,
                "annualized_trigger_count": None,
                "estimated_cost_bps": None,
                "confidence": "low",
            }]
    rt_cost_bps = round_trip_cost_bps(costs)
    n = len(series)
    years = n / _TRADING_DAYS
    rows: list[dict] = []
    for multiplier in multipliers:
        spread = round(float(base_spread) * float(multiplier), 4)
        triggers = _grid_triggers(series, spread)
        annualized = round(triggers / years, 2) if years > 0 else 0.0
        rows.append({
            "spread": spread,
            "trigger_count": triggers,
            "annualized_trigger_count": annualized,
            "estimated_cost_bps": round(annualized * rt_cost_bps, 2),
            "confidence": "high",
        })
    return rows


__all__ = ["sweep_spread_grid", "round_trip_cost_bps"]
