"""reference/trader_fingerprint：交易指纹测试（离线 fixture，不依赖真实数据目录）。

覆盖：买卖比 / 持仓天数估计 / 换手率 / 集中度 / 人工干预档案 / 非 CONFIRMED 审计 /
缺 human_log 不报错 / **报告无中文策略名断言**。
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from qteasy_research.reference.trader_fingerprint import (
    analyze_trader_fingerprint,
    render_fingerprint_markdown,
)

_CJK = re.compile(r"[一-鿿]")


class TraderFingerprintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger_path = self.root / "ledger.csv"
        self.override_path = self.root / "override.csv"
        self.human_log_path = self.root / "human_log.csv"
        self._write_ledger()
        self._write_overrides()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_ledger(self) -> None:
        ledger = pd.DataFrame({
            "trade_date": [
                "2025-01-02", "2025-01-05", "2025-01-09",
                "2025-01-03", "2025-01-10", "2025-01-03",
                "2025-01-20",
            ],
            "strategy_id": ["grid_a"] * 6 + ["grid_b"],
            "asset_id": [
                "515450.SH", "515450.SH", "515450.SH",
                "588000.SH", "588000.SH", "",
                "159941.SZ",
            ],
            "side": ["BUY", "BUY", "SELL", "BUY", "SELL", "CASH", "BUY"],
            "amount": [1000.0, 2000.0, 1500.0, 3000.0, 3000.0, 500.0, 100.0],
            "confirm_status": ["CONFIRMED"] * 6 + ["PENDING"],
            "name": ["甲基金"] * 7,  # 中文策略名只存在于台账，报告零出现。
        })
        ledger.to_csv(self.ledger_path, index=False, encoding="utf-8")

    def _write_overrides(self) -> None:
        overrides = pd.DataFrame({
            "date": ["2025-01-01", "2025-01-02", "2025-01-03"],
            "asset_id": ["515450.SH", "588000.SH", "159941.SZ"],
            "override_action": ["HOLD", "REDUCE", "ADD"],
            "override_weight": ["0.5", "0.3", "0.2"],
            "reason": ["manual", "manual", "manual"],
            "approved_by": ["alice", "alice", "bob"],
            "expires_on": ["2026-12-31", "2025-01-01", ""],
        })
        overrides.to_csv(self.override_path, index=False, encoding="utf-8")

    def _fingerprint(self, human_log_path: str | object | None = None) -> dict:
        return analyze_trader_fingerprint(
            self.ledger_path, self.override_path, human_log_path=human_log_path
        )

    def test_buy_sell_ratio_and_cash(self) -> None:
        fp = self._fingerprint()
        grid_a = fp["strategies"]["grid_a"]
        self.assertEqual(grid_a["trade_count"], 5)
        self.assertEqual(grid_a["buy_count"], 3)
        self.assertEqual(grid_a["sell_count"], 2)
        self.assertAlmostEqual(grid_a["buy_ratio"], 0.6)
        self.assertAlmostEqual(grid_a["sell_ratio"], 0.4)
        self.assertEqual(grid_a["cash_count"], 1)
        self.assertAlmostEqual(grid_a["cash_ratio"], 1 / 6, places=4)

    def test_holding_days_median(self) -> None:
        grid_a = self._fingerprint()["strategies"]["grid_a"]
        # 515450 买入1/2→卖出1/9=7天；588000 买入1/3→卖出1/10=7天 → 中位 7。
        self.assertEqual(grid_a["holding_days_median"], 7.0)

    def test_turnover(self) -> None:
        grid_a = self._fingerprint()["strategies"]["grid_a"]
        # 成交额 10500 / 期末净持仓 1500 = 7.0。
        self.assertAlmostEqual(grid_a["turnover"], 7.0)

    def test_concentration(self) -> None:
        grid_a = self._fingerprint()["strategies"]["grid_a"]
        self.assertEqual(grid_a["asset_count"], 2)
        self.assertEqual(grid_a["top5_assets"], ["588000.SH", "515450.SH"])
        self.assertAlmostEqual(grid_a["top5_concentration"], 1.0)

    def test_unpaired_buy_holding_none(self) -> None:
        grid_b = self._fingerprint()["strategies"]["grid_b"]
        self.assertIsNone(grid_b["holding_days_median"])
        self.assertEqual(grid_b["buy_ratio"], 1.0)

    def test_non_confirmed_audit(self) -> None:
        non_confirmed = self._fingerprint()["non_confirmed"]
        self.assertEqual(len(non_confirmed), 1)
        self.assertEqual(non_confirmed[0]["strategy_id"], "grid_b")
        self.assertEqual(non_confirmed[0]["confirm_status"], "PENDING")

    def test_override_profile(self) -> None:
        profile = self._fingerprint()["override_profile"]
        self.assertEqual(profile["total"], 3)
        self.assertEqual(profile["active"], 1)
        self.assertEqual(profile["expired"], 1)
        self.assertEqual(profile["no_expiry"], 1)
        self.assertEqual(profile["by_action"], {"HOLD": 1, "REDUCE": 1, "ADD": 1})

    def test_missing_human_log_does_not_raise(self) -> None:
        # human_log 缺失 → 不报错，human_log_rows 为 None（报告标 N/A）。
        fp = self._fingerprint(human_log_path=self.root / "absent.csv")
        self.assertIsNone(fp["human_log_rows"])
        report = render_fingerprint_markdown(fp)
        self.assertIn("human_log_rows: N/A", report)

    def test_no_chinese_in_report(self) -> None:
        report = render_fingerprint_markdown(self._fingerprint())
        self.assertIsNone(_CJK.search(report), "报告包含中文字符（零中文策略名纪律被破坏）")
        # 报告内只出现 strategy_id 标识符，台账中文名不出现在报告中。
        self.assertNotIn("甲基金", report)


if __name__ == "__main__":
    unittest.main()
