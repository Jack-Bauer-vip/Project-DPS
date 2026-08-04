from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from qteasy_research.pretrade.factor_scoring import (
    calculate_factor_scores,
    monitor_factor_long_short,
)
from qteasy_research.pretrade.storage import ResearchStore


def _write_factor(root: Path, factor_id: str, rows: list[dict], *, semantics: str = "neutralized_exposure") -> Path:
    directory = root / "data" / "factor_values"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{factor_id}.parquet"
    pd.DataFrame(rows).to_parquet(path, index=False)
    (directory / f"{factor_id}.manifest.json").write_text(
        json.dumps({"factor_id": factor_id, "value_semantics": semantics}), encoding="utf-8"
    )
    return path


class FactorScoreTests(unittest.TestCase):
    def _store_with_factor(self, root: Path, *, asset_type: str = "STOCK", value_scope: str = "asset") -> ResearchStore:
        store = ResearchStore(root)
        store.upsert_factor_definition({
            "factor_id": "momentum_60d",
            "name": "60日动量",
            "category": "momentum",
            "formula": "close.pct_change(60)",
            "formula_hash": "fixture-hash",
            "direction": 1,
            "default_horizon": "medium",
            "supported_asset_types": [asset_type],
            "value_scope": value_scope,
            "missing_policy": "exclude",
            "status": "ACTIVE",
        })
        store.upsert_factor_activation({
            "factor_id": "momentum_60d",
            "asset_type": asset_type,
            "horizon": "medium",
            "enabled": 1,
            "weight": 1.0,
        })
        return store

    def test_no_enabled_profile_returns_configuration_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = self._store_with_factor(root)
            store.upsert_factor_activation({
                "factor_id": "momentum_60d", "asset_type": "ETF", "horizon": "medium",
                "enabled": 0, "weight": 1.0,
            })
            result = calculate_factor_scores(
                "2025-01-01", "ETF", "medium", universe=["510300.SH"], store_root=root,
            )
            self.assertTrue(any(item["code"] == "NO_ENABLED_PROFILE" for item in result.diagnostics))

    def test_parquet_fixture_and_strict_point_in_time_score(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._store_with_factor(root)
            rows = [
                {"date": "2025-01-01", "asset_code": code, "value": value, "available_at": "2025-01-01"}
                for code, value in [("000001.SZ", 1.0), ("000002.SZ", 2.0), ("000003.SZ", 3.0)]
            ]
            rows += [
                {"date": "2025-01-02", "asset_code": code, "value": value + 10, "available_at": "2025-01-10"}
                for code, value in [("000001.SZ", 1.0), ("000002.SZ", 2.0), ("000003.SZ", 3.0)]
            ]
            path = _write_factor(root, "momentum_60d", rows)
            self.assertEqual(len(pd.read_parquet(path)), 6)
            result = calculate_factor_scores(
                "2025-01-05", "STOCK", "medium",
                universe=["000001.SZ", "000002.SZ", "000003.SZ"], store_root=root,
            )
            self.assertEqual(result.coverage["factor_coverage"]["momentum_60d"], 3)
            scores = {row["asset_code"]: row["composite_score"] for row in result.scores}
            self.assertLess(scores["000001.SZ"], scores["000002.SZ"])
            self.assertLess(scores["000002.SZ"], scores["000003.SZ"])
            self.assertTrue(Path(result.csv_path or "").exists())

            rows.append({"date": "2025-01-06", "asset_code": "000001.SZ", "value": 999, "available_at": "2025-01-06"})
            _write_factor(root, "momentum_60d", rows)
            unchanged = calculate_factor_scores(
                "2025-01-05", "STOCK", "medium",
                universe=["000001.SZ", "000002.SZ", "000003.SZ"], store_root=root,
            )
            self.assertEqual(scores, {row["asset_code"]: row["composite_score"] for row in unchanged.scores})

    def test_complex_missing_policy_falls_back_to_exclude(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = self._store_with_factor(root)
            store.upsert_factor_definition({
                "factor_id": "momentum_60d", "name": "60日动量", "category": "momentum",
                "formula": "close.pct_change(60)", "formula_hash": "fixture-hash", "direction": 1,
                "supported_asset_types": ["STOCK"], "missing_policy": "industry_median", "status": "ACTIVE",
            })
            _write_factor(root, "momentum_60d", [
                {"date": "2025-01-01", "asset_code": "000001.SZ", "value": 1, "available_at": "2025-01-01"},
                {"date": "2025-01-01", "asset_code": "000002.SZ", "value": 2, "available_at": "2025-01-01"},
            ])
            result = calculate_factor_scores(
                "2025-01-01", "STOCK", "medium", universe=["000001.SZ", "000002.SZ", "000003.SZ"], store_root=root,
            )
            self.assertTrue(any("降级为排除处理" in warning for warning in result.warnings))

    def test_etf_underlying_mapping_is_used_for_underlying_factor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = self._store_with_factor(root, asset_type="ETF", value_scope="underlying")
            store.upsert_etf_underlying_mapping({
                "etf_code": "510300.SH", "underlying_type": "INDEX", "underlying_code": "000300.SH",
                "effective_date": "2025-01-01", "source": "fixture",
            })
            _write_factor(root, "momentum_60d", [
                {"date": "2025-01-01", "asset_code": "000300.SH", "value": 2, "available_at": "2025-01-01"},
            ])
            result = calculate_factor_scores(
                "2025-01-01", "ETF", "medium", universe=["510300.SH"], store_root=root,
            )
            self.assertEqual(result.coverage["factor_coverage"]["momentum_60d"], 1)

    def test_missing_factor_file_returns_structured_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._store_with_factor(root)
            result = calculate_factor_scores(
                "2025-01-01", "STOCK", "medium", universe=["000001.SZ"], store_root=root,
            )
            codes = {item["code"] for item in result.diagnostics}
            self.assertIn("FACTOR_FILE_MISSING", codes)
            self.assertIn("000001.SZ", result.scores[0]["asset_code"])
            self.assertEqual(result.scores[0]["availability_status"], "不可用")

    def test_invalid_manifest_returns_manifest_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._store_with_factor(root)
            factor_dir = root / "data" / "factor_values"
            factor_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame([
                {"date": "2025-01-01", "asset_code": "000001.SZ", "value": 1, "available_at": "2025-01-01"},
            ]).to_parquet(factor_dir / "momentum_60d.parquet", index=False)
            (factor_dir / "momentum_60d.manifest.json").write_text("{}", encoding="utf-8")
            result = calculate_factor_scores(
                "2025-01-01", "STOCK", "medium", universe=["000001.SZ"], store_root=root,
            )
            self.assertTrue(any(item["code"] == "MANIFEST_MISSING_OR_INVALID" for item in result.diagnostics))

    def test_missing_point_in_time_data_returns_asset_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._store_with_factor(root)
            _write_factor(root, "momentum_60d", [
                {"date": "2025-02-01", "asset_code": "000001.SZ", "value": 1, "available_at": "2025-02-01"},
            ])
            result = calculate_factor_scores(
                "2025-01-01", "STOCK", "medium", universe=["000001.SZ"], store_root=root,
            )
            diagnostics = result.asset_diagnostics["000001.SZ"]
            self.assertTrue(any(item["code"] == "NO_POINT_IN_TIME_DATA" for item in diagnostics))

    def test_asset_not_in_factor_data_returns_asset_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._store_with_factor(root)
            _write_factor(root, "momentum_60d", [
                {"date": "2025-01-01", "asset_code": "000002.SZ", "value": 1, "available_at": "2025-01-01"},
            ])
            result = calculate_factor_scores(
                "2025-01-01", "STOCK", "medium", universe=["000001.SZ"], store_root=root,
            )
            self.assertTrue(any(item["code"] == "ASSET_NOT_IN_FACTOR_DATA" for item in result.diagnostics))

    def test_stock_preview_with_etf_codes_returns_mismatch_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = calculate_factor_scores(
                "2025-01-01", "STOCK", "medium", universe=["518880.SH"], store_root=root,
            )
            self.assertTrue(any(item["code"] == "ASSET_TYPE_MISMATCH" for item in result.diagnostics))

    def test_factor_drawdown_suspends_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = self._store_with_factor(root)
            profile = store.list_factor_activations(asset_type="STOCK", horizon="medium")[0]
            factor_values = pd.DataFrame([
                {"date": "2025-01-01", "asset_code": "A", "value": 1},
                {"date": "2025-01-01", "asset_code": "B", "value": 2},
                {"date": "2025-01-01", "asset_code": "C", "value": 3},
                {"date": "2025-01-02", "asset_code": "A", "value": 1},
                {"date": "2025-01-02", "asset_code": "B", "value": 2},
                {"date": "2025-01-02", "asset_code": "C", "value": 3},
            ])
            returns = pd.DataFrame([
                {"date": "2025-01-01", "asset_code": "A", "forward_return": 0.0},
                {"date": "2025-01-01", "asset_code": "B", "forward_return": 0.0},
                {"date": "2025-01-01", "asset_code": "C", "forward_return": 0.0},
                {"date": "2025-01-02", "asset_code": "A", "forward_return": 0.1},
                {"date": "2025-01-02", "asset_code": "B", "forward_return": 0.0},
                {"date": "2025-01-02", "asset_code": "C", "forward_return": -0.5},
            ])
            result = monitor_factor_long_short(
                "momentum_60d", factor_values, returns, store_root=root, as_of="2025-01-02",
                profile_id=profile["profile_id"],
            )
            self.assertEqual(result["status"], "SUSPENDED")
            self.assertEqual(store.list_factor_activations(asset_type="STOCK", horizon="medium")[0]["status"], "SUSPENDED")
            store.restore_factor(profile["profile_id"])
            self.assertEqual(store.list_factor_activations(asset_type="STOCK", horizon="medium")[0]["status"], "ENABLED")


if __name__ == "__main__":
    unittest.main()
