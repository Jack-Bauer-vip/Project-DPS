"""P3 研究资产↔交易资产映射的离线测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qteasy_research.core.global_etf_trade_mapping import (
    effective_trade_mapping,
    initialize_default_global_etf_trade_mappings,
)
from qteasy_research.pretrade.storage import ResearchStore


def _register_assets(store: ResearchStore) -> None:
    for asset in ("SPY", "TLT", "GLD"):
        store.upsert_global_etf_definition({
            "asset_code": asset,
            "research_asset_code": asset,
            "name": asset,
        })


def _mapping(research: str, trade: str, **overrides) -> dict:
    """构造一个带默认静态汇率的最小映射 payload。"""
    payload = {
        "research_asset_code": research,
        "trade_asset_code": trade,
        "fx_rule": "static",
        "exchange_rate": 1.0,
    }
    payload.update(overrides)
    return payload


class TradeMappingStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ResearchStore(self.root / "store")
        _register_assets(self.store)

    def tearDown(self) -> None:
        self.temp.cleanup()

    # ---- upsert 与校验 ----

    def test_upsert_persists_defaults(self) -> None:
        result = self.store.upsert_global_etf_trade_mapping(_mapping("SPY", "513500.SH"))
        self.assertTrue(result["mapping_id"])
        self.assertEqual(result["currency"], "CNY")
        self.assertEqual(result["status"], "ACTIVE")
        self.assertEqual(result["priority"], 1)
        self.assertEqual(result["management_fee"], 0.0)
        self.assertEqual(result["trading_cost_bps"], 0.0)
        rows = self.store.list_global_etf_trade_mappings()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trade_asset_code"], "513500.SH")

    def test_upsert_preserves_created_at_and_mapping_id(self) -> None:
        first = self.store.upsert_global_etf_trade_mapping(_mapping("SPY", "513500.SH"))
        second = self.store.upsert_global_etf_trade_mapping(
            _mapping("SPY", "513500.SH", management_fee=0.6)
        )
        self.assertEqual(first["mapping_id"], second["mapping_id"])
        self.assertEqual(first["created_at"], second["created_at"])
        self.assertNotEqual(first["updated_at"], second["updated_at"])
        self.assertEqual(second["management_fee"], 0.6)

    def test_upsert_rejects_unknown_research_asset(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.store.upsert_global_etf_trade_mapping(_mapping("QQQ", "513500.SH"))
        self.assertIn("研究资产不存在", str(ctx.exception))

    def test_upsert_rejects_invalid_status(self) -> None:
        with self.assertRaises(ValueError):
            self.store.upsert_global_etf_trade_mapping(_mapping("SPY", "513500.SH", status="FOO"))

    def test_static_fx_rule_requires_exchange_rate(self) -> None:
        # 显式 static 但不给 exchange_rate → 应报错
        payload = {
            "research_asset_code": "SPY",
            "trade_asset_code": "513500.SH",
            "fx_rule": "static",
            "exchange_rate": None,
        }
        with self.assertRaises(ValueError) as ctx:
            self.store.upsert_global_etf_trade_mapping(payload)
        self.assertIn("exchange_rate", str(ctx.exception))

    def test_non_static_fx_rule_allows_none_rate(self) -> None:
        result = self.store.upsert_global_etf_trade_mapping(
            _mapping("SPY", "513500.SH", fx_rule="realtime", exchange_rate=None)
        )
        self.assertEqual(result["fx_rule"], "realtime")
        self.assertIsNone(result["exchange_rate"])

    def test_rejects_negative_fee(self) -> None:
        with self.assertRaises(ValueError):
            self.store.upsert_global_etf_trade_mapping(_mapping("SPY", "513500.SH", management_fee=-1))
        with self.assertRaises(ValueError):
            self.store.upsert_global_etf_trade_mapping(_mapping("SPY", "513500.SH", trading_cost_bps=-5))

    # ---- list / get 过滤与排序 ----

    def test_list_filters_by_status(self) -> None:
        self.store.upsert_global_etf_trade_mapping(_mapping("SPY", "513500.SH", status="ACTIVE"))
        self.store.upsert_global_etf_trade_mapping(_mapping("SPY", "513100.SH", status="INACTIVE"))
        active = self.store.list_global_etf_trade_mappings(status="ACTIVE")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["trade_asset_code"], "513500.SH")
        self.assertEqual(len(self.store.list_global_etf_trade_mappings(status=None)), 2)

    def test_list_filters_by_codes(self) -> None:
        self.store.upsert_global_etf_trade_mapping(_mapping("SPY", "513500.SH"))
        self.store.upsert_global_etf_trade_mapping(_mapping("GLD", "518880.SH"))
        spy = self.store.list_global_etf_trade_mappings(research_asset_code="SPY")
        self.assertEqual([r["trade_asset_code"] for r in spy], ["513500.SH"])
        gld = self.store.list_global_etf_trade_mappings(trade_asset_code="518880.SH")
        self.assertEqual([r["research_asset_code"] for r in gld], ["GLD"])

    def test_get_orders_by_priority(self) -> None:
        for priority, code in ((2, "A"), (1, "B"), (3, "C")):
            self.store.upsert_global_etf_trade_mapping(_mapping("SPY", code, priority=priority))
        rows = self.store.get_global_etf_trade_mappings("SPY")
        self.assertEqual([r["trade_asset_code"] for r in rows], ["B", "A", "C"])

    def test_get_defaults_to_active_only(self) -> None:
        self.store.upsert_global_etf_trade_mapping(_mapping("SPY", "513500.SH", status="ACTIVE"))
        self.store.upsert_global_etf_trade_mapping(_mapping("SPY", "513100.SH", status="INACTIVE"))
        active = self.store.get_global_etf_trade_mappings("SPY")
        self.assertEqual(len(active), 1)
        all_rows = self.store.get_global_etf_trade_mappings("SPY", status=None)
        self.assertEqual(len(all_rows), 2)

    def test_one_research_many_trade_assets(self) -> None:
        for code in ("513500.SH", "007300.OF"):
            self.store.upsert_global_etf_trade_mapping(_mapping("SPY", code))
        self.assertEqual(len(self.store.get_global_etf_trade_mappings("SPY")), 2)

    def test_numeric_fields_roundtrip(self) -> None:
        self.store.upsert_global_etf_trade_mapping(_mapping(
            "SPY", "513500.SH",
            fx_pair="USD/CNY", management_fee=0.6, trading_cost_bps=20,
            tracking_error=1.2, premium_discount=-0.3,
        ))
        row = self.store.get_global_etf_trade_mappings("SPY")[0]
        self.assertEqual(row["fx_pair"], "USD/CNY")
        self.assertEqual(row["management_fee"], 0.6)
        self.assertEqual(row["trading_cost_bps"], 20)
        self.assertEqual(row["tracking_error"], 1.2)
        self.assertEqual(row["premium_discount"], -0.3)

    def test_no_mapping_returns_empty(self) -> None:
        self.assertEqual(self.store.get_global_etf_trade_mappings("SPY"), [])

    # ---- 消费侧辅助 ----

    def test_effective_trade_mapping_picks_min_priority(self) -> None:
        self.store.upsert_global_etf_trade_mapping(_mapping("SPY", "513500.SH", priority=2))
        self.store.upsert_global_etf_trade_mapping(_mapping("SPY", "007300.OF", priority=1))
        eff = effective_trade_mapping(self.store, "SPY")
        self.assertEqual(eff["trade_asset_code"], "007300.OF")

    def test_effective_trade_mapping_none_when_unconfigured(self) -> None:
        self.assertIsNone(effective_trade_mapping(self.store, "SPY"))

    def test_effective_trade_mapping_ignores_inactive(self) -> None:
        self.store.upsert_global_etf_trade_mapping(_mapping("SPY", "513500.SH", status="INACTIVE"))
        self.assertIsNone(effective_trade_mapping(self.store, "SPY"))

    def test_effective_trade_mapping_dangling_reference(self) -> None:
        # research 资产未注册 definition → 悬空引用 → None
        self.assertIsNone(effective_trade_mapping(self.store, "QQQ"))

    def test_initialize_self_mapping_only_when_requested(self) -> None:
        created = initialize_default_global_etf_trade_mappings(self.root / "store", self_mapping=False)
        self.assertEqual(created, [])
        self.assertEqual(self.store.list_global_etf_trade_mappings(), [])

        created = initialize_default_global_etf_trade_mappings(self.root / "store", self_mapping=True)
        self.assertEqual(len(created), 3)
        for row in created:
            self.assertEqual(row["research_asset_code"], row["trade_asset_code"])
            self.assertEqual(row["currency"], "USD")
            self.assertEqual(row["exchange_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
