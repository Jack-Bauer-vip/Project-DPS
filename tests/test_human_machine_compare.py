"""reference/human_machine_compare：人机对比月报测试（合成数据，不依赖真实数据目录）。

覆盖：human_override_log 解析（BOM/CRLF/缺列/脏行降级）、B 信号三态、方向一致性
三分类、偏离度量、干预后收益、合成数据全链路渲染、报告无中文策略名断言。
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from qteasy_research.reference.human_machine_compare import (
    b_signal,
    build_hmc_report,
    classify_direction,
    monthly_returns_from_prices,
    parse_human_override_log,
    post_intervention_performance,
    render_hmc_markdown,
    weight_delta,
)

_ASCII = re.compile(r"^[\x00-\x7F]*$")
_CJK = re.compile(r"[一-鿿]")

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "human_override_log_synthetic.csv"

# 合成决策包：信号假设与 fixtures/_generate_fixtures.py 联动。
# 512890.SH→bullish（ret20>0）；588230.SH→bearish（ret20<0）；164824.SZ→bullish
# （ret20 缺失，beta 有正）；512880.SH→neutral（ret20=0、无 beta、无 stress）。
PACKAGE = {
    "generated_date": "2026-08-01",
    "data_asof": "2026-07-31",
    "schema_version": "1.0",
    "macro_regime": {
        "phase": "mid",
        "phase_confidence": "high",
        "states": ["rate_up"],
        "state_durations": {"rate_up": 3},
    },
    "assets": [
        {"asset_id": "512890.SH", "name": "证券ETF", "returns": {"20d": 0.03},
         "volatility": {"20d": 0.20}, "beta": {"SPY": {"60d": 0.9}},
         "red_flag": None, "macro_stress": {}},
        {"asset_id": "588230.SH", "name": "科创50", "returns": {"20d": -0.02},
         "volatility": {"20d": 0.25}, "beta": {}, "red_flag": None, "macro_stress": {}},
        {"asset_id": "164824.SZ", "name": "红利低波", "returns": {},
         "volatility": {"20d": 0.30}, "beta": {"SPY": {"60d": 0.7}},
         "red_flag": None, "macro_stress": {}},
        {"asset_id": "512880.SH", "name": "证券ETF基金", "returns": {"20d": 0.0},
         "volatility": {"20d": 0.18}, "beta": {}, "red_flag": None, "macro_stress": {}},
    ],
}

# 干预后表现行情：干预月 2026-05..08 → 次月 2026-06..09（6 只资产，sample_count≥5）。
MONTHLY = {
    "512890.SH": pd.Series({"2026-06": 0.05, "2026-07": 0.02, "2026-08": 0.01, "2026-09": -0.01}),
    "588230.SH": pd.Series({"2026-06": -0.02, "2026-07": 0.01, "2026-08": 0.00, "2026-09": 0.01}),
    "164824.SZ": pd.Series({"2026-06": 0.01, "2026-07": -0.01, "2026-08": 0.02, "2026-09": 0.00}),
    "512880.SH": pd.Series({"2026-06": 0.02, "2026-07": 0.00, "2026-08": 0.01, "2026-09": 0.02}),
    "999001.SH": pd.Series({"2026-06": 0.01, "2026-07": 0.01, "2026-08": 0.01, "2026-09": 0.01}),
    "999002.SH": pd.Series({"2026-06": 0.01, "2026-07": 0.01, "2026-08": 0.01, "2026-09": 0.01}),
}


class ParseTests(unittest.TestCase):
    def test_fixture_parses_72_rows(self) -> None:
        frame = parse_human_override_log(FIXTURE)
        self.assertEqual(len(frame), 72)
        self.assertEqual(set(frame.columns), {
            "time", "strategy_id", "asset_id", "old_weight", "new_weight", "reason", "month",
        })
        self.assertEqual(set(frame["month"]), {"2026-05", "2026-06", "2026-07", "2026-08"})
        self.assertFalse(frame["old_weight"].isna().any())
        self.assertFalse(frame["new_weight"].isna().any())

    def test_missing_file_returns_empty(self) -> None:
        frame = parse_human_override_log(None)
        self.assertTrue(frame.empty)
        frame = parse_human_override_log(Path("D:/definitely_absent.csv"))
        self.assertTrue(frame.empty)

    def test_missing_columns_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.csv"
            path.write_text("time,strategy_id\n2026-01-01,a\n", encoding="utf-8-sig")
            with self.assertRaises(ValueError):
                parse_human_override_log(path)

    def test_dirty_weight_degrades_not_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dirty.csv"
            path.write_text(
                "time,strategy_id,asset_id,old_weight,new_weight,reason\n"
                "2026-01-01T00:00:00,s,512890.SH,abc,0.56,r\n",
                encoding="utf-8-sig",
            )
            frame = parse_human_override_log(path)
            self.assertEqual(len(frame), 1)
            self.assertTrue(pd.isna(frame.loc[0, "old_weight"]))
            # 脏权重不参与偏离度量（降级 0.0，不硬判方向）。
            self.assertEqual(weight_delta(frame.loc[0, "old_weight"], frame.loc[0, "new_weight"]), 0.0)


class SignalTests(unittest.TestCase):
    def test_bullish_positive_return(self) -> None:
        self.assertEqual(b_signal({"returns": {"20d": 0.03}}), "bullish")

    def test_bullish_beta_without_red_flag(self) -> None:
        asset = {"returns": {}, "beta": {"SPY": {"60d": 0.7}}, "red_flag": None}
        self.assertEqual(b_signal(asset), "bullish")

    def test_bearish_negative_return(self) -> None:
        self.assertEqual(b_signal({"returns": {"20d": -0.02}}), "bearish")

    def test_bearish_significant_stress(self) -> None:
        asset = {
            "returns": {"20d": 0.0},
            "macro_stress": {
                "rate_up_50bp": {"pnl_pct": -2.5, "confidence": "high", "sample_count": 20},
            },
        }
        self.assertEqual(b_signal(asset), "bearish")

    def test_neutral_zero_return_no_beta_no_stress(self) -> None:
        self.assertEqual(b_signal({"returns": {"20d": 0.0}}), "neutral")

    def test_neutral_red_flag_blocks_beta(self) -> None:
        asset = {
            "returns": {"20d": 0.0},
            "beta": {"SPY": {"60d": 0.9}},
            "red_flag": {"level": "red"},
        }
        self.assertEqual(b_signal(asset), "neutral")

    def test_neutral_none_asset(self) -> None:
        self.assertEqual(b_signal(None), "neutral")

    def test_neutral_conflicting_signals(self) -> None:
        asset = {"returns": {"20d": 0.03}, "macro_stress": {
            "rate_down_50bp": {"pnl_pct": -3.0, "confidence": "high", "sample_count": 20},
        }}
        self.assertEqual(b_signal(asset), "neutral")


class DirectionTests(unittest.TestCase):
    def test_agree_combinations(self) -> None:
        self.assertEqual(classify_direction(0.01, "bullish"), "agree")
        self.assertEqual(classify_direction(-0.01, "bearish"), "agree")

    def test_diverge_combinations(self) -> None:
        self.assertEqual(classify_direction(-0.01, "bullish"), "diverge")
        self.assertEqual(classify_direction(0.01, "bearish"), "diverge")

    def test_neutral_zero_delta(self) -> None:
        self.assertEqual(classify_direction(0.0, "bullish"), "neutral")

    def test_neutral_no_signal(self) -> None:
        self.assertEqual(classify_direction(0.01, "neutral"), "neutral")


class WeightDeltaTests(unittest.TestCase):
    def test_delta_positive(self) -> None:
        self.assertAlmostEqual(weight_delta(0.55, 0.56), 0.01)

    def test_delta_negative(self) -> None:
        self.assertAlmostEqual(weight_delta(0.60, 0.55), -0.05)

    def test_delta_missing_returns_zero(self) -> None:
        self.assertEqual(weight_delta(None, 0.56), 0.0)
        self.assertEqual(weight_delta(0.55, None), 0.0)


class PerformanceTests(unittest.TestCase):
    def test_outperformed(self) -> None:
        result = post_intervention_performance(
            "2026-05", "512890.SH", MONTHLY, min_samples=5,
        )
        self.assertEqual(result["asset_pnl_pct"], 5.0)  # 512890.SH 2026-06 = +5%
        self.assertIsNotNone(result["benchmark_pnl_pct"])
        self.assertTrue(result["outperformed"])
        self.assertEqual(result["confidence"], "medium")  # 6 只资产 → 6<12 medium

    def test_underperformed(self) -> None:
        result = post_intervention_performance(
            "2026-05", "588230.SH", MONTHLY, min_samples=5,
        )
        self.assertFalse(result["outperformed"])

    def test_insufficient_samples_degrades(self) -> None:
        result = post_intervention_performance("2026-05", "512890.SH", MONTHLY, min_samples=10)
        self.assertIsNone(result["outperformed"])
        self.assertEqual(result["confidence"], "low")

    def test_no_price_degrades(self) -> None:
        result = post_intervention_performance("2026-05", "999999.SH", MONTHLY)
        self.assertIsNone(result["asset_pnl_pct"])
        self.assertIsNone(result["outperformed"])
        self.assertEqual(result["confidence"], "low")

    def test_monthly_returns_from_prices(self) -> None:
        frame = pd.DataFrame({
            "trade_date": pd.to_datetime(["2026-05-29", "2026-06-30", "2026-07-31"]),
            "close": [100.0, 105.0, 102.0],
        })
        monthly = monthly_returns_from_prices({"512890.SH": frame})
        self.assertAlmostEqual(monthly["512890.SH"]["2026-06"], 0.05)
        self.assertAlmostEqual(monthly["512890.SH"]["2026-07"], 102.0 / 105.0 - 1.0)

    def test_monthly_returns_empty_frame(self) -> None:
        monthly = monthly_returns_from_prices({"512890.SH": pd.DataFrame()})
        self.assertTrue(monthly["512890.SH"].empty)


class IntegrationTests(unittest.TestCase):
    def _load_fixture(self) -> pd.DataFrame:
        return parse_human_override_log(FIXTURE)

    def test_synthetic_full_chain(self) -> None:
        frame = self._load_fixture()
        report = build_hmc_report(frame, package=PACKAGE, monthly=MONTHLY)
        summary = report["summary"]
        # 每月 9 agree / 6 diverge / 3 neutral × 4 月。
        self.assertEqual(summary["interventions"], 72)
        self.assertEqual(summary["agree"], 36)
        self.assertEqual(summary["diverge"], 24)
        self.assertEqual(summary["neutral"], 12)
        self.assertAlmostEqual(summary["agree_rate"], 60.0)
        self.assertEqual(summary["strategies"], 3)
        self.assertEqual(summary["net_direction"], "up")
        self.assertEqual(set(report["by_strategy"]), {"strat_alpha", "strat_beta", "strat_gamma"})
        self.assertEqual(summary["performed"], 72)  # 6 只资产 → 全部可评估
        self.assertGreater(summary["effective"] + summary["ineffective"], 0)

    def test_join_semantics_shared_asset(self) -> None:
        """同一资产被多策略共享：B 维度对该资产的所有策略干预一致（共享基线）。"""
        frame = self._load_fixture()
        report = build_hmc_report(frame, package=PACKAGE, monthly=MONTHLY)
        alpha_512890 = [r for r in report["by_strategy"]["strat_alpha"] if r["asset_id"] == "512890.SH"]
        beta_512890 = [r for r in report["by_strategy"]["strat_beta"] if r["asset_id"] == "512890.SH"]
        self.assertTrue(alpha_512890)
        self.assertTrue(beta_512890)
        # 同一资产（512890.SH）在 alpha（调增）与 beta（调减）下共享 bullish 基线。
        self.assertEqual(alpha_512890[0]["signal"], "bullish")
        self.assertEqual(beta_512890[0]["signal"], "bullish")
        # 因此调增+看多→一致，调减+看多→背离。
        self.assertEqual(alpha_512890[0]["direction"], "agree")
        self.assertEqual(beta_512890[0]["direction"], "diverge")

    def test_report_renders_skeleton(self) -> None:
        frame = self._load_fixture()
        report = build_hmc_report(frame, package=PACKAGE, monthly=MONTHLY)
        md = render_hmc_markdown(report, "2026-08")
        self.assertIn("# 人机对比月报 2026-08", md)
        self.assertIn("方向一致率 60.0%", md)
        self.assertIn("## 干预明细", md)
        self.assertIn("## 方向一致性评估", md)
        self.assertIn("## 干预后表现", md)
        self.assertIn("## 宏观背景", md)
        self.assertIn("阶段 phase：mid", md)
        self.assertIn("状态：rate_up", md)
        self.assertIn("网页人工修改", md)  # reason 中文原文引用

    def test_report_no_chinese_strategy_name(self) -> None:
        """机器输出零中文策略名：中文 name 不泄漏进报告，仅 reason/标题允许中文。"""
        frame = self._load_fixture()
        report = build_hmc_report(frame, package=PACKAGE, monthly=MONTHLY)
        md = render_hmc_markdown(report, "2026-08")
        self.assertNotIn("证券ETF", md)
        self.assertNotIn("科创50", md)
        self.assertNotIn("红利低波", md)
        # 明细表 strategy_id/asset_id 列全 ASCII（逐行检查策略/资产字段）。
        for line in md.splitlines():
            if line.startswith("| 2026-") and "| strat_" in line:
                cells = [c.strip() for c in line.split("|")]
                # 明细列：… strategy_id(2) asset_id(3) …
                self.assertIsNotNone(_ASCII.fullmatch(cells[2]))
                self.assertIsNotNone(_ASCII.fullmatch(cells[3]))

    def test_month_filter(self) -> None:
        frame = self._load_fixture()
        report = build_hmc_report(frame, package=PACKAGE, monthly=MONTHLY, month="2026-06")
        self.assertEqual(report["summary"]["interventions"], 18)
        self.assertEqual(report["summary"]["agree"], 9)

    def test_no_package_degrades(self) -> None:
        frame = self._load_fixture()
        report = build_hmc_report(frame, monthly=MONTHLY)
        # 无决策包：维度缺失 → 信号中性 → 方向全部中性，不虚构。
        self.assertEqual(report["summary"]["neutral"], 72)
        self.assertIsNone(report["summary"]["agree_rate"])


if __name__ == "__main__":
    unittest.main()
