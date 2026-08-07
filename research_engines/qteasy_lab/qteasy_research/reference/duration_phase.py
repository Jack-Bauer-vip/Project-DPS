"""宏观状态持续期（阶段二 M3）：统计各宏观状态的连续月数并输出阶段。

- 对 9 个宏观状态**全部**计算当前连续月数（``durations`` 全量输出给系统A）。
- scalar ``phase`` 只由方向性/张力状态驱动（``rate_up / rate_down / curve_inverted /
  real_yield_up``，与 ``macro_regime.rate_proxy=DGS30`` 的主次一致）；多方向性状态
  并存时取优先级第一个，``phase_basis`` 明确标注依据，保证 phase 是**单一确定值**。
- 分档：连续 ``<3`` 月 early / ``<6`` 月 mid / ``>=6`` 月 late。
- **降级链**：空表 / 末行 ``macro_unavailable`` / 无方向性状态 → ``phase=None``
  + 显式 ``reason``（不虚构阶段，延续"不把缺失当作中性"安全语义）。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

# 9 个宏观状态列（与 macro_scenarios._all_states 对齐）。
_STATE_COLUMNS: tuple[str, ...] = (
    "rate_up", "rate_down", "rate_stable",
    "curve_inverted", "curve_normal",
    "real_yield_up", "real_yield_down", "real_yield_stable",
)
# 方向性/张力状态优先级：决定 scalar phase 的依据（多状态并存取第一个）。
_PHASE_PRIORITY: tuple[str, ...] = (
    "rate_up", "rate_down", "curve_inverted", "real_yield_up",
)
# 分档阈值：连续月数 <3 → early，<6 → mid，>=6 → late。
PHASE_EARLY_MONTHS = 3
PHASE_MID_MONTHS = 6


def _trailing_true_run(series: pd.Series) -> int:
    """末位连续 True 的月数（逆序扫描，遇 False/NaN 即停）。"""
    count = 0
    for value in reversed(series.astype(bool).tolist()):
        if value:
            count += 1
        else:
            break
    return count


def build_duration_phase(macro_table: pd.DataFrame) -> dict[str, Any]:
    """宏观状态连续月数统计 → 阶段。

    参数：
        macro_table: ``build_monthly_scenario_table()`` 输出的场景表
        （列含 month/states/macro_unavailable + 9 个布尔状态列）。

    返回：
        ``{"phase", "phase_confidence", "phase_basis", "durations",
        "active_states", "reason"}``。phase 为 None 时 reason 说明原因。
    """
    base: dict[str, Any] = {
        "phase": None,
        "phase_confidence": None,
        "phase_basis": None,
        "durations": {},
        "active_states": [],
        "reason": None,
    }
    if macro_table is None or macro_table.empty:
        base["reason"] = "macro_table_empty"
        return base
    table = macro_table.sort_values("month")  # 防御：确保升序
    last = table.iloc[-1]
    if bool(last.get("macro_unavailable", False)):
        # 末月宏观不可用 → 不把缺失当早段。
        base["reason"] = "macro_unavailable"
        base["active_states"] = [
            s for s in _STATE_COLUMNS if bool(last.get(s, False))
        ]
        return base

    durations = {
        state: _trailing_true_run(table[state]) if state in table.columns else 0
        for state in _STATE_COLUMNS
    }
    base["durations"] = durations
    active_states = [s for s in _STATE_COLUMNS if bool(last.get(s, False))]
    base["active_states"] = active_states

    basis = next((s for s in _PHASE_PRIORITY if s in active_states), None)
    if basis is None:
        # 无方向性状态（稳定态）→ phase 无法确定，显式 reason。
        base["phase_confidence"] = "low"
        base["reason"] = "no_directional_state"
        return base

    run = durations[basis]
    base["phase"] = "early" if run < PHASE_EARLY_MONTHS else (
        "mid" if run < PHASE_MID_MONTHS else "late"
    )
    base["phase_basis"] = basis
    # confidence 用活跃状态中最短持续期衡量当前状态元组的一致性。
    shortest = min(durations[s] for s in active_states) if active_states else run
    base["phase_confidence"] = (
        "high" if shortest >= PHASE_MID_MONTHS
        else "medium" if shortest >= PHASE_EARLY_MONTHS
        else "low"
    )
    return base


__all__ = [
    "PHASE_EARLY_MONTHS",
    "PHASE_MID_MONTHS",
    "build_duration_phase",
]
