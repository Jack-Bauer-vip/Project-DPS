from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from qteasy_research.factor_lab import (
    analyze_factor_collinearity,
    analyze_factor_decay,
    estimate_exposure,
    match_factors,
    research_factor,
)
from qteasy_research.pretrade.factor_data import filter_point_in_time
from qteasy_research.pretrade.factor_research import estimate_transaction_cost
from qteasy_research.pretrade.schemas import DataQualityMetadata, TransactionCostConfig
from qteasy_research.pretrade.storage import ResearchStore
from qteasy_research.macro.scoring import MacroScoringSystem


class FactorLabTests(unittest.TestCase):
    def test_point_in_time_filter_excludes_late_release(self) -> None:
        frame = pd.DataFrame(
            {
                "factor_id": ["pmi", "pmi"],
                "observation_date": ["2023-01-01", "2023-02-01"],
                "available_at": ["2023-01-10", "2023-02-20"],
                "value": [50.0, 51.0],
            }
        )
        filtered = filter_point_in_time(frame, "2023-02-15")
        self.assertEqual(filtered["value"].tolist(), [50.0])

    def test_known_leading_factor_has_positive_ic_without_lookahead(self) -> None:
        index = pd.date_range("2020-01-01", periods=50, freq="D")
        factor = pd.Series(np.arange(50, dtype=float), index=index)
        # actual return at t+1 is determined by factor at t
        returns = pd.Series([0.0] + list(factor.iloc[:-1] / 100), index=index)
        result = research_factor(
            "synthetic_momentum", factor=factor, forward_returns=returns,
            forward_period=1, min_samples=24,
            data_quality=DataQualityMetadata(
                source="fixture", release_date="2020-01-01", available_at="2020-01-01",
                vintage_id="v1", completeness=1.0, quality_level="A", quality_score=1.0,
            ),
        )
        self.assertEqual(result.status, "ok")
        self.assertGreater(result.ic or 0, 0.99)
        self.assertGreater(result.rank_ic or 0, 0.99)
        self.assertIsNotNone(result.net_return)

    def test_cost_model_does_not_apply_stamp_tax_to_etf_by_default(self) -> None:
        etf = estimate_transaction_cost(100000, 10000000, asset_type="ETF")
        stock = estimate_transaction_cost(100000, 10000000, asset_type="STOCK")
        self.assertEqual(etf["stamp_tax_rate"], 0.0)
        self.assertGreater(stock["stamp_tax_rate"] or 0, 0)
        self.assertGreater(etf["cost_amount"] or 0, 0)

    def test_collinearity_reports_cluster_and_vif_action(self) -> None:
        frame = pd.DataFrame({"growth": range(30), "growth_copy": range(30), "independent": [i % 3 for i in range(30)]})
        result = analyze_factor_collinearity(frame)
        self.assertTrue(result["high_correlation_pairs"])
        self.assertTrue(any(set(cluster) >= {"growth", "growth_copy"} for cluster in result["clusters"]))
        self.assertIn("growth", result["requires_action"])

    def test_decay_warning_and_exposure_matching(self) -> None:
        history = pd.Series([0.02] * 8 + [-0.1] * 6)
        decay = analyze_factor_decay(history, data_quality_level="C")
        self.assertEqual(decay["status"], "degraded")
        index = pd.date_range("2020-01-01", periods=40, freq="D")
        factor = pd.Series(np.linspace(-1, 1, 40), index=index)
        returns = factor * 0.02
        exposure = estimate_exposure(returns, factor, asset_code="518880.SH", factor_id="gold_real_rate", min_samples=24)
        self.assertEqual(exposure.status, "ok")
        self.assertEqual(exposure.exposure_direction, "positive")
        match = match_factors("518880.SH", [exposure], {"gold_real_rate": 1.0})
        self.assertGreater(match.score or 0, 50)
        self.assertTrue(match.supporting_factors)

    def test_factor_research_is_persisted_as_a_new_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            index = pd.date_range("2020-01-01", periods=30, freq="D")
            factor = pd.Series(np.arange(30, dtype=float), index=index)
            returns = pd.Series([0.0] + list(factor.iloc[:-1] / 100), index=index)
            result = research_factor(
                "fixture_factor", factor=factor, forward_returns=returns,
                store_root=Path(temp), as_of_date="2020-02-01", min_samples=10,
            )
            self.assertEqual(result.factor_id, "fixture_factor")
            rows = ResearchStore(temp).list_factor_research("fixture_factor")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["payload"]["factor_id"], "fixture_factor")

    def test_macro_insufficient_state_is_not_scored_as_neutral(self) -> None:
        scorer = MacroScoringSystem(weights={"PMI": 1.0}, forward_period=1)
        factor_data = {"PMI": pd.Series([50.0] * 5, index=pd.date_range("2020-01-31", periods=5, freq="ME"))}
        asset_monthly = pd.DataFrame({"gold": [0.01] * 5}, index=factor_data["PMI"].index)
        tables = scorer.build_prob_tables(factor_data, asset_monthly, include_metadata=True)
        result = scorer.score({"PMI": {"state": "neutral", "latest_value": 50}}, tables)
        self.assertIsNone(result["composite_prob"])
        self.assertEqual(result["action"], "数据不足")


if __name__ == "__main__":
    unittest.main()
