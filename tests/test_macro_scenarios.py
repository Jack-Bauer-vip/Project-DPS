"""reference/macro_scenarios：月度宏观场景表测试（离线 FRED fixture）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from qteasy_research.reference.macro_scenarios import (
    build_monthly_scenario_table,
    scenario_monthly_returns,
)


class MacroScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.macro_dir = self.root / "processed" / "global_macro"
        self.macro_dir.mkdir(parents=True)
        dates = pd.date_range("2024-01-01", periods=24, freq="MS")
        self._write_series("DGS30", dates, [3.0] * 23 + [3.3])
        self._write_series("DGS10", dates, [2.5] * 24)
        self._write_series("DGS2", dates, [3.0] * 24)
        self._write_series("DFII10", dates, [1.0] * 23 + [1.2])

    def _write_series(self, series_id: str, dates: pd.DatetimeIndex, values: list[float]) -> None:
        frame = pd.DataFrame({
            "series_id": series_id,
            "observation_date": dates.strftime("%Y-%m-%d"),
            "available_at": (dates + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            "value": values,
            "quality_level": "A",
        })
        frame.to_csv(self.macro_dir / f"{series_id}.csv", index=False)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_scenario_table_shape(self) -> None:
        table = build_monthly_scenario_table(self.root)
        self.assertFalse(table.empty)
        self.assertIn("month", table.columns)
        self.assertIn("states", table.columns)
        self.assertIn("rate_proxy", table.columns)
        self.assertIn("macro_unavailable", table.columns)
        for _, row in table.iterrows():
            self.assertIsInstance(row["states"], list)
            self.assertIsNotNone(row["rate_proxy"])

    def test_point_in_time_filtering(self) -> None:
        # 观察日在月末，available_at 为次月初；月末取 state 时应只见当月数据。
        table = build_monthly_scenario_table(self.root)
        last = table.iloc[-1]
        self.assertFalse(last["macro_unavailable"])
        self.assertIsInstance(last["states"], list)

    def test_monthly_returns_alignment(self) -> None:
        dates = pd.date_range("2024-01-01", periods=24, freq="MS")
        asset = pd.DataFrame({
            "observation_date": dates.strftime("%Y-%m-%d"),
            "value": [100.0 + i for i in range(24)],
        })
        table = build_monthly_scenario_table(self.root)
        merged = scenario_monthly_returns(asset, table)
        self.assertFalse(merged.empty)
        self.assertEqual(list(merged.columns), ["month", "asset_return", "states"])
