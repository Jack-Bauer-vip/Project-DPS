"""reference/volatility_cone：波动率锥与参考间距建议测试。"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from qteasy_research.reference.volatility_cone import (
    build_volatility_cone,
    current_vol_rank,
    suggest_reference_spread,
)


def _returns(n: int = 800, seed: int = 3) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(0.0003, 0.012, n))


class VolatilityConeTests(unittest.TestCase):
    def test_cone_long_table_columns(self) -> None:
        cone = build_volatility_cone(_returns())
        self.assertEqual(list(cone.columns), ["window_days", "percentile", "value"])
        self.assertEqual(set(cone["window_days"]), {20, 60, 120, 252})
        self.assertEqual(len(cone), 4 * 5)

    def test_cone_percentile_order(self) -> None:
        cone = build_volatility_cone(_returns())
        for window in (20, 60, 120, 252):
            subset = cone[cone["window_days"] == window].set_index("percentile")["value"]
            self.assertLess(subset[5], subset[95], f"窗口 {window} 的 p5 应小于 p95")

    def test_rank_in_unit_interval(self) -> None:
        rank = current_vol_rank(_returns(), 60)
        self.assertIsNotNone(rank)
        self.assertTrue(0.0 <= rank <= 1.0)

    def test_spread_boundaries(self) -> None:
        # daily_vol=0.2/sqrt(252)，base=×3 → 约 0.0378；乘子分档。
        cases = [
            (0.10, 0.8),
            (0.30, 1.0),
            (0.50, 1.15),
            (0.70, 1.35),
            (0.90, 1.6),
        ]
        base = 0.20 / np.sqrt(252) * 3.0
        for rank, multiplier in cases:
            expected = round(base * multiplier, 4)
            spread = suggest_reference_spread(0.20, rank)
            self.assertAlmostEqual(spread, expected, delta=0.0002, msg=f"rank={rank}")

    def test_spread_invalid_input(self) -> None:
        self.assertIsNone(suggest_reference_spread(None, 0.5))
        self.assertIsNone(suggest_reference_spread(0.0, 0.5))
        self.assertIsNone(suggest_reference_spread(float("nan"), 0.5))
        # vol_rank 未知时用中性乘子给参考间距，不返回 None。
        self.assertIsNotNone(suggest_reference_spread(0.20, None))
