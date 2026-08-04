from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from qteasy_research.core.global_etf_engine import (
    GlobalEtfEngine,
    initialize_default_global_etf_profiles,
)
from qteasy_research.pretrade.storage import ResearchStore


class GlobalEtfEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_root = self.root / "data"
        self.macro_dir = self.data_root / "processed" / "global_macro"
        self.macro_dir.mkdir(parents=True)
        self.store = ResearchStore(self.root / "research_store")
        self.store.upsert_global_etf_definition({
            "asset_code": "SPY",
            "research_asset_code": "SPY",
            "name": "SPDR S&P 500 ETF",
        })
        self.store.upsert_global_etf_activation({
            "asset_code": "SPY",
            "horizon": "medium",
            "enabled": 1,
            "frequency": "monthly",
        })
        self._write_macro_fixture()
        self._write_asset_fixture()

    def tearDown(self) -> None:
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
        dates = pd.date_range("2024-01-01", periods=30, freq="MS")
        self._write_series("DGS30", dates, [3.0] * 29 + [3.3])
        self._write_series("DGS10", dates, [2.5] * 30)
        self._write_series("DGS2", dates, [3.0] * 30)
        self._write_series("DFII10", dates, [1.0] * 29 + [1.2])

    def _write_asset_fixture(self) -> None:
        dates = pd.date_range("2024-01-01", periods=30, freq="MS")
        self._write_series("SPY", dates, [400.0 + i * 2.0 for i in range(30)])

    def _approve_current_rules(self) -> None:
        for state, modifier in {
            "rate_up": 1.10,
            "curve_inverted": 1.00,
            "real_yield_up": 0.90,
        }.items():
            self.store.upsert_global_etf_macro_rule({
                "asset_code": "SPY",
                "macro_state": state,
                "modifier": modifier,
                "sample_start": "2024-01-01",
                "sample_end": "2026-06-30",
                "sample_count": 24,
                "confidence": "medium",
                "status": "APPROVED",
                "effective_date": "2024-01-01",
            })

    def test_dgs30_preferred_and_approved_rules_produce_score(self) -> None:
        self._approve_current_rules()
        result = GlobalEtfEngine(self.data_root, self.root / "research_store").calculate_scores(
            "2026-07-31", assets=["SPY"], persist=False
        )
        row = result.scores[0]
        self.assertEqual(result.rate_proxy, "DGS30")
        self.assertEqual(result.status, "COMPLETED")
        self.assertIsNotNone(row["macro_modifier"])
        self.assertIsNotNone(row["final_score"])
        self.assertEqual(row["status"], "COMPLETED")

    def test_default_global_universe_is_safe_and_disabled(self) -> None:
        profiles = initialize_default_global_etf_profiles(self.root / "empty_store")
        self.assertEqual({item["asset_code"] for item in profiles}, {"SPY", "TLT", "GLD"})
        self.assertTrue(all(item["enabled"] == 0 for item in profiles))

    def test_missing_approved_rule_keeps_macro_score_unavailable(self) -> None:
        result = GlobalEtfEngine(self.data_root, self.root / "research_store").calculate_scores(
            "2026-07-31", assets=["SPY"], persist=False
        )
        row = result.scores[0]
        self.assertEqual(result.status, "PARTIAL")
        self.assertIsNotNone(row["base_score"])
        self.assertIsNone(row["macro_modifier"])
        self.assertIsNone(row["final_score"])
        self.assertTrue(any("APPROVED" in warning for warning in row["warnings"]))

    def test_dgs30_falls_back_to_dgs10(self) -> None:
        (self.macro_dir / "DGS30.csv").unlink()
        result = GlobalEtfEngine(self.data_root, self.root / "research_store").calculate_scores(
            "2026-07-31", assets=["SPY"], persist=False
        )
        self.assertEqual(result.rate_proxy, "DGS10")
        self.assertTrue(any("降级" in warning for warning in result.warnings))

    def test_future_rows_are_excluded_and_a_share_factor_path_is_rejected(self) -> None:
        future = pd.DataFrame({
            "series_id": "SPY",
            "observation_date": ["2026-08-05"],
            "available_at": ["2026-08-06"],
            "value": [9999.0],
            "quality_level": ["B"],
        })
        path = self.macro_dir / "SPY.csv"
        pd.concat([pd.read_csv(path), future], ignore_index=True).to_csv(path, index=False)
        result = GlobalEtfEngine(self.data_root, self.root / "research_store").calculate_scores(
            "2026-07-31", assets=["SPY"], persist=False
        )
        self.assertEqual(result.scores[0]["data_as_of"], "2026-06-01")

        (self.root / "bad_data" / "factor_values").mkdir(parents=True)
        with self.assertRaises(RuntimeError):
            GlobalEtfEngine(self.root / "bad_data", self.root / "research_store")


if __name__ == "__main__":
    unittest.main()
