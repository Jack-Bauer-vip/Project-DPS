"""reference/duration_phase：宏观状态持续期测试（离线 fixture，不依赖真实数据目录）。"""

from __future__ import annotations

import unittest

import pandas as pd

from qteasy_research.reference.duration_phase import (
    PHASE_EARLY_MONTHS,
    PHASE_MID_MONTHS,
    build_duration_phase,
)

_STATES = [
    "rate_up", "rate_down", "rate_stable",
    "curve_inverted", "curve_normal",
    "real_yield_up", "real_yield_down", "real_yield_stable",
]


def _table(months: list[list[str]], unavailable: set[int] | None = None) -> pd.DataFrame:
    """构造场景表：months 为逐月状态列表，末月取 months[-1]。"""
    n = len(months)
    rows = []
    unavailable = unavailable or set()
    for i, states in enumerate(months):
        row = {
            "month": pd.Timestamp("2024-01-31") + pd.DateOffset(months=i),
            "states": list(states),
            "macro_unavailable": i in unavailable,
        }
        row.update({s: s in states for s in _STATES})
        rows.append(row)
    return pd.DataFrame(rows)


def _months(count: int, states: list[str]) -> list[list[str]]:
    return [list(states) for _ in range(count)]


class DurationPhaseTests(unittest.TestCase):
    def test_phase_boundaries(self) -> None:
        # 连续 2/3/5/6 月 rate_up → early / mid(边界) / mid / late(边界)。
        cases = [
            (2, "early"), (3, "mid"), (5, "mid"), (6, "late"),
        ]
        for run, expected in cases:
            table = _table(_months(run, ["rate_up", "curve_normal"]))
            info = build_duration_phase(table)
            self.assertEqual(info["phase"], expected, f"run={run}")
            self.assertEqual(info["phase_basis"], "rate_up")

    def test_empty_table(self) -> None:
        info = build_duration_phase(pd.DataFrame())
        self.assertIsNone(info["phase"])
        self.assertEqual(info["reason"], "macro_table_empty")
        self.assertIsNone(build_duration_phase(None)["phase"])

    def test_macro_unavailable_last_month(self) -> None:
        table = _table(
            _months(6, ["rate_up", "curve_normal"]), unavailable={5}
        )
        info = build_duration_phase(table)
        self.assertIsNone(info["phase"])
        self.assertEqual(info["reason"], "macro_unavailable")

    def test_stable_state_no_phase(self) -> None:
        table = _table(_months(4, ["rate_stable", "curve_normal", "real_yield_stable"]))
        info = build_duration_phase(table)
        self.assertIsNone(info["phase"])
        self.assertEqual(info["reason"], "no_directional_state")
        self.assertEqual(info["phase_confidence"], "low")

    def test_run_length_reset_on_gap(self) -> None:
        # rate_up 5 月中间插入 1 个不可用月 → 末行 trailing run=2 → early。
        months = [
            ["rate_up", "curve_normal"]] * 2 + [["rate_stable", "curve_normal"]] + [
            ["rate_up", "curve_normal"]] * 2
        table = _table(months)
        info = build_duration_phase(table)
        self.assertEqual(info["phase"], "early")
        self.assertEqual(info["durations"]["rate_up"], 2)

    def test_state_flip(self) -> None:
        # rate_up 6 月后转 rate_down 2 月 → phase=early，basis=rate_down，rate_up 持续 0。
        months = _months(6, ["rate_up", "curve_normal"]) + _months(
            2, ["rate_down", "curve_normal"]
        )
        info = build_duration_phase(_table(months))
        self.assertEqual(info["phase"], "early")
        self.assertEqual(info["phase_basis"], "rate_down")
        self.assertEqual(info["durations"]["rate_up"], 0)
        self.assertEqual(info["durations"]["rate_down"], 2)

    def test_priority_when_multiple_directional(self) -> None:
        # rate_up 与 real_yield_up 同时激活 → basis 取优先级第一个 rate_up。
        months = _months(3, ["rate_up", "real_yield_up", "curve_normal"])
        info = build_duration_phase(_table(months))
        self.assertEqual(info["phase_basis"], "rate_up")
        self.assertEqual(info["phase"], "mid")
        self.assertIn("real_yield_up", info["active_states"])

    def test_confidence_levels(self) -> None:
        # 活跃状态最短持续期 1/3/6 → low/medium/high。
        cases = [(1, "low"), (3, "medium"), (6, "high")]
        for run, expected in cases:
            table = _table(_months(run, ["rate_up", "curve_normal"]))
            info = build_duration_phase(table)
            self.assertEqual(info["phase_confidence"], expected, f"run={run}")

    def test_confidence_uses_shortest_active(self) -> None:
        # rate_up 持续 6 月（late）但 curve_inverted 刚激活 1 月 → confidence=low。
        months = _months(6, ["rate_up", "curve_normal"]) + [
            ["rate_up", "curve_normal", "curve_inverted"]
        ]
        info = build_duration_phase(_table(months))
        self.assertEqual(info["phase"], "late")
        self.assertEqual(info["phase_confidence"], "low")

    def test_missing_state_columns_no_crash(self) -> None:
        # 无 9 布尔列的退化表 → 不抛错、phase=None。
        table = pd.DataFrame({"month": ["2024-01-31"], "states": [[]],
                              "macro_unavailable": [False]})
        info = build_duration_phase(table)
        self.assertIsNone(info["phase"])
        self.assertEqual(info["reason"], "no_directional_state")

    def test_phase_constants_exported(self) -> None:
        self.assertEqual(PHASE_EARLY_MONTHS, 3)
        self.assertEqual(PHASE_MID_MONTHS, 6)


if __name__ == "__main__":
    unittest.main()
