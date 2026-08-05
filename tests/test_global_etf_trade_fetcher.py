"""交易资产信息抓取模块（core/global_etf_trade_fetcher）的离线测试。

覆盖：本地 fund_basic 名称/管理费读取、Tushare 回退（mock）、折溢价抓取
（mock AKShare）、跟踪误差计算、数据缺失时的 None 回退与 notes 提示。
网络接口一律 mock 或离线，不依赖真实联网。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from qteasy_research.core.global_etf_trade_fetcher import (
    TradeAssetFetchResult,
    compute_tracking_error,
    fetch_local_fund_basic,
    fetch_premium_discount_akshare,
    fetch_trade_asset_info,
    fetch_tushare_fund_basic,
)


def _write_fund_basic(data_root: Path) -> None:
    pd.DataFrame([
        {"ts_code": "518880.SH", "name": "华安黄金ETF", "m_fee": 0.5, "benchmark": "黄金现货价格收益率(Au99.99合约)×100%"},
        {"ts_code": "513500.SH", "name": "标普500ETF", "m_fee": 0.6, "benchmark": "中证800指数收益率×100%"},
    ]).to_csv(data_root / "fund_basic.csv", index=False)


def _write_fund_daily(data_root: Path) -> None:
    dates = pd.date_range("2025-01-01", periods=260, freq="D")
    # 基金日收益围绕 0.1% 有真实波动（sin 叠加），确保跟踪误差非零
    rng = pd.Series(range(260))
    daily_ret = 0.001 + 0.001 * (rng % 5 == 0).astype(float)  # 每 5 天一个 0.2% 跳变
    close = (1.0 + daily_ret).cumprod()
    pd.DataFrame({
        "ts_code": "513500.SH",
        "trade_date": dates.strftime("%Y-%m-%d"),
        "close": close.values,
    }).to_csv(data_root / "fund_daily.csv", index=False)


def _write_index_daily(data_root: Path) -> None:
    dates = pd.date_range("2025-01-01", periods=260, freq="D")
    # 指数日收益恒定 0.05%，与基金差异有波动 → 产生非零跟踪误差
    close = (1.0 + 0.0005) ** pd.Series(range(260))
    pd.DataFrame({
        "ts_code": "000300.SH",
        "trade_date": dates.strftime("%Y-%m-%d"),
        "close": close.values,
    }).to_csv(data_root / "index_daily.csv", index=False)


class LocalFundBasicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        _write_fund_basic(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_fetch_local_fund_basic_returns_name_and_fee(self) -> None:
        result = fetch_local_fund_basic("518880.SH", self.root)
        self.assertEqual(result["name"], "华安黄金ETF")
        self.assertEqual(result["management_fee"], 0.5)

    def test_fetch_local_fund_basic_case_insensitive(self) -> None:
        result = fetch_local_fund_basic("518880.sh", self.root)
        self.assertEqual(result["name"], "华安黄金ETF")

    def test_fetch_local_fund_basic_missing_returns_none(self) -> None:
        self.assertIsNone(fetch_local_fund_basic("999999.SH", self.root))

    def test_fetch_local_fund_basic_missing_file(self) -> None:
        empty = Path(self.temp.name) / "empty"
        empty.mkdir()
        self.assertIsNone(fetch_local_fund_basic("518880.SH", empty))


class TushareFundBasicTests(unittest.TestCase):
    @unittest.skipUnless(False, "Tushare 联网测试在离线 CI 中跳过")
    def test_fetch_tushare_network(self) -> None:
        result = fetch_tushare_fund_basic("518880.SH")
        self.assertTrue(result is None or "name" in result)

    def test_fetch_tushare_no_token_returns_none(self) -> None:
        import os
        old = os.environ.get("TUSHARE_TOKEN")
        os.environ.pop("TUSHARE_TOKEN", None)
        try:
            self.assertIsNone(fetch_tushare_fund_basic("518880.SH"))
        finally:
            if old is not None:
                os.environ["TUSHARE_TOKEN"] = old


class PremiumDiscountTests(unittest.TestCase):
    @unittest.skipUnless(False, "AKShare 联网测试在离线 CI 中跳过")
    def test_fetch_premium_network(self) -> None:
        value = fetch_premium_discount_akshare("518880.SH")
        self.assertTrue(value is None or isinstance(value, float))

    def test_fetch_premium_missing_akshare_returns_none(self) -> None:
        import unittest.mock as mock
        with mock.patch.dict("sys.modules", {"akshare": None}):
            self.assertIsNone(fetch_premium_discount_akshare("518880.SH"))


class TrackingErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        _write_fund_daily(self.root)
        _write_index_daily(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_compute_tracking_error_with_benchmark(self) -> None:
        # 基金日收益 0.1%、指数 0.05%，应产出有限正跟踪误差
        te = compute_tracking_error("513500.SH", self.root, benchmark_code="000300.SH")
        self.assertIsNotNone(te)
        self.assertGreater(te, 0)

    def test_compute_tracking_error_no_benchmark_resolves_none(self) -> None:
        # 没有 fund_basic → benchmark_code 无法解析 → None
        (self.root / "fund_basic.csv").unlink(missing_ok=True)
        te = compute_tracking_error("513500.SH", self.root, benchmark_code=None)
        self.assertIsNone(te)

    def test_compute_tracking_error_missing_index(self) -> None:
        (self.root / "index_daily.csv").unlink(missing_ok=True)
        te = compute_tracking_error("513500.SH", self.root, benchmark_code="000300.SH")
        self.assertIsNone(te)


class FetchTradeAssetInfoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        _write_fund_basic(self.root)
        _write_fund_daily(self.root)
        _write_index_daily(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _fetch(self, code: str, premium: float | None = None) -> TradeAssetFetchResult:
        """mock 掉 AKShare 折溢价联网调用后执行抓取。"""
        import unittest.mock as mock
        with mock.patch(
            "qteasy_research.core.global_etf_trade_fetcher.fetch_premium_discount_akshare",
            return_value=premium,
        ):
            return fetch_trade_asset_info(code, self.root)

    def test_fetch_returns_result_object(self) -> None:
        result = self._fetch("518880.SH", premium=-0.2)
        self.assertIsInstance(result, TradeAssetFetchResult)
        self.assertEqual(result.code, "518880.SH")

    def test_fetch_local_name_and_fee_filled(self) -> None:
        result = self._fetch("518880.SH", premium=-0.2)
        self.assertEqual(result.name, "华安黄金ETF")
        self.assertEqual(result.management_fee, 0.5)

    def test_fetch_premium_filled(self) -> None:
        result = self._fetch("518880.SH", premium=-0.2)
        self.assertEqual(result.premium_discount, -0.2)

    def test_fetch_unknown_code_adds_notes(self) -> None:
        result = self._fetch("999999.SH")
        self.assertTrue(any("无该基金基础资料" in note for note in result.notes))

    def test_fetch_tracking_error_present_or_note(self) -> None:
        result = self._fetch("513500.SH")
        # 有 fund_basic 且 index_daily 有 000300.SH → 应算出或提示
        self.assertTrue(
            result.tracking_error is not None
            or any("跟踪误差" in note for note in result.notes)
        )

    def test_filled_excludes_none_and_notes(self) -> None:
        result = self._fetch("518880.SH", premium=-0.2)
        filled = result.filled
        self.assertNotIn("notes", filled)
        self.assertNotIn("code", filled)
        self.assertIn("name", filled)
        self.assertIn("management_fee", filled)


if __name__ == "__main__":
    unittest.main()
