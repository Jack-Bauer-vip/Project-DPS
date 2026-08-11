"""reference/asset_pool：资产池读取与本地行情对齐测试（合成 fixture）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from qteasy_research.reference.asset_pool import (
    align_pool_price_history,
    read_active_assets,
    report_pool_gaps,
)


class AssetPoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        dates = pd.date_range("2024-01-02", periods=60, freq="B")
        pd.DataFrame({
            "asset_id": ["000001.SZ", "164824.SZ", "MISSING.X"],
            "code": ["000001", "164824", "999999"],
            "name": ["平安银行", "测试基金", "缺失资产"],
            "type": ["stock", "fund", "fund"],
            "exchange": ["SZ", "SZ", "X"],
            "theme": ["a", "b", "c"],
            "status": ["active", "active", "active"],
        }).to_csv(self.root / "asset_pool.csv", index=False)
        fund = pd.DataFrame({
            "ts_code": ["000001.SZ"] * 60 + ["164824.SZ"] * 60,
            "trade_date": list(dates.strftime("%Y-%m-%d")) * 2,
            "close": list(10.0 + pd.Series(range(60)) * 0.1) * 2,
            "volume": [1000] * 120,
            "amount": [10000] * 120,
        })
        (self.root / "fund_daily.csv").write_text(fund.to_csv(index=False), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_read_active_assets(self) -> None:
        assets = read_active_assets(self.root / "asset_pool.csv")
        self.assertEqual(len(assets), 3)
        self.assertEqual(list(assets.columns), ["asset_id", "code", "name", "type", "exchange", "theme"])
        # 非 active 被过滤
        inactive = self.root / "pool_inactive.csv"
        pd.read_csv(self.root / "asset_pool.csv").assign(status="inactive").to_csv(inactive, index=False)
        self.assertEqual(len(read_active_assets(inactive)), 0)

    def test_read_active_assets_overrides_truncated_names(self) -> None:
        # A 侧 name 截断/错字时，read_active_assets 按 asset_id 覆盖为规范展示名。
        pool = self.root / "pool_truncated.csv"
        pd.DataFrame({
            "asset_id": ["562800.SH", "159985.SZ", "513650.SH", "000001.SZ"],
            "code": ["562800", "159985", "513650", "000001"],
            "name": ["稀有金属ETF嘉", "豆柏ETF华夏", "标普500ETF南", "平安银行"],
            "type": ["fund", "fund", "fund", "stock"],
            "exchange": ["SH", "SZ", "SH", "SZ"],
            "theme": ["a", "b", "c", "d"],
            "status": ["active", "active", "active", "active"],
        }).to_csv(pool, index=False)
        assets = read_active_assets(pool)
        by_id = {row["asset_id"]: row["name"] for _, row in assets.iterrows()}
        self.assertEqual(by_id["562800.SH"], "稀有金属ETF嘉实")
        self.assertEqual(by_id["159985.SZ"], "豆粕ETF华夏")   # 错字修正
        self.assertEqual(by_id["513650.SH"], "标普500ETF南方")  # 缺"方"补齐
        self.assertEqual(by_id["000001.SZ"], "平安银行")       # 未命中规范表 → 透传

    def test_read_active_assets_missing_file(self) -> None:
        with self.assertRaises(FileNotFoundError):
            read_active_assets(self.root / "absent.csv")

    def test_align_local_price_history(self) -> None:
        assets = read_active_assets(self.root / "asset_pool.csv")
        aligned = align_pool_price_history(assets, data_dir=self.root, online_ok=False)
        self.assertIn("000001.SZ", aligned)
        self.assertIn("164824.SZ", aligned)
        self.assertEqual(len(aligned["000001.SZ"]), 60)
        self.assertTrue(aligned["MISSING.X"].empty)  # 缺失资产返回空帧

    def test_report_gaps_marks_missing(self) -> None:
        assets = read_active_assets(self.root / "asset_pool.csv")
        aligned = align_pool_price_history(assets, data_dir=self.root, online_ok=False)
        gaps = {g["asset_id"]: g for g in report_pool_gaps(aligned)}
        self.assertFalse(gaps["MISSING.X"]["available"])
        self.assertEqual(gaps["MISSING.X"]["quality_level"], "D")
        self.assertIn("未虚构", gaps["MISSING.X"]["warning"])
        self.assertEqual(gaps["000001.SZ"]["quality_level"], "A")
