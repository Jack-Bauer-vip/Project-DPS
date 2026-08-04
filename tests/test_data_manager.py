from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from qteasy_research.pretrade.data_manager import DataManager, DataSyncRequest
from qteasy_research.pretrade.factor_scoring import calculate_factor_scores
from qteasy_research.pretrade.providers import ProviderData
from qteasy_research.pretrade.storage import ResearchStore


class DataManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="qteasy_data_manager_", dir="D:/Project DPS"))
        self.data_dir = self.root / "source"
        self.data_dir.mkdir()
        rows = []
        dates = pd.date_range("2025-01-01", periods=70, freq="D")
        for code, multiplier in (("510300.SH", 1.0), ("510500.SH", 1.5)):
            for index, day in enumerate(dates):
                close = multiplier * (100 + index)
                rows.append({
                    "ts_code": code,
                    "trade_date": day.strftime("%Y-%m-%d"),
                    "open": close,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                    "pre_close": close - 1,
                    "vol": 1000,
                    "amount": close * 1000,
                })
        pd.DataFrame(rows).to_csv(self.data_dir / "fund_daily.csv", index=False)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_import_is_idempotent_and_queryable(self) -> None:
        manager = DataManager(self.root, data_dir=self.data_dir)
        first = manager.import_existing_local_data()
        second = manager.import_existing_local_data()
        self.assertEqual(first.status, "COMPLETED")
        self.assertGreater(first.rows_added, 0)
        self.assertEqual(second.rows_added, 0)
        queried = manager.query_data("market", code="510300.SH", limit=500)
        self.assertEqual(len(queried.rows), 70)
        self.assertEqual(queried.rows["trade_date"].min(), "2025-01-01")

    def test_provider_fallback_records_attempts_and_persists_data(self) -> None:
        manager = DataManager(self.root, data_dir=self.data_dir)
        frame = pd.DataFrame([
            {"trade_date": "2025-02-01", "close": 101.0, "open": 100.0, "high": 102.0, "low": 99.0}
        ])

        class FailingProvider:
            name = "akshare"

            def get_price_history(self, identity):
                raise RuntimeError("fixture failure")

        class WorkingProvider:
            name = "tushare"

            def get_price_history(self, identity):
                return ProviderData(data=frame, source=self.name, as_of="2025-02-01", message="fixture")

        with patch.object(manager, "_providers", return_value=[FailingProvider(), WorkingProvider()]):
            result = manager.sync_data(DataSyncRequest(codes=["510300.SH"], start="2025-02-01", end="2025-02-01"))
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(result.rows_added, 1)
        self.assertEqual([item["source"] for item in result.attempts], ["akshare", "tushare"])
        self.assertEqual(manager.store.latest_market_date("510300.SH"), "2025-02-01")

    def test_build_factor_values_can_be_consumed_by_score_engine(self) -> None:
        manager = DataManager(self.root, data_dir=self.data_dir)
        manager.import_existing_local_data()
        store = ResearchStore(self.root)
        store.upsert_factor_definition({
            "factor_id": "momentum_60d",
            "name": "60日动量",
            "category": "momentum",
            "formula": "close.pct_change(60)",
            "formula_hash": "fixture",
            "direction": 1,
            "supported_asset_types": ["ETF"],
            "value_scope": "asset",
            "status": "ACTIVE",
        })
        store.upsert_factor_activation({
            "factor_id": "momentum_60d",
            "asset_type": "ETF",
            "horizon": "medium",
            "enabled": 1,
            "weight": 1.0,
        })
        built = manager.build_factor_values(
            ["momentum_60d"], asset_type="ETF", horizon="medium", codes=["510300.SH", "510500.SH"], as_of_date="2025-03-11"
        )
        self.assertEqual(built.status, "COMPLETED")
        self.assertGreater(built.row_count, 0)
        manifest = json.loads(Path(built.manifest_path).read_text(encoding="utf-8"))
        self.assertEqual(manifest["value_semantics"], "asset_exposure")
        result = calculate_factor_scores(
            "2025-03-11", "ETF", "medium", universe=["510300.SH", "510500.SH"], store_root=self.root
        )
        self.assertEqual(result.coverage["factor_count"], 1)
        self.assertFalse(any("因子数据不存在" in warning for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
