from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from qteasy_research.factor_lab import import_factor_research_result
from qteasy_research.pretrade.factor_scoring import calculate_factor_scores
from qteasy_research.pretrade.storage import ResearchStore


def write_factor(root: Path, factor_id: str, values: list[float]) -> None:
    factor_dir = root / "data" / "factor_values"
    factor_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {"date": "2025-01-01", "asset_code": f"A{i}", "value": value, "available_at": "2025-01-01"}
        for i, value in enumerate(values, start=1)
    ]
    path = factor_dir / f"{factor_id}.parquet"
    pd.DataFrame(rows).to_parquet(path, index=False)
    (factor_dir / f"{factor_id}.manifest.json").write_text(
        json.dumps({"factor_id": factor_id, "value_semantics": "neutralized_exposure"}),
        encoding="utf-8",
    )


class FactorAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ResearchStore(self.root)
        for factor_id in ("momentum_60d", "momentum_120d", "low_volatility_20d"):
            self.store.upsert_factor_definition({
                "factor_id": factor_id,
                "name": factor_id,
                "category": "momentum",
                "formula": factor_id,
                "formula_hash": factor_id,
                "direction": 1,
                "supported_asset_types": ["ETF"],
                "status": "ACTIVE",
            })
        self.store.upsert_factor_activation({
            "factor_id": "momentum_60d", "asset_type": "ETF", "horizon": "medium",
            "enabled": 1, "weight": 1.0,
        })
        self.store.upsert_factor_activation({
            "factor_id": "momentum_120d", "asset_type": "ETF", "horizon": "medium",
            "enabled": 1, "weight": 0.15,
        })
        self.store.upsert_factor_activation({
            "factor_id": "low_volatility_20d", "asset_type": "ETF", "horizon": "medium",
            "enabled": 0, "weight": 1.0,
        })
        write_factor(self.root, "momentum_60d", [1.0, 2.0, 3.0])
        write_factor(self.root, "momentum_120d", [3.0, 1.0, 1.0])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_two_factor_scores_and_weight_change(self) -> None:
        first = calculate_factor_scores(
            "2025-01-01", "ETF", "medium", universe=["A1", "A2", "A3"], store_root=self.root
        )
        self.assertEqual(first.coverage["factor_count"], 2)
        self.assertIn("score_momentum_60d", first.scores[0])
        self.assertIn("score_momentum_120d", first.scores[0])
        first_order = [row["asset_code"] for row in sorted(first.scores, key=lambda row: row["composite_score"], reverse=True)]

        self.store.upsert_factor_activation({
            "factor_id": "momentum_120d", "asset_type": "ETF", "horizon": "medium",
            "enabled": 1, "weight": 2.0,
        })
        second = calculate_factor_scores(
            "2025-01-01", "ETF", "medium", universe=["A1", "A2", "A3"], store_root=self.root
        )
        second_order = [row["asset_code"] for row in sorted(second.scores, key=lambda row: row["composite_score"], reverse=True)]
        self.assertNotEqual(first_order, second_order)
        for weights in second.normalized_weights.values():
            self.assertAlmostEqual(sum(abs(value) for value in weights.values()), 1.0)

    def test_missing_factor_keeps_remaining_factor_and_normalizes(self) -> None:
        factor_path = self.root / "data" / "factor_values" / "momentum_120d.parquet"
        factor_path.unlink()
        result = calculate_factor_scores(
            "2025-01-01", "ETF", "medium", universe=["A1", "A2", "A3"], store_root=self.root
        )
        self.assertEqual(result.coverage["factor_count"], 1)
        self.assertIn("score_momentum_60d", result.scores[0])
        self.assertNotIn("score_momentum_120d", result.scores[0])
        for weights in result.normalized_weights.values():
            self.assertEqual(list(weights), ["momentum_60d"])
            self.assertAlmostEqual(weights["momentum_60d"], 1.0)

    def test_disabled_factor_is_not_in_score(self) -> None:
        result = calculate_factor_scores(
            "2025-01-01", "ETF", "medium", universe=["A1", "A2", "A3"], store_root=self.root
        )
        self.assertNotIn("low_volatility_20d", result.factor_details)

    def test_import_research_summary_can_approve_factor(self) -> None:
        imported = import_factor_research_result(
            "momentum_120d",
            {"sample_count": 120, "ic": 0.08, "rank_ic": 0.11, "net_return": 0.04},
            store_root=self.root,
            approve=True,
        )
        self.assertEqual(imported["status"], "APPROVED")
        definition = next(item for item in self.store.list_factor_definitions() if item["factor_id"] == "momentum_120d")
        self.assertEqual(definition["research_summary"]["rank_ic"], 0.11)


if __name__ == "__main__":
    unittest.main()
