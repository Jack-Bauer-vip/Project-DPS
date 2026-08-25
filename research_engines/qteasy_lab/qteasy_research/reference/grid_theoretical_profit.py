"""网格交易理论收益补充参考（B2 扩展，REFERENCE_ONLY）。

在网格建议包 / 推荐包上附加「理论收益」参考字段，双口径 + 成本临界标注：
- 会计式（A 端说明书 §7）：``annual_accounting_profit = round_profit × n_annual −
  n_annual × 2 × amount_per_grid × fee_rate``；``round_profit = amount_per_grid ×
  regular_spread``；``n_annual`` 为年化完整往返次数（2 次单边网格触发 = 1 次往返）。
- 理论最大上限（K 库路径长度模型）：``theory_max = 0.8 × w × σ_annual × n_annual ×
  (amount_per_grid × levels_total)``（金额口径，网格占用本金 = 单份金额 × 满仓档数），
  仅为理论上限，非预期收益。
- 成本临界：``regular_spread > 2 × fee_rate``（往返双边费率）才有利润。
- N 双口径独立字段：``n_annual``（频率估算，与 suitability.trigger_freq 同源）与
  ``backtest_n_annual``（回测实测，可选）分别标注；``n_deviation`` 为偏差。

纯新增模块，不改任何现有回测逻辑。机器产出全 ASCII，零中文策略名。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from qteasy_research.reference.grid_suggestion import (
    _annualized_single_side_triggers,
    _asset_returns,
)

# 年化系数（日频），与 grid_suggestion / volatility_cone 一致。
_TRADING_DAYS = 252.0
# K 库路径长度模型系数（R ≈ 0.8 × w × σ × N）。
_THEORY_MAX_COEF = 0.8


def _sigma_annual(frame: pd.DataFrame) -> float | None:
    """60d 日收益年化波动率（与 grid_suggestion._vol_60 同口径）。"""
    if frame is None or frame.empty:
        return None
    returns = _asset_returns(frame)
    if returns.empty:
        return None
    vol = returns.rolling(60, min_periods=max(60 // 2, 2)).std()
    if vol.empty or pd.isna(vol.iloc[-1]):
        return None
    value = float(vol.iloc[-1]) * np.sqrt(_TRADING_DAYS)
    return value if np.isfinite(value) and value > 0 else None


def _degraded(
    asset_id: str,
    backtest_n_annual: float | None,
    reason: str,
) -> dict[str, Any]:
    """缺数据降级 dict（字段置 None，不虚构）。"""
    return {
        "asset_id": asset_id,
        "degraded": True,
        "round_profit": None,
        "n_annual": None,
        "backtest_n_annual": round(float(backtest_n_annual), 4)
        if backtest_n_annual is not None else None,
        "n_deviation": None,
        "annual_accounting_profit": None,
        "theory_max": None,
        "cost_breakeven": None,
        "basis": "accounting",
        "assumptions": [reason],
    }


def compute_asset_theoretical_profit(
    asset_id: str,
    regular_spread: float | None,
    frame: pd.DataFrame,
    amount_per_grid: float,
    fee_rate: float,
    theory_max_w: float = 1.0,
    levels_total: int = 1,
    backtest_n_annual: float | None = None,
) -> dict[str, Any]:
    """单标的理论收益（会计式 + 路径模型 + 成本临界）。

    缺 spread / 价格数据 / 触发数 → 降级 dict（字段置 None + ``degraded=True``），
    不虚构。``backtest_n_annual`` 由调用方按回测实测填入（未跑回测则 None）。
    ``theory_max``（K 库路径模型）为金额口径：
    ``0.8 × w × σ_annual × n_annual × (amount_per_grid × levels_total)``，
    网格占用本金 = 单份金额 × 总档数（levels_total 与推荐包 9 档全占一致）。
    """
    aid = str(asset_id)
    spread = None if regular_spread is None else float(regular_spread)
    if spread is None or spread <= 0 or frame is None or frame.empty:
        return _degraded(aid, backtest_n_annual, "spread or price data missing")

    round_profit = round(float(amount_per_grid) * spread, 4)
    single_side = _annualized_single_side_triggers(frame, spread)
    if single_side <= 0:
        return _degraded(aid, backtest_n_annual, "n_annual not computable (no grid triggers)")

    # 完整往返次数 = 单边触发次数 / 2（1 买 + 1 卖 = 1 往返，档位变化 2 次）。
    n_annual = round(single_side * 0.5, 4)
    annual_accounting_profit = round(
        round_profit * n_annual - n_annual * 2.0 * float(amount_per_grid) * float(fee_rate),
        4,
    )
    sigma = _sigma_annual(frame)
    capital_grid = float(amount_per_grid) * max(1, int(levels_total))
    theory_max = (
        round(_THEORY_MAX_COEF * float(theory_max_w) * sigma * n_annual * capital_grid, 4)
        if sigma is not None
        else None
    )
    cost_breakeven = spread > 2.0 * float(fee_rate)
    deviation = (
        round(float(backtest_n_annual) - n_annual, 4)
        if backtest_n_annual is not None and n_annual is not None
        else None
    )
    return {
        "asset_id": aid,
        "degraded": False,
        "round_profit": round_profit,
        "n_annual": n_annual,
        "backtest_n_annual": round(float(backtest_n_annual), 4)
        if backtest_n_annual is not None else None,
        "n_deviation": deviation,
        "annual_accounting_profit": annual_accounting_profit,
        "theory_max": theory_max,
        "cost_breakeven": cost_breakeven,
        "basis": "accounting",
        "assumptions": [
            "round_trip = single_side_grid_triggers / 2",
            "theory_max = 0.8 * w * sigma_annual * n_annual * (amount_per_grid * levels_total)",
            "cost_breakeven: regular_spread > 2 * fee_rate",
        ],
    }


def compute_asset_theoretical_profit_actual(
    asset_id: str,
    regular_spread_actual: float | None,
    regular_levels_per_side: int | None,
    edge_levels_per_side: int | None,
    frame: pd.DataFrame,
    amount_per_grid: float,
    fee_rate: float,
    theory_max_w: float = 1.0,
    backtest_n_annual: float | None = None,
) -> dict[str, Any]:
    """按 A 端现有网格参数（契约 ``assets[].grid_config`` 权威值）计算理论收益。

    与 B 建议口径 ``compute_asset_theoretical_profit`` 同一公式，仅替换两个入参：
    - ``regular_spread_actual`` = A 权威 ``regular_spread``（如 159985.SZ = 0.015）。
    - ``levels_total`` = A 权威满仓档数 ``(regular_levels_per_side +
      edge_levels_per_side) × 2 + 1``。当前契约 grid_config 为 per-side 对称档数
      （上下各 regular+edge 档 + 中轴 1 档），故 2×side+1 对所有现有配置成立；
      **仅适用对称网格**——未来支持非对称（上下不同）需改为逐侧读取，不能套公式。
    - ``amount_per_grid`` 与 B 建议口径共用配置值：会计式语义 = 每格买卖一份的量
      （per-trade lot），与间距宽窄/档数多少无关；A 契约当前无 per-asset 权威值，
      per-asset 参数化挂起待办，落地后可独立传入。

    任一输入缺失 → 降级 dict（``degraded=True``），不虚构。
    """
    aid = str(asset_id)
    if regular_spread_actual is None or float(regular_spread_actual) <= 0:
        return _degraded(aid, backtest_n_annual, "actual grid_config missing (contract not exported)")
    if regular_levels_per_side is None or edge_levels_per_side is None:
        return _degraded(aid, backtest_n_annual, "actual grid levels missing")
    levels_total = (
        int(regular_levels_per_side) + int(edge_levels_per_side)
    ) * 2 + 1
    tp = compute_asset_theoretical_profit(
        aid,
        float(regular_spread_actual),
        frame,
        float(amount_per_grid),
        float(fee_rate),
        theory_max_w=float(theory_max_w),
        levels_total=levels_total,
        backtest_n_annual=backtest_n_annual,
    )
    if tp.get("degraded"):
        return tp
    tp["basis"] = "accounting (actual grid params)"
    tp["actual_levels_total"] = levels_total
    tp["assumptions"] = (tp.get("assumptions") or []) + [
        "computed with A actual grid params (contract assets[].grid_config)",
        "levels_total_actual = (regular+edge per side) * 2 + 1 (symmetric per-side)",
    ]
    return tp


def compute_portfolio_theoretical_profit(
    plan_asset_ids: list[str],
    per_asset: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """组合理论收益（本轮降级方案）。

    只做「会计式组合年收益汇总 + 各标的理论最大收益清单」；组合理论最大
    ``R = k × C × Leff%`` 留待下一轮（B 端无现成 Leff 计算，口径不可靠不进入
    系统）。``degraded=True`` 表示组合理论最大未上线。
    """
    total = 0.0
    per_asset_max: dict[str, float | None] = {}
    for asset_id in plan_asset_ids:
        info = per_asset.get(asset_id) or {}
        annual = info.get("annual_accounting_profit")
        if annual is not None:
            total += float(annual)
        per_asset_max[asset_id] = info.get("theory_max")
    return {
        "annual_accounting_total": round(total, 4),
        "per_asset_max": per_asset_max,
        "degraded": True,
        "note": "portfolio theoretical max (k*C*Leff%) deferred to next round",
    }


__all__ = [
    "compute_asset_theoretical_profit",
    "compute_asset_theoretical_profit_actual",
    "compute_portfolio_theoretical_profit",
]
