"""P3 交易资产换算逻辑（交易口径）的离线测试。

覆盖：相对费用差异、交易成本折减、汇率说明、未配置映射、研究评分不可用、
综合换算公式、样本年数折算、防呆上限与字段透传。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qteasy_research.core.global_etf_trade_conversion import (
    RESEARCH_ASSET_MANAGEMENT_FEE,
    conversion_adjustments,
    convert_research_score_to_trade,
)
from qteasy_research.core.global_etf_trade_mapping import effective_trade_mapping
from qteasy_research.pretrade.storage import ResearchStore


def _mapping(research: str = "SPY", trade: str = "513500.SH", **overrides) -> dict:
    """构造一个带默认静态汇率的 A 股 QDII 映射 payload。"""
    payload = {
        "research_asset_code": research,
        "trade_asset_code": trade,
        "trade_asset_name": f"标普500 {trade}",
        "trade_market": "CN",
        "currency": "CNY",
        "fx_pair": "USD/CNY",
        "fx_rule": "static",
        "exchange_rate": 7.10,
        "management_fee": 0.0,
        "trading_cost_bps": 0.0,
        "tracking_error": 1.2,
        "premium_discount": -0.3,
    }
    payload.update(overrides)
    return payload


def _score_row(asset: str = "SPY", **overrides) -> dict:
    """构造一行研究评分（final_score = base_score × macro_modifier = 0.864）。"""
    payload = {
        "asset": asset,
        "research_asset": asset,
        "base_score": 0.8,
        "macro_modifier": 1.08,
        "final_score": 0.864,
        "sample_count": 12,
        "frequency_used": "monthly",
        "status": "COMPLETED",
    }
    payload.update(overrides)
    return payload


class ConversionAdjustmentTests(unittest.TestCase):
    """conversion_adjustments：费用差异、成本与汇率说明。"""

    def test_fee_diff_only_extra_cost(self) -> None:
        # SPY 基准 0.09%，交易资产 0.60% → 差额 0.51%/年；样本 12 月 = 1 年
        mapping = _mapping(management_fee=0.60)
        adj = conversion_adjustments("SPY", mapping, sample_count=12, frequency_used="monthly")
        self.assertAlmostEqual(adj["fee_adjustment"], (0.60 - 0.09) / 100.0)
        self.assertEqual(adj["cost_adjustment"], 0.0)
        self.assertEqual(adj["warnings"], [])

    def test_fee_adjustment_zero_when_trade_fee_not_higher(self) -> None:
        # 交易资产费用 ≤ 研究资产基准 → 不反向加分
        mapping = _mapping(management_fee=0.05)
        adj = conversion_adjustments("SPY", mapping, sample_count=12, frequency_used="monthly")
        self.assertEqual(adj["fee_adjustment"], 0.0)

    def test_fee_adjustment_zero_when_equal(self) -> None:
        mapping = _mapping(management_fee=RESEARCH_ASSET_MANAGEMENT_FEE["SPY"])
        adj = conversion_adjustments("SPY", mapping, sample_count=12, frequency_used="monthly")
        self.assertEqual(adj["fee_adjustment"], 0.0)

    def test_cost_adjustment_from_bps(self) -> None:
        mapping = _mapping(trading_cost_bps=25)
        adj = conversion_adjustments("SPY", mapping, sample_count=12, frequency_used="monthly")
        self.assertAlmostEqual(adj["cost_adjustment"], 25 / 10000.0)

    def test_sample_years_proportional_monthly(self) -> None:
        # monthly 样本 6 个月 = 0.5 年 → 费用减半
        mapping = _mapping(management_fee=0.60)
        adj = conversion_adjustments("SPY", mapping, sample_count=6, frequency_used="monthly")
        self.assertAlmostEqual(adj["fee_adjustment"], (0.60 - 0.09) / 100.0 * 0.5)

    def test_sample_years_proportional_daily(self) -> None:
        # daily 样本 126 天 = 0.5 年；TLT 基准 0.15%
        mapping = _mapping(management_fee=0.60)
        adj = conversion_adjustments("TLT", mapping, sample_count=126, frequency_used="daily")
        self.assertAlmostEqual(adj["fee_adjustment"], (0.60 - 0.15) / 100.0 * 0.5)

    def test_sample_years_clamped_to_one(self) -> None:
        # 样本超过一年 → 最多按 1 年计费
        mapping = _mapping(management_fee=0.60)
        adj = conversion_adjustments("SPY", mapping, sample_count=100, frequency_used="monthly")
        self.assertAlmostEqual(adj["fee_adjustment"], (0.60 - 0.09) / 100.0)

    def test_sample_count_zero_means_no_fee(self) -> None:
        mapping = _mapping(management_fee=0.60)
        adj = conversion_adjustments("SPY", mapping, sample_count=0, frequency_used="monthly")
        self.assertEqual(adj["fee_adjustment"], 0.0)

    def test_adjustment_capped_when_excessive(self) -> None:
        # 费用+成本合计超过 50% → 按比例压缩到 0.5 并记警告
        mapping = _mapping(management_fee=30.0, trading_cost_bps=4000)
        adj = conversion_adjustments("SPY", mapping, sample_count=12, frequency_used="monthly")
        self.assertAlmostEqual(adj["fee_adjustment"] + adj["cost_adjustment"], 0.5)
        self.assertTrue(adj["warnings"])

    def test_fx_note_cny_static(self) -> None:
        adj = conversion_adjustments(
            "SPY", _mapping(currency="CNY", fx_rule="static", exchange_rate=7.1),
            sample_count=12, frequency_used="monthly",
        )
        self.assertIn("CNY", adj["fx_note"])
        self.assertIn("7.1000", adj["fx_note"])

    def test_fx_note_usd_direct(self) -> None:
        adj = conversion_adjustments(
            "SPY", _mapping(currency="USD", fx_rule="static", exchange_rate=1.0),
            sample_count=12, frequency_used="monthly",
        )
        self.assertIn("USD", adj["fx_note"])
        self.assertIn("无汇率差异", adj["fx_note"])

    def test_fx_note_non_static_mentions_exposure(self) -> None:
        adj = conversion_adjustments(
            "SPY", _mapping(currency="CNY", fx_rule="realtime", exchange_rate=None),
            sample_count=12, frequency_used="monthly",
        )
        self.assertIn("未对冲", adj["fx_note"])


class ConvertScoreToTradeTests(unittest.TestCase):
    """convert_research_score_to_trade：状态机与综合换算。"""

    def test_no_mapping_returns_minimal_row(self) -> None:
        row = convert_research_score_to_trade(_score_row(), None)
        self.assertEqual(row["status"], "NO_MAPPING")
        self.assertIsNone(row["trade_final_score"])
        self.assertEqual(row["asset"], "SPY")
        self.assertEqual(row["research_final_score"], 0.864)

    def test_no_research_score_returns_no_research_score(self) -> None:
        row = convert_research_score_to_trade(_score_row(final_score=None), _mapping())
        self.assertEqual(row["status"], "NO_RESEARCH_SCORE")
        self.assertIsNone(row["trade_final_score"])

    def test_converted_formula(self) -> None:
        # fee 0.6% → diff 0.51%；成本 20bps → 0.002
        mapping = _mapping(management_fee=0.60, trading_cost_bps=20)
        row = convert_research_score_to_trade(_score_row(final_score=1.0), mapping)
        expected = 1.0 * (1.0 - 0.0051 - 0.002)
        self.assertAlmostEqual(row["trade_final_score"], expected)
        self.assertEqual(row["status"], "CONVERTED")

    def test_daily_frequency_uses_252_per_year(self) -> None:
        # TLT 基准 0.15%，样本 252 天 = 1 年
        mapping = _mapping(management_fee=0.60, trading_cost_bps=0)
        row = convert_research_score_to_trade(
            _score_row(asset="TLT", final_score=1.0, sample_count=252, frequency_used="daily"),
            mapping,
        )
        expected = 1.0 * (1.0 - (0.60 - 0.15) / 100.0)
        self.assertAlmostEqual(row["trade_final_score"], expected)

    def test_passthrough_mapping_fields(self) -> None:
        mapping = _mapping(
            currency="CNY", fx_pair="USD/CNY", exchange_rate=7.1,
            management_fee=0.6, trading_cost_bps=20, tracking_error=1.2, premium_discount=-0.3,
        )
        row = convert_research_score_to_trade(_score_row(), mapping)
        self.assertEqual(row["trade_asset_code"], "513500.SH")
        self.assertEqual(row["trade_asset_name"], "标普500 513500.SH")
        self.assertEqual(row["currency"], "CNY")
        self.assertEqual(row["fx_pair"], "USD/CNY")
        self.assertEqual(row["exchange_rate"], 7.1)
        self.assertEqual(row["management_fee"], 0.6)
        self.assertEqual(row["trading_cost_bps"], 20)
        self.assertEqual(row["tracking_error"], 1.2)
        self.assertEqual(row["premium_discount"], -0.3)
        self.assertEqual(row["research_final_score"], 0.864)


class ConvertIntegrationWithStoreTests(unittest.TestCase):
    """与存储层、effective_trade_mapping 的端到端组合。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ResearchStore(self.root / "store")
        for asset in ("SPY", "TLT", "GLD"):
            self.store.upsert_global_etf_definition({
                "asset_code": asset,
                "research_asset_code": asset,
                "name": asset,
            })

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_end_to_end_with_effective_mapping(self) -> None:
        self.store.upsert_global_etf_trade_mapping(_mapping("SPY", "513500.SH", management_fee=0.6))
        mapping = effective_trade_mapping(self.store, "SPY")
        row = convert_research_score_to_trade(_score_row(), mapping)
        self.assertEqual(row["trade_asset_code"], "513500.SH")
        self.assertEqual(row["status"], "CONVERTED")
        expected = 0.864 * (1.0 - (0.6 - 0.09) / 100.0)
        self.assertAlmostEqual(row["trade_final_score"], expected)

    def test_end_to_end_without_mapping(self) -> None:
        row = convert_research_score_to_trade(
            _score_row(), effective_trade_mapping(self.store, "SPY")
        )
        self.assertEqual(row["status"], "NO_MAPPING")
        self.assertIsNone(row["trade_final_score"])


if __name__ == "__main__":
    unittest.main()
