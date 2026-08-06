"""P2 宏观规则状态体系（BASELINE/PROVISIONAL）回归测试。

覆盖：状态枚举校验、有效规则查询优先级（APPROVED > PROVISIONAL > BASELINE）、
状态机转换（promote/demote/approve），以及"常态兜底+临时规则参与评分后引擎
能产出 final_score"（原"无数据状态阻断评分链"问题的回归防护）。

全部使用临时研究库与临时数据 fixture，不依赖真实 data 目录。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from qteasy_research.core.global_etf_engine import GlobalEtfEngine
from qteasy_research.pretrade.storage import ResearchStore

TARGET_DATE = "2026-07-31"


class MacroRuleStatusTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_root = self.root / "data"
        self.macro_dir = self.data_root / "processed" / "global_macro"
        self.macro_dir.mkdir(parents=True)
        self.store = ResearchStore(self.root / "research_store")
        self.store.upsert_global_etf_definition({
            "asset_code": "SPY", "research_asset_code": "SPY", "name": "SPDR S&P 500 ETF",
        })
        self.store.upsert_global_etf_activation({
            "asset_code": "SPY", "horizon": "medium", "enabled": 1, "frequency": "monthly",
        })
        self._write_macro_fixture()
        self._write_asset_fixture()

    def tearDown(self):
        self.temp.cleanup()

    def _write_series(self, series_id: str, dates: pd.DatetimeIndex, values: list[float]) -> None:
        frame = pd.DataFrame({
            "series_id": series_id,
            "observation_date": dates.strftime("%Y-%m-%d"),
            "available_at": (dates + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            "value": values,
            "quality_level": "A" if series_id.startswith("DGS") or series_id == "DFII10" else "B",
        })
        frame.to_csv(self.macro_dir / f"{series_id}.csv", index=False)

    def _write_macro_fixture(self) -> None:
        """月末跳升构造宏观三元组 rate_up + curve_normal + real_yield_up。"""
        dates = pd.date_range("2024-01-01", periods=30, freq="MS")
        self._write_series("DGS30", dates, [3.0] * 29 + [3.3])   # 末月 +0.3 → rate_up
        self._write_series("DGS10", dates, [3.0] * 30)
        self._write_series("DGS2", dates, [2.5] * 30)            # 10Y-2Y=0.5 → curve_normal
        self._write_series("DFII10", dates, [1.0] * 29 + [1.2])  # 末月 +0.2 → real_yield_up

    def _write_asset_fixture(self) -> None:
        dates = pd.date_range("2024-01-01", periods=30, freq="MS")
        self._write_series("SPY", dates, [400.0 + i * 2.0 for i in range(30)])

    def _upsert(self, state: str, modifier: float, sample: int, status: str, confidence: str = "low") -> int:
        rule = self.store.upsert_global_etf_macro_rule({
            "asset_code": "SPY",
            "macro_state": state,
            "modifier": modifier,
            "sample_count": sample,
            "confidence": confidence,
            "status": status,
            "effective_date": "2024-01-01",
        })
        return rule["rule_id"]

    def test_status_values_accept_new_states(self):
        self._upsert("curve_normal", 1.00, 0, "BASELINE", "baseline")
        self._upsert("rate_up", 0.98, 52, "PROVISIONAL")
        rules = {r["macro_state"]: r["status"] for r in self.store.list_global_etf_macro_rules()}
        self.assertEqual(rules["curve_normal"], "BASELINE")
        self.assertEqual(rules["rate_up"], "PROVISIONAL")

    def test_invalid_status_rejected(self):
        with self.assertRaises(ValueError):
            self._upsert("rate_up", 1.0, 52, "NOT_A_STATUS")

    def test_effective_rules_priority(self):
        # 同一 (asset,state) 同时存在 PROVISIONAL 与 BASELINE 时取 PROVISIONAL。
        self._upsert("rate_up", 0.98, 52, "PROVISIONAL")
        self._upsert("curve_normal", 1.00, 0, "BASELINE", "baseline")
        rules = self.store.get_effective_global_etf_macro_rules(
            asset_code="SPY", macro_states=["rate_up", "curve_normal"], target_date=TARGET_DATE
        )
        by_state = {r["macro_state"]: r for r in rules}
        self.assertEqual(by_state["rate_up"]["status"], "PROVISIONAL")
        self.assertEqual(by_state["curve_normal"]["status"], "BASELINE")

        # APPROVED 优先于 PROVISIONAL。
        self._upsert("rate_up", 0.95, 80, "APPROVED")
        rules = self.store.get_effective_global_etf_macro_rules(
            asset_code="SPY", macro_states=["rate_up"], target_date=TARGET_DATE
        )
        self.assertEqual(rules[0]["status"], "APPROVED")
        self.assertEqual(rules[0]["modifier"], 0.95)

        # 完全没有规则的状态不出现在结果里（由引擎判断为阻断）。
        rules = self.store.get_effective_global_etf_macro_rules(
            asset_code="SPY", macro_states=["curve_inverted"], target_date=TARGET_DATE
        )
        self.assertEqual(rules, [])

    def test_state_machine_promote_demote(self):
        rid = self._upsert("rate_up", 0.98, 52, "DRAFT")
        self.assertEqual(self.store.promote_global_etf_macro_rule(rid, note="临时")["status"], "PROVISIONAL")
        self.assertEqual(self.store.demote_global_etf_macro_rule(rid, note="退回")["status"], "DRAFT")
        # PROVISIONAL 可确认升级。
        self.store.promote_global_etf_macro_rule(rid, note="临时")
        self.assertEqual(self.store.approve_global_etf_macro_rule(rid, note="确认")["status"], "APPROVED")
        # DRAFT 也可人工确认（人工 override 样本门槛）。
        rid2 = self._upsert("rate_up", 0.9, 52, "DRAFT")
        self.assertEqual(self.store.approve_global_etf_macro_rule(rid2)["status"], "APPROVED")

    def test_engine_completed_with_baseline_and_provisional(self):
        """原死胡同回归：rate_up(样本不足)+curve_normal(无数据) 应能算出 final_score。"""
        self._upsert("rate_up", 1.10, 52, "PROVISIONAL")
        self._upsert("curve_normal", 1.00, 0, "BASELINE", "baseline")
        self._upsert("real_yield_up", 0.90, 85, "APPROVED")
        result = GlobalEtfEngine(self.data_root, self.root / "research_store").calculate_scores(
            TARGET_DATE, assets=["SPY"], persist=False
        )
        row = result.scores[0]
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(row["status"], "COMPLETED")
        self.assertIsNotNone(row["macro_modifier"])
        self.assertIsNotNone(row["final_score"])
        warns = " ".join(row["warnings"])
        self.assertIn("临时规则", warns)
        self.assertIn("常态基准", warns)

    def test_engine_still_partial_without_any_rule(self):
        """无任何规则时仍阻断（保持安全语义），不让缺失被自动当作中性。"""
        self._upsert("real_yield_up", 0.90, 85, "APPROVED")
        result = GlobalEtfEngine(self.data_root, self.root / "research_store").calculate_scores(
            TARGET_DATE, assets=["SPY"], persist=False
        )
        row = result.scores[0]
        self.assertEqual(result.status, "PARTIAL")
        self.assertEqual(row["status"], "PARTIAL")
        self.assertIsNone(row["final_score"])
        self.assertTrue(any("APPROVED" in w for w in row["warnings"]))


if __name__ == "__main__":
    unittest.main()
