"""tests/test_d_provider.py：Bern 数据中台（D）Provider 单元测试。

全部 mock ``requests.get``，不真实请求 127.0.0.1:8765（镜像
test_data_manager / test_asset_pool 的离线 fixture 风格）。
"""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import requests

from qteasy_research.pretrade.providers import (
    CompositeProvider,
    DProvider,
    ProviderData,
    ProviderUnavailable,
)
from qteasy_research.pretrade.schemas import AssetIdentity


def _envelope(records, message="ok", code=200):
    return {
        "code": code,
        "message": message,
        "data": records,
        "total": len(records),
        "source": "local_db",
        "data_status": "active",
        "meta": None,
    }


def _etf_records():
    return [
        {"id": 1, "date": "2026-08-11", "open": "4.72", "high": "4.75", "low": "4.70", "close": "4.728", "volume": "5353219.17", "amount": "2541400.132", "code": "510300"},
        {"id": 2, "date": "2026-08-12", "open": "4.727", "high": "4.759", "low": "4.72", "close": "4.748", "volume": "9010137.43", "amount": "4275919.625", "code": "510300"},
    ]


def _index_records():
    return [
        {"id": 1, "date": "2026-08-12", "open": "4660.466", "high": "4700.435", "low": "4657.693", "close": "4690.917", "volume": "18493914800", "amount": None, "symbol": "sh000300"},
    ]


class _FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class DProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        # 清掉可能干扰的 env，保证构造默认值可预期（测试只关心显式入参 + mock）。
        self.env_patch = patch.dict("os.environ", {
            "D_BASE_URL": "",
            "BERN_DATA_BASE_URL": "",
            "D_API_KEY": "",
            "BERN_DATA_API_KEY": "",
            "API_TOKEN": "",
        })
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_get_price_history_returns_normalized_frame(self) -> None:
        provider = DProvider()
        with patch("requests.get", return_value=_FakeResponse(_envelope(_etf_records()))) as mock_get:
            result = provider.get_price_history(AssetIdentity(code="510300.SH", asset_type="ETF"))
        self.assertTrue(result.ok)
        self.assertEqual(result.source, "bern_data")
        self.assertEqual(result.as_of, "2026-08-12")
        self.assertTrue(result.official)
        self.assertIn("trade_date", result.data.columns)
        self.assertIn("close", result.data.columns)
        self.assertIn("vol", result.data.columns)
        self.assertIn("ts_code", result.data.columns)
        self.assertEqual(len(result.data), 2)
        self.assertEqual(result.data["ts_code"].iloc[0], "510300.SH")
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["params"]["code"], "510300")
        # 研究需全量历史：必须带 limit（D 不带 limit 只返回最近 200 条）。
        self.assertEqual(kwargs["params"]["limit"], provider._DEFAULT_LIMIT)
        # 空 key 时不发送 X-API-Key 头。
        self.assertNotIn("X-API-Key", kwargs["headers"])

    def test_api_key_header_sent_when_configured(self) -> None:
        provider = DProvider(api_key="secret")
        with patch("requests.get", return_value=_FakeResponse(_envelope(_etf_records()))) as mock_get:
            provider.get_price_history(AssetIdentity(code="510300.SH", asset_type="ETF"))
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["headers"]["X-API-Key"], "secret")

    def test_connection_error_raises_provider_unavailable(self) -> None:
        provider = DProvider()
        with patch("requests.get", side_effect=requests.exceptions.ConnectionError("down")):
            with self.assertRaises(ProviderUnavailable):
                provider.get_price_history(AssetIdentity(code="510300.SH", asset_type="ETF"))

    def test_http_error_raises_provider_unavailable(self) -> None:
        provider = DProvider()
        with patch("requests.get", return_value=_FakeResponse({"detail": "not found"}, status=404)):
            with self.assertRaises(ProviderUnavailable):
                provider.get_price_history(AssetIdentity(code="510300.SH", asset_type="ETF"))

    def test_bad_body_code_raises_provider_unavailable(self) -> None:
        provider = DProvider()
        with patch("requests.get", return_value=_FakeResponse({"code": 500, "message": "err", "data": []})):
            with self.assertRaises(ProviderUnavailable):
                provider.get_price_history(AssetIdentity(code="510300.SH", asset_type="ETF"))

    def test_composite_falls_back_when_d_unavailable(self) -> None:
        provider = DProvider()
        frame = pd.DataFrame([
            {"trade_date": pd.Timestamp("2026-08-12"), "close": 4.748, "vol": 9010137.43, "ts_code": "510300.SH"},
        ])

        class WorkingProvider:
            name = "working"

            def get_price_history(self, identity):
                return ProviderData(data=frame, source=self.name, as_of="2026-08-12", message="fixture")

        composite = CompositeProvider([provider, WorkingProvider()])
        with patch("requests.get", side_effect=requests.exceptions.ConnectionError("down")):
            result = composite.get_price_history(AssetIdentity(code="510300.SH", asset_type="ETF"))
        self.assertTrue(result.ok)
        self.assertEqual(result.source, "working")
        self.assertEqual(len(composite.attempts), 2)
        self.assertFalse(composite.attempts[0]["ok"])
        self.assertIn("down", composite.attempts[0]["message"])
        self.assertTrue(composite.attempts[1]["ok"])

    def test_empty_price_history_returns_ok_false(self) -> None:
        provider = DProvider()
        with patch("requests.get", return_value=_FakeResponse(_envelope([]))):
            result = provider.get_price_history(AssetIdentity(code="510300.SH", asset_type="ETF"))
        self.assertFalse(result.ok)
        self.assertEqual(result.source, "bern_data")

    def test_benchmark_returns_normalized_frame(self) -> None:
        provider = DProvider()
        with patch("requests.get", return_value=_FakeResponse(_envelope(_index_records()))) as mock_get:
            result = provider.get_benchmark_history("000300.SH")
        self.assertTrue(result.ok)
        self.assertEqual(result.as_of, "2026-08-12")
        self.assertIn("trade_date", result.data.columns)
        self.assertEqual(result.data["ts_code"].iloc[0], "000300.SH")
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["params"]["code"], "sh000300")

    def test_benchmark_empty_falls_through(self) -> None:
        # D 无 SPY/TLT/GLD → 空 data → ok=False，CompositeProvider 顺延到原链。
        provider = DProvider()
        with patch("requests.get", return_value=_FakeResponse(_envelope([]))):
            result = provider.get_benchmark_history("SPY")
        self.assertFalse(result.ok)
        self.assertEqual(result.source, "bern_data")

    def test_to_d_symbol(self) -> None:
        provider = DProvider()
        self.assertEqual(provider._to_d_symbol("000300.SH"), "sh000300")
        self.assertEqual(provider._to_d_symbol("000300.SZ"), "sz000300")
        self.assertEqual(provider._to_d_symbol("SPY"), "SPY")
        self.assertEqual(provider._to_d_symbol("spy"), "SPY")
        self.assertEqual(provider._to_d_symbol("HSI"), "HSI")

    def test_get_metadata_returns_not_official(self) -> None:
        provider = DProvider()
        result = provider.get_metadata(AssetIdentity(code="510300.SH", asset_type="ETF"))
        self.assertFalse(result.ok)
        self.assertEqual(result.source, "bern_data")
        self.assertIn("不提供", result.message)


class DProviderInsertionTests(unittest.TestCase):
    def test_d_provider_is_first_in_orchestrator_chain(self) -> None:
        from qteasy_research.pretrade.orchestrator import _provider_chain
        from qteasy_research.pretrade.providers import LocalCsvProvider, ResearchSnapshotProvider, SqliteProvider

        for data_mode in ("local", "hybrid", "direct"):
            chain = _provider_chain(data_mode, LocalCsvProvider(), ResearchSnapshotProvider("."), SqliteProvider("."))
            self.assertEqual(chain.providers[0].name, "bern_data", data_mode)

    def test_d_provider_is_first_in_data_manager_providers(self) -> None:
        from qteasy_research.pretrade.data_manager import DataManager

        manager = object.__new__(DataManager)
        providers = manager._providers("direct")
        self.assertEqual(providers[0].name, "bern_data")

    def test_d_provider_is_first_in_asset_pool_composite(self) -> None:
        from pathlib import Path

        from qteasy_research.reference.asset_pool import _build_composite_provider

        with tempfile.TemporaryDirectory() as directory:
            chain = _build_composite_provider(Path(directory))
        self.assertEqual(chain.providers[0].name, "bern_data")


if __name__ == "__main__":
    unittest.main()
