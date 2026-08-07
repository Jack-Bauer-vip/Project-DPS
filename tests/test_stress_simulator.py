"""reference/stress_simulator：压力情景历史回放测试（离线 fixture，不依赖真实数据目录）。"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from qteasy_research.reference.stress_simulator import build_stress_simulator

_ASCII = re.compile(r"^[\x00-\x7F]*$")


def _write_series(macro_dir: Path, series_id: str, dates: pd.DatetimeIndex, values: list[float]) -> None:
    frame = pd.DataFrame({
        "series_id": series_id,
        "observation_date": dates.strftime("%Y-%m-%d"),
        "available_at": (dates + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        "value": values,
        "quality_level": "A",
    })
    frame.to_csv(macro_dir / f"{series_id}.csv", index=False)


class StressSimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.macro_dir = self.root / "processed" / "global_macro"
        self.macro_dir.mkdir(parents=True)
        # 24 个资产观测月；宏观多 1 个观测，保证全部月份 diff 有值。
        self.dates = pd.date_range("2024-01-31", periods=24, freq="ME")
        self.macro_dates = pd.date_range("2023-12-31", periods=25, freq="ME")
        # DGS30：25 观测 → 24 个月绝对差。
        #   月 0-5  +0.6（>50bp 强加息，rate_up_50bp）；月 6-11 -0.6（rate_down_50bp）；
        #   月 12-17 +0.1；月 18-23 +0.1（小波动）。
        self.dgs30 = (
            [2.0, 2.6, 3.2, 3.8, 4.4, 5.0, 5.6]           # 观测 0-6（6 个 +0.6 diff）
            + [5.0, 4.4, 3.8, 3.2, 2.6, 2.0]              # 观测 7-12（6 个 -0.6 diff）
            + [2.1, 2.2, 2.3, 2.4, 2.5, 2.6]              # 观测 13-18（+0.1）
            + [2.7, 2.8, 2.9, 3.0, 3.1, 3.2]              # 观测 19-24（+0.1）
        )
        self.dfii10 = [1.0 + 0.1 * i for i in range(25)]
        _write_series(self.macro_dir, "DGS30", self.macro_dates, self.dgs30)
        _write_series(self.macro_dir, "DFII10", self.macro_dates, self.dfii10)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _scenario_table(self, state_spec: list[list[str]] | None = None) -> pd.DataFrame:
        # 默认 24 月：0-5 rate_up，6-11 rate_down，12-17 curve_inverted，
        #       18-22 rate_up+real_yield_up（滞胀），23 real_yield_up。
        state_spec = state_spec or (
            [["rate_up"]] * 6 + [["rate_down"]] * 6
            + [["curve_inverted"]] * 6 + [["rate_up", "real_yield_up"]] * 5
            + [["real_yield_up"]]
        )
        return pd.DataFrame({
            "month": self.dates.strftime("%Y-%m-%d"),
            "states": state_spec,
            "macro_unavailable": [False] * 24,
        })

    def _correlated_asset(self, series: list[float] | None = None) -> pd.DataFrame:
        # 资产月收益 = k × 宏观序列月绝对差（pct_change 与 diff 完全同向）。
        # 用水平×乘子不成立：线性增长的 pct_change 递减，与绝对差相关性差。
        series = series or self.dgs30
        diffs = list(pd.Series(series).diff().values[1:])  # 24 个月绝对差
        k = 0.1
        close = [100.0]
        for i in range(1, 24):
            close.append(close[-1] * (1.0 + k * diffs[i]))
        return pd.DataFrame({"trade_date": self.dates, "close": close, "source": "local"})

    def _build(self, asset: pd.DataFrame, table: pd.DataFrame) -> dict:
        assets = pd.DataFrame({"asset_id": ["ASSET_A"], "name": ["AssetA"]})
        aligned = {"ASSET_A": asset}
        return build_stress_simulator(assets, aligned, table, self.root)

    def test_five_scenarios_keys(self) -> None:
        stress = self._build(self._correlated_asset(), self._scenario_table())["ASSET_A"]
        self.assertEqual(
            set(stress),
            {"rate_up_50bp", "rate_down_50bp", "curve_inverted",
             "real_yield_up", "stagnation"},
        )

    def test_rate_up_50bp_samples_and_pnl(self) -> None:
        # DGS30 月 0-5 绝对差 +0.6 → 强加息样本；资产同向 → pnl_pct>0。
        # （资产月收益 pct_change 首月 NaN 被剔除，故 5 个样本。）
        stress = self._build(self._correlated_asset(), self._scenario_table())["ASSET_A"]
        rate_up = stress["rate_up_50bp"]
        self.assertEqual(rate_up["sample_count"], 5)
        self.assertGreater(rate_up["pnl_pct"], 0)
        self.assertEqual(rate_up["basis"], "DGS30")

    def test_rate_down_50bp_samples_and_pnl(self) -> None:
        stress = self._build(self._correlated_asset(), self._scenario_table())["ASSET_A"]
        rate_down = stress["rate_down_50bp"]
        self.assertEqual(rate_down["sample_count"], 6)
        self.assertLess(rate_down["pnl_pct"], 0)

    def test_less_than_50bp_not_included(self) -> None:
        # DGS30 月 12-23 绝对差 +0.1（<50bp）→ rate_up_50bp 不应含这些月份。
        stress = self._build(self._correlated_asset(), self._scenario_table())["ASSET_A"]
        self.assertEqual(stress["rate_up_50bp"]["sample_count"], 5)  # 仅 DGS30 大涨月

    def test_stagnation_composite(self) -> None:
        # 滞胀：rate_up AND real_yield_up 同时（月 18-22，5 个样本）。
        stress = self._build(self._correlated_asset(), self._scenario_table())["ASSET_A"]
        stag = stress["stagnation"]
        self.assertEqual(stag["sample_count"], 5)
        self.assertEqual(stag["basis"], "DFII10")

    def test_real_yield_up_samples(self) -> None:
        # real_yield_up：月 18-23（6 个样本）。
        stress = self._build(self._correlated_asset(), self._scenario_table())["ASSET_A"]
        self.assertEqual(stress["real_yield_up"]["sample_count"], 6)

    def test_corr_positive_for_correlated_asset(self) -> None:
        # DGS30 绝对差渐增（+0.5~+1.1，均 ≥50bp）→ 资产收益同向，corr 接近 1。
        # （固定 +0.6 的平差序列会使 asset_ret 常数化 → pandas corr 为 NaN。）
        dgs30_corr = ([2.0, 2.5, 3.1, 3.8, 4.6, 5.5, 6.5, 7.6] + [7.7] * 17)
        _write_series(self.macro_dir, "DGS30", self.macro_dates, dgs30_corr)
        stress = self._build(self._correlated_asset(dgs30_corr), self._scenario_table())["ASSET_A"]
        rate_up = stress["rate_up_50bp"]
        self.assertIsNotNone(rate_up["corr"])
        self.assertGreater(rate_up["corr"], 0.9)

    def test_high_confidence_with_14_samples(self) -> None:
        # 连续 14 个月 rate_up → rate_up_50bp 若 DGS30 全部大涨则 confidence=high。
        dgs30_high = (
            [2.0] + [2.0 + 0.6 * i for i in range(1, 25)]  # 绝对差恒 +0.6
        )
        _write_series(self.macro_dir, "DGS30", self.macro_dates, dgs30_high)
        table = pd.DataFrame({
            "month": self.dates.strftime("%Y-%m-%d"),
            "states": [[]] + [["rate_up"]] * 23,
            "macro_unavailable": [False] * 24,
        })
        stress = self._build(self._correlated_asset(), table)["ASSET_A"]
        rate_up = stress["rate_up_50bp"]
        self.assertGreaterEqual(rate_up["sample_count"], 12)
        self.assertEqual(rate_up["confidence"], "high")

    def test_insufficient_samples_degrades(self) -> None:
        # DGS30 仅 3 个 ≥50bp 强加息月，但资产月收益 pct_change 剔除首月后只剩 2 个
        # 可用样本（< STRESS_MIN_SAMPLES）→ 数值 None + confidence=low（不虚构压力损益）。
        dgs30_few = [2.0, 2.6, 3.2, 3.8] + [3.9] * 21
        _write_series(self.macro_dir, "DGS30", self.macro_dates, dgs30_few)
        table = pd.DataFrame({
            "month": self.dates.strftime("%Y-%m-%d"),
            "states": [[]] + [["rate_up"]] * 3 + [[]] * 20,
            "macro_unavailable": [False] * 24,
        })
        stress = self._build(self._correlated_asset(), table)["ASSET_A"]
        rate_up = stress["rate_up_50bp"]
        self.assertEqual(rate_up["sample_count"], 2)
        self.assertIsNone(rate_up["pnl_pct"])
        self.assertIsNone(rate_up["corr"])
        self.assertEqual(rate_up["confidence"], "low")

    def test_empty_asset_and_empty_macro(self) -> None:
        # 空行情资产 → 不产出；宏观序列缺失 → 全情景 sample_count=0（不崩）。
        assets = pd.DataFrame({"asset_id": ["EMPTY_A"], "name": ["EmptyA"]})
        table = self._scenario_table()
        aligned = {"EMPTY_A": pd.DataFrame()}
        stress = build_stress_simulator(assets, aligned, table, self.root)
        self.assertEqual(stress, {})

        # 资产有行情但宏观表空 → 不产出。
        table2 = pd.DataFrame(columns=["month", "states", "macro_unavailable"])
        stress2 = build_stress_simulator(
            pd.DataFrame({"asset_id": ["ASSET_A"], "name": ["A"]}),
            {"ASSET_A": self._correlated_asset()},
            table2,
            self.root,
        )
        self.assertEqual(stress2, {})

    def test_missing_macro_file_no_crash(self) -> None:
        # 清空 DGS30 文件 → rate_up_50bp 样本 0，不崩溃。
        import os
        os.remove(self.macro_dir / "DGS30.csv")
        stress = self._build(self._correlated_asset(), self._scenario_table())["ASSET_A"]
        self.assertEqual(stress["rate_up_50bp"]["sample_count"], 0)
        self.assertIsNone(stress["rate_up_50bp"]["pnl_pct"])

    def test_no_chinese_in_output(self) -> None:
        stress = self._build(self._correlated_asset(), self._scenario_table())["ASSET_A"]
        for scenario, info in stress.items():
            self.assertRegex(scenario, _ASCII)
            for key, value in info.items():
                self.assertRegex(key, _ASCII)
                if isinstance(value, str):
                    self.assertRegex(value, _ASCII, f"{scenario}.{key}")


if __name__ == "__main__":
    unittest.main()
