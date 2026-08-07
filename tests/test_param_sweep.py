"""reference/param_sweep：间距网格扫描测试（离线 fixture，不依赖真实数据目录）。"""

from __future__ import annotations

import re
import unittest

import numpy as np
import pandas as pd

from qteasy_research.reference.param_sweep import round_trip_cost_bps, sweep_spread_grid

_ASCII = re.compile(r"^[\x00-\x7F]*$")

# 默认成本镜像 TransactionCostConfig（ETF 免印花税）：2×(0.0003+0.0002)+0.0005 = 15 bps。
_DEFAULT_RT_BPS = 15.0


class ParamSweepTests(unittest.TestCase):
    def _random_walk(self, n: int = 120, seed: int = 7) -> list[float]:
        rng = np.random.default_rng(seed)
        return list(100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n))))

    def test_trigger_count_crossings(self) -> None:
        # close 在 [100,110] 交替，d=0.1：每次跨越网格线记 1 次触发 → 5 次。
        # （min_history=1 放行短序列，聚焦穿越计数而非降级。）
        close = [100.0, 110.0, 100.0, 110.0, 100.0, 110.0]
        rows = sweep_spread_grid(close, base_spread=0.1, multipliers=(1.0,), min_history=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trigger_count"], 5)
        self.assertEqual(rows[0]["spread"], 0.1)
        self.assertEqual(rows[0]["confidence"], "high")

    def test_estimated_cost_bps_value(self) -> None:
        # 年化触发 = 5/(6/252) = 210 次/年；成本 = 210 × 15 bps = 3150。
        close = [100.0, 110.0, 100.0, 110.0, 100.0, 110.0]
        rows = sweep_spread_grid(close, base_spread=0.1, multipliers=(1.0,), min_history=1)
        self.assertAlmostEqual(rows[0]["annualized_trigger_count"], 210.0)
        self.assertAlmostEqual(rows[0]["estimated_cost_bps"], 210.0 * _DEFAULT_RT_BPS)

    def test_multipliers_monotonic(self) -> None:
        # 间距越大 → 触发越少、成本越低；spread 严格递增。
        rows = sweep_spread_grid(
            self._random_walk(), base_spread=0.012, multipliers=(0.5, 1.0, 2.0)
        )
        self.assertEqual([r["spread"] for r in rows], [0.006, 0.012, 0.024])
        triggers = [r["trigger_count"] for r in rows]
        self.assertEqual(triggers, sorted(triggers, reverse=True))
        self.assertTrue(all(r["confidence"] == "high" for r in rows))

    def test_base_spread_derived_from_vol(self) -> None:
        # 120 天随机游走 → 60 日年化波动率可推导基准间距，产出正向 spread。
        rows = sweep_spread_grid(self._random_walk(), multipliers=(1.0,))
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]["spread"])
        self.assertGreater(rows[0]["spread"], 0.0)
        self.assertEqual(rows[0]["confidence"], "high")

    def test_short_history_degrades(self) -> None:
        # 历史 <20 天 → 单行 None + confidence=low，不虚构扫描结果。
        close = self._random_walk(n=10)
        rows = sweep_spread_grid(close, base_spread=0.012)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["spread"])
        self.assertIsNone(rows[0]["trigger_count"])
        self.assertIsNone(rows[0]["estimated_cost_bps"])
        self.assertEqual(rows[0]["confidence"], "low")

    def test_undecidable_base_spread_degrades(self) -> None:
        # 显式 min_history=1 但行情太少无法推导基准间距 → low 单行（不崩）。
        rows = sweep_spread_grid([100.0, 101.0], min_history=1)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["spread"])
        self.assertEqual(rows[0]["confidence"], "low")

    def test_empty_series_returns_empty(self) -> None:
        self.assertEqual(sweep_spread_grid([]), [])
        self.assertEqual(sweep_spread_grid(pd.DataFrame()), [])

    def test_dataframe_close_column(self) -> None:
        # 传入对齐行情 DataFrame（trade_date/close）→ 取 close 列。
        frame = pd.DataFrame({
            "trade_date": pd.date_range("2026-01-01", periods=6, freq="D"),
            "close": [100.0, 110.0, 100.0, 110.0, 100.0, 110.0],
        })
        rows = sweep_spread_grid(frame, base_spread=0.1, multipliers=(1.0,), min_history=1)
        self.assertEqual(rows[0]["trigger_count"], 5)

    def test_round_trip_cost_mirrors_pretrade(self) -> None:
        # 默认（ETF 免印花税）= 15 bps；etf_stamp_tax=True = 20 bps；
        # 自定义佣金/冲击成本覆盖生效。
        self.assertEqual(round_trip_cost_bps(), _DEFAULT_RT_BPS)
        self.assertEqual(round_trip_cost_bps({"etf_stamp_tax": True}), 20.0)
        # 2×(0.001+0.0002)+0+0.001 = 0.0034 → 34 bps（自定义佣金/冲击覆盖生效）。
        self.assertEqual(
            round_trip_cost_bps({"commission_rate": 0.001, "impact_coefficient": 0.001}),
            34.0,
        )

    def test_no_chinese_in_output(self) -> None:
        rows = sweep_spread_grid(self._random_walk(), base_spread=0.012)
        for row in rows:
            for key, value in row.items():
                self.assertRegex(key, _ASCII)
                if isinstance(value, str):
                    self.assertRegex(value, _ASCII, f"{key}={value}")


if __name__ == "__main__":
    unittest.main()
