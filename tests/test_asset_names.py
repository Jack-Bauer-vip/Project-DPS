"""reference/asset_names：资产展示名规范化测试（name 完整性/截断/错字）。"""

from __future__ import annotations

import unittest

from qteasy_research.reference.asset_names import (
    ASSET_DISPLAY_NAMES,
    KNOWN_TYPO_SUBSTRINGS,
    LEGACY_BAD_NAMES,
    resolve_display_name,
)


class AssetDisplayNameTests(unittest.TestCase):
    """规范展示名表本身完整性：非空、无错字、确实修正了已知截断/错字形态。"""

    def test_canonical_names_nonempty(self) -> None:
        self.assertTrue(ASSET_DISPLAY_NAMES)
        for asset_id, name in ASSET_DISPLAY_NAMES.items():
            self.assertIsInstance(asset_id, str)
            self.assertTrue(asset_id)
            self.assertIsInstance(name, str)
            self.assertTrue(name.strip(), f"{asset_id} 展示名为空")

    def test_canonical_names_no_known_typo(self) -> None:
        for asset_id, name in ASSET_DISPLAY_NAMES.items():
            for typo in KNOWN_TYPO_SUBSTRINGS:
                self.assertNotIn(
                    typo, name,
                    f"{asset_id} 展示名含错字 {typo!r}: {name!r}",
                )

    def test_canonical_names_fix_legacy_truncation(self) -> None:
        # 规范表必须与已知截断/错字形态不同（即确实完成了修正）。
        for asset_id, legacy in LEGACY_BAD_NAMES.items():
            canonical = ASSET_DISPLAY_NAMES.get(asset_id)
            self.assertIsNotNone(canonical, f"{asset_id} 未在规范表中")
            self.assertNotEqual(canonical, legacy, f"{asset_id} 规范名未修正: {canonical!r}")

    def test_canonical_names_not_truncated_to_lone_company_prefix(self) -> None:
        # 截断特征哨兵：A 侧截断形态都以单字公司简称首字结尾
        # （"稀有金属ETF嘉"/"科创200ETF华"/"标普500ETF南"）。规范展示名不得以
        # 这些单字结尾（"华夏"→夏、"华泰柏瑞"→瑞、"南方"→方，均不受影响）。
        truncated_suffixes = ("嘉", "华", "南")
        for asset_id, name in ASSET_DISPLAY_NAMES.items():
            for suffix in truncated_suffixes:
                self.assertFalse(
                    name.endswith(suffix),
                    f"{asset_id} 展示名以疑似截断单字 {suffix!r} 结尾: {name!r}",
                )

    def test_canonical_known_ids(self) -> None:
        # 固定锁定 14 个 active 资产的规范展示名（与协调层 2026-08-11 实测清单一致）。
        expected = {
            "562800.SH": "稀有金属ETF嘉实",
            "159985.SZ": "豆粕ETF华夏",
            "588230.SH": "科创200ETF华泰柏瑞",
            "518880.SH": "黄金ETF华安易富",
            "159516.SZ": "半导体设备ETF",
            "515180.SH": "红利ETF易方达",
            "513050.SH": "中概互联网ETF",
            "512890.SH": "红利低波ETF华泰柏瑞",
            "515450.SH": "红利低波50ETF",
            "513650.SH": "标普500ETF南方",
            "159941.SZ": "纳指ETF广发",
            "164824.SZ": "印度基金LOF",
            "513520.SH": "日经ETF华夏",
            "159131.SZ": "港股通信息技术",
        }
        self.assertEqual(ASSET_DISPLAY_NAMES, expected)


class ResolveDisplayNameTests(unittest.TestCase):
    def test_override_known_truncated(self) -> None:
        self.assertEqual(resolve_display_name("562800.SH", "稀有金属ETF嘉"), "稀有金属ETF嘉实")
        self.assertEqual(resolve_display_name("159985.SZ", "豆柏ETF华夏"), "豆粕ETF华夏")
        self.assertEqual(resolve_display_name("513650.SH", "标普500ETF南"), "标普500ETF南方")

    def test_override_full_source(self) -> None:
        # 命中规范表时，即使 A 侧 name 已是完整形态也以规范表为准（确定性）。
        self.assertEqual(resolve_display_name("515180.SH", "红利ETF易方达"), "红利ETF易方达")

    def test_passthrough_unknown_id(self) -> None:
        self.assertEqual(resolve_display_name("000001.SZ", "平安银行"), "平安银行")
        self.assertEqual(resolve_display_name("999999.X", "自定义资产"), "自定义资产")

    def test_passthrough_none(self) -> None:
        self.assertIsNone(resolve_display_name("000001.SZ", None))

    def test_whitespace_normalized(self) -> None:
        self.assertEqual(resolve_display_name(" 562800.SH ", "稀有金属ETF嘉"), "稀有金属ETF嘉实")


if __name__ == "__main__":
    unittest.main()
