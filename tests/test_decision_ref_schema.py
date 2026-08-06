"""reference/schema：决策参考包序列化与"策略名零出现"测试。"""

from __future__ import annotations

import unittest

from qteasy_research.reference.schema import AssetDimensions, DecisionRefPackage


class DecisionRefSchemaTests(unittest.TestCase):
    def test_default_approval_policy(self) -> None:
        package = DecisionRefPackage(generated_date="2026-08-06", data_asof="2026-08-06")
        self.assertEqual(package.approval_policy, "REFERENCE_ONLY")

    def test_to_from_dict_roundtrip(self) -> None:
        asset = AssetDimensions(
            asset_id="000001.SZ",
            name="平安银行",
            volatility={"60d": 0.25},
            beta={"SPY": {"60d": 1.1}},
            data_quality={"quality_level": "A"},
        )
        package = DecisionRefPackage(
            generated_date="2026-08-06",
            data_asof="2026-08-05",
            assets=[asset],
            macro_regime={"states": ["rate_up"]},
        )
        payload = package.to_dict()
        self.assertEqual(payload["approval_policy"], "REFERENCE_ONLY")
        self.assertEqual(payload["data_asof"], "2026-08-05")
        self.assertEqual(payload["source_system"], "systemB")
        restored = DecisionRefPackage.from_dict(payload)
        self.assertEqual(restored.data_asof, "2026-08-05")
        self.assertEqual(restored.assets[0].asset_id, "000001.SZ")
        self.assertEqual(restored.assets[0].beta["SPY"]["60d"], 1.1)
        self.assertEqual(restored.approval_policy, "REFERENCE_ONLY")

    def test_from_dict_missing_fields_tolerated(self) -> None:
        restored = DecisionRefPackage.from_dict({"generated_date": "2026-08-06"})
        self.assertEqual(restored.data_asof, "")
        self.assertEqual(restored.assets, [])
        self.assertEqual(restored.approval_policy, "REFERENCE_ONLY")
        self.assertEqual(restored.source_system, "systemB")

    def test_no_strategy_names_in_output(self) -> None:
        # 机器输出不得出现系统A中文策略名（三剑客/网格等）。
        forbidden = ("三剑客", "网格", "轮动", "定投")
        package = DecisionRefPackage(generated_date="2026-08-06", data_asof="2026-08-06")
        text = str(package.to_dict())
        for word in forbidden:
            self.assertNotIn(word, text)

    def test_empty_asset_dimensions_defaults(self) -> None:
        asset = AssetDimensions(asset_id="MISSING.X")
        payload = asset.to_dict()
        self.assertEqual(payload["volatility"], {})
        self.assertEqual(payload["data_quality"], {})
