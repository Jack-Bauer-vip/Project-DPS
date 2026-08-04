"""P1 全球宏观规则：suggest_modifier 与规则落库逻辑的离线测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from qteasy_research.core.global_etf_engine import suggest_modifier_from_condition_returns as suggest
from qteasy_research.pretrade.storage import ResearchStore
from scripts.create_global_macro_rules import (
    approve_eligible_rules,
    build_rule_candidates,
    write_draft_rules,
)


class SuggestModifierTests(unittest.TestCase):
    """五档离散 modifier + 样本联动。输入为月度收益率百分点。"""

    # ---- 五档边界（偏差 = condition - baseline，单位百分点）----

    def test_strongly_positive_deviation(self) -> None:
        self.assertEqual(suggest(3.0, 1.0, 60), {"modifier": 1.15, "confidence": "high", "status": "APPROVED"})

    def test_boundary_plus_1_5(self) -> None:
        self.assertEqual(suggest(2.5, 1.0, 60)["modifier"], 1.15)   # dev = +1.5 → 强烈利好
        self.assertEqual(suggest(2.49, 1.0, 60)["modifier"], 1.08)  # dev = +1.49 → 温和利好

    def test_boundary_plus_0_5(self) -> None:
        self.assertEqual(suggest(1.5, 1.0, 60)["modifier"], 1.08)   # dev = +0.5 → 温和利好
        self.assertEqual(suggest(1.49, 1.0, 60)["modifier"], 1.00)  # dev = +0.49 → 中性

    def test_boundary_minus_0_5(self) -> None:
        self.assertEqual(suggest(0.5, 1.0, 60)["modifier"], 0.92)   # dev = -0.5 → 温和利空
        self.assertEqual(suggest(0.51, 1.0, 60)["modifier"], 1.00)  # dev = -0.49 → 中性

    def test_boundary_minus_1_5(self) -> None:
        self.assertEqual(suggest(-0.5, 1.0, 60)["modifier"], 0.85)   # dev = -1.5 → 强烈利空
        self.assertEqual(suggest(-0.49, 1.0, 60)["modifier"], 0.92)  # dev = -1.49 → 温和利空

    def test_neutral_center(self) -> None:
        self.assertEqual(suggest(1.0, 1.0, 60)["modifier"], 1.00)
        self.assertEqual(suggest(1.0, 1.0, 60)["status"], "NEUTRAL")

    def test_unit_is_percentage_points(self) -> None:
        # 输入为百分点：dev=0.01 表示 0.01 个百分点 → 中性
        result = suggest(1.01, 1.00, 100)
        self.assertEqual(result["modifier"], 1.00)
        self.assertEqual(result["status"], "NEUTRAL")

    # ---- 样本联动 ----

    def test_insufficient_sample_below_24(self) -> None:
        result = suggest(10.0, 1.0, 23)
        self.assertIsNone(result["modifier"])
        self.assertEqual(result["status"], "REFERENCE_ONLY")
        self.assertEqual(result["confidence"], "insufficient_sample")

    def test_candidate_24_to_59(self) -> None:
        self.assertEqual(suggest(10.0, 1.0, 24)["status"], "CANDIDATE")
        self.assertEqual(suggest(10.0, 1.0, 59)["status"], "CANDIDATE")
        self.assertEqual(suggest(-10.0, 1.0, 30)["status"], "CANDIDATE")

    def test_approved_at_60(self) -> None:
        self.assertEqual(suggest(10.0, 1.0, 60)["status"], "APPROVED")
        self.assertEqual(suggest(10.0, 1.0, 60)["modifier"], 1.15)

    def test_neutral_overrides_approval(self) -> None:
        # 样本足够但偏离中性 → NEUTRAL，即使 n>=60 也不 APPROVED
        result = suggest(1.1, 1.0, 100)
        self.assertEqual(result["modifier"], 1.00)
        self.assertEqual(result["status"], "NEUTRAL")

    def test_confidence_follows_deviation(self) -> None:
        self.assertEqual(suggest(3.0, 1.0, 60)["confidence"], "high")    # 强烈
        self.assertEqual(suggest(1.6, 1.0, 60)["confidence"], "medium")   # 温和
        self.assertEqual(suggest(1.2, 1.0, 60)["confidence"], "low")      # 中性


# 与真实 condition_returns.csv 一致的条件收益 fixture
CONDITION_FIXTURE = [
    ("SPY", "加息/利率上行", 52, 0.0097, "2003-06-30", "2026-07-31"),
    ("SPY", "降息/利率下行", 41, -0.0136, "2003-05-31", "2026-02-28"),
    ("SPY", "实际利率上行", 85, -0.0023, "2003-03-31", "2026-07-31"),
    ("SPY", "实际利率下行", 88, 0.0106, "2003-02-28", "2026-02-28"),
    ("TLT", "加息/利率上行", 52, -0.0483, "2003-06-30", "2026-07-31"),
    ("TLT", "降息/利率下行", 41, 0.0657, "2003-05-31", "2026-02-28"),
    ("TLT", "实际利率上行", 85, -0.0276, "2003-03-31", "2026-07-31"),
    ("TLT", "实际利率下行", 88, 0.0333, "2003-02-28", "2026-02-28"),
    ("GLD", "加息/利率上行", 47, -0.0060, "2005-07-31", "2026-07-31"),
    ("GLD", "降息/利率下行", 37, 0.0313, "2004-12-31", "2026-02-28"),
    ("GLD", "实际利率上行", 78, -0.0155, "2005-07-31", "2026-07-31"),
    ("GLD", "实际利率下行", 78, 0.0358, "2005-04-30", "2026-02-28"),
]

BASELINE_FIXTURE = {"SPY": 0.0101, "TLT": 0.0033, "GLD": 0.0094}

EXPECTED_APPROVED = {
    ("SPY", "real_yield_up"), ("TLT", "real_yield_up"),
    ("TLT", "real_yield_down"), ("GLD", "real_yield_up"), ("GLD", "real_yield_down"),
}


def _make_condition_frame() -> pd.DataFrame:
    return pd.DataFrame(CONDITION_FIXTURE, columns=[
        "asset", "macro_state", "sample_count", "monthly_return", "sample_start", "sample_end",
    ])


def _make_baselines() -> dict[str, dict[str, object]]:
    return {asset: {"baseline_monthly_return": value} for asset, value in BASELINE_FIXTURE.items()}


class RuleCandidateTests(unittest.TestCase):
    def test_build_candidates_matches_expected_table(self) -> None:
        candidates = build_rule_candidates(_make_condition_frame(), _make_baselines())
        self.assertEqual(len(candidates), 12)
        approved = {(c["asset_code"], c["macro_state"]) for c in candidates if c["suggest_status"] == "APPROVED"}
        self.assertEqual(approved, EXPECTED_APPROVED)

        by_key = {(c["asset_code"], c["macro_state"]): c for c in candidates}
        self.assertEqual(by_key[("SPY", "real_yield_up")]["modifier"], 0.92)
        self.assertEqual(by_key[("TLT", "real_yield_down")]["modifier"], 1.15)
        self.assertEqual(by_key[("SPY", "rate_down")]["modifier"], 0.85)
        self.assertEqual(by_key[("SPY", "rate_up")]["modifier"], 1.00)
        self.assertEqual(by_key[("SPY", "real_yield_down")]["suggest_status"], "NEUTRAL")

    def test_write_drafts_then_approve(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = ResearchStore(Path(td))
            candidates = build_rule_candidates(_make_condition_frame(), _make_baselines())
            written = write_draft_rules(
                store, candidates, effective_date="2026-08-03",
                include_no_data=False, include_reference_only=False,
            )
            self.assertEqual(len(written), 12)
            rules = store.list_global_etf_macro_rules()
            self.assertEqual(len(rules), 12)
            self.assertTrue(all(r["status"] == "DRAFT" for r in rules))
            self.assertTrue(all(r["effective_date"] == "2026-08-03" for r in rules))

            approved = approve_eligible_rules(store)
            self.assertEqual(len(approved), 5)
            self.assertTrue(all(r["status"] == "APPROVED" for r in approved))
            self.assertEqual(
                {(r["asset_code"], r["macro_state"]) for r in approved},
                EXPECTED_APPROVED,
            )
            remaining = store.list_global_etf_macro_rules()
            self.assertEqual(sum(1 for r in remaining if r["status"] == "DRAFT"), 7)

    def test_engine_only_reads_approved_rules(self) -> None:
        from qteasy_research.core.global_etf_engine import GlobalEtfEngine

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = ResearchStore(root / "store")
            store.upsert_global_etf_definition({
                "asset_code": "SPY", "research_asset_code": "SPY", "name": "SPDR S&P 500 ETF",
            })
            store.upsert_global_etf_activation({
                "asset_code": "SPY", "horizon": "medium", "enabled": 1, "frequency": "monthly",
            })
            # 只写一条 rate_up DRAFT（未 APPROVED），引擎必须忽略
            store.upsert_global_etf_macro_rule({
                "asset_code": "SPY", "macro_state": "rate_up", "modifier": 0.85,
                "sample_count": 52, "status": "DRAFT", "effective_date": "2024-01-01",
            })
            macro_dir = root / "data" / "processed" / "global_macro"
            macro_dir.mkdir(parents=True)
            # 两个月末数据，available_at=观测日（点内可用），使宏观状态可识别（rate_up + curve_inverted + real_yield_up）
            for series, values in {
                "DGS30": [3.0, 3.3], "DGS10": [2.5, 2.6], "DGS2": [3.0, 3.1], "DFII10": [1.0, 1.2],
            }.items():
                pd.DataFrame({
                    "observation_date": ["2024-01-31", "2024-02-29"],
                    "available_at": ["2024-01-31", "2024-02-29"],
                    "value": values, "quality_level": ["A", "A"],
                }).to_csv(macro_dir / f"{series}.csv", index=False)
            pd.DataFrame({
                "observation_date": ["2024-01-31", "2024-02-29"],
                "available_at": ["2024-01-31", "2024-02-29"],
                "value": [400.0, 410.0], "quality_level": ["B", "B"],
            }).to_csv(macro_dir / "SPY.csv", index=False)

            result = GlobalEtfEngine(root / "data", root / "store").calculate_scores(
                "2024-02-29", assets=["SPY"], persist=False
            )
            self.assertEqual(result.status, "PARTIAL")
            self.assertIsNone(result.scores[0]["macro_modifier"])
            self.assertTrue(any("APPROVED" in w for w in result.scores[0]["warnings"]))


if __name__ == "__main__":
    unittest.main()
