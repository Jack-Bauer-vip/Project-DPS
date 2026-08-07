"""reference/hedge_efficiency：宏观对冲效率测试（离线 fixture，不依赖真实数据目录）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from qteasy_research.reference.hedge_efficiency import build_hedge_efficiency


def _write_series(macro_dir: Path, series_id: str, dates: pd.DatetimeIndex, values: list[float]) -> None:
    frame = pd.DataFrame({
        "series_id": series_id,
        "observation_date": dates.strftime("%Y-%m-%d"),
        "available_at": (dates + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        "value": values,
        "quality_level": "A",
    })
    frame.to_csv(macro_dir / f"{series_id}.csv", index=False)


class HedgeEfficiencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.macro_dir = self.root / "processed" / "global_macro"
        self.macro_dir.mkdir(parents=True)
        # 24 个资产观测月；宏观多 1 个观测，保证全部月份 pct_change 有值。
        self.dates = pd.date_range("2024-01-31", periods=24, freq="ME")
        self.macro_dates = pd.date_range("2023-12-31", periods=25, freq="ME")
        self.dgs30 = [2.0 + 0.1 * i for i in range(25)]
        self.dfii10 = [1.0 + 0.05 * i for i in range(25)]
        _write_series(self.macro_dir, "DGS30", self.macro_dates, self.dgs30)
        _write_series(self.macro_dir, "DFII10", self.macro_dates, self.dfii10)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _scenario_table(self, unavailable: set[int] | None = None) -> pd.DataFrame:
        # months 0-23；1-6 rate_up，7-12 rate_down，13-18 curve_inverted，19-23 real_yield_up。
        state_spec: list[list[str]] = [[]]
        state_spec += [["rate_up"]] * 6
        state_spec += [["rate_down"]] * 6
        state_spec += [["curve_inverted"]] * 6
        state_spec += [["real_yield_up"]] * 5
        unavailable = unavailable or set()
        return pd.DataFrame({
            "month": self.dates.strftime("%Y-%m-%d"),
            "states": state_spec,
            "macro_unavailable": [i in unavailable for i in range(24)],
        })

    def _correlated_asset(self) -> pd.DataFrame:
        # close = 3 × DGS30（无偏置）→ 月收益与 DGS30 pct_change 完全相同。
        close = [3.0 * self.dgs30[i] for i in range(24)]
        return pd.DataFrame({"trade_date": self.dates, "close": close, "source": "local"})

    def _build(self, asset: pd.DataFrame, scenario_table: pd.DataFrame) -> pd.DataFrame:
        assets = pd.DataFrame({"asset_id": ["ASSET_A"], "name": ["AssetA"]})
        aligned = {"ASSET_A": asset}
        return build_hedge_efficiency(self.root, assets, aligned, scenario_table)

    def test_four_scenarios_grouped(self) -> None:
        hedge = self._build(self._correlated_asset(), self._scenario_table())
        self.assertEqual(len(hedge), 4)
        self.assertEqual(set(hedge["scenario"]),
                         {"rate_up", "rate_down", "curve_inverted", "real_yield_up"})
        for scenario in ("rate_up", "rate_down", "curve_inverted", "real_yield_up"):
            row = hedge.loc[hedge["scenario"] == scenario].iloc[0]
            self.assertEqual(row["asset_id"], "ASSET_A")
            self.assertGreaterEqual(row["sample_count"], 5)

    def test_perfect_positive_correlation(self) -> None:
        # 资产收益与 DGS30 变化完全同向 → corr≈1，hedge_efficiency≈0（对冲无效）。
        hedge = self._build(self._correlated_asset(), self._scenario_table())
        rate_up = hedge.loc[hedge["scenario"] == "rate_up"].iloc[0]
        self.assertEqual(rate_up["sample_count"], 6)
        self.assertEqual(rate_up["confidence"], "high")
        self.assertGreater(float(rate_up["conditional_corr"]), 0.99)
        self.assertLess(float(rate_up["hedge_efficiency"]), 0.01)

    def test_hedge_efficiency_formula_consistency(self) -> None:
        # 对每一行，hedge_efficiency 与 1 - min(|corr|, 1) 数值一致。
        hedge = self._build(self._correlated_asset(), self._scenario_table())
        for _, row in hedge.iterrows():
            if pd.isna(row["conditional_corr"]):
                continue
            corr = float(row["conditional_corr"])
            expected = 1.0 - min(abs(corr), 1.0)
            self.assertAlmostEqual(float(row["hedge_efficiency"]), expected, delta=0.001)

    def test_sample_count_degradation(self) -> None:
        # 资产仅 4 个月（rate_up 仅 3 个样本）→ 全部情景 confidence=low + 数值列空。
        dates = pd.date_range("2024-01-31", periods=4, freq="ME")
        macro_dates = pd.date_range("2023-12-31", periods=5, freq="ME")
        dgs30 = [2.0, 2.1, 2.2, 2.3, 2.4]
        dfii10 = [1.0, 1.1, 1.2, 1.3, 1.4]
        _write_series(self.macro_dir, "DGS30", macro_dates, dgs30)
        _write_series(self.macro_dir, "DFII10", macro_dates, dfii10)
        table = pd.DataFrame({
            "month": dates.strftime("%Y-%m-%d"),
            "states": [[], ["rate_up"], ["rate_up"], ["rate_up"]],
            "macro_unavailable": [False] * 4,
        })
        asset = pd.DataFrame({
            "trade_date": dates,
            "close": [6.0, 6.3, 6.6, 6.9],
            "source": "local",
        })
        hedge = self._build(asset, table)
        self.assertEqual(hedge["confidence"].unique().tolist(), ["low"])
        self.assertTrue(hedge["conditional_corr"].isna().all())
        self.assertEqual(hedge.loc[hedge["scenario"] == "rate_up", "sample_count"].iloc[0], 3)

    def test_macro_unavailable_excluded(self) -> None:
        # rate_up 6 个月中 2 个 macro_unavailable → 剔除后 sample_count=4 → 降级。
        table = self._scenario_table(unavailable={3, 5})
        hedge = self._build(self._correlated_asset(), table)
        rate_up = hedge.loc[hedge["scenario"] == "rate_up"].iloc[0]
        self.assertEqual(rate_up["sample_count"], 4)
        self.assertEqual(rate_up["confidence"], "low")
        self.assertTrue(pd.isna(rate_up["conditional_corr"]))
        # 不受影响的 curve_inverted 保持 high。
        curve = hedge.loc[hedge["scenario"] == "curve_inverted"].iloc[0]
        self.assertEqual(curve["sample_count"], 6)
        self.assertEqual(curve["confidence"], "high")

    def test_empty_asset_and_missing_macro(self) -> None:
        # 空行情资产 → data_quality=D，全 low；宏观序列缺失 → 不崩溃且样本为 0。
        assets = pd.DataFrame({"asset_id": ["ASSET_A"], "name": ["AssetA"]})
        aligned = {"ASSET_A": pd.DataFrame()}
        table = self._scenario_table()
        # 单独清空 DGS30，验证缺失不崩溃。
        hedge = build_hedge_efficiency(self.root, assets, aligned, table)
        self.assertEqual(len(hedge), 4)
        self.assertTrue((hedge["data_quality"] == "D").all())
        self.assertTrue((hedge["confidence"] == "low").all())
        self.assertEqual(hedge["sample_count"].sum(), 0)


if __name__ == "__main__":
    unittest.main()
