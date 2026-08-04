"""P2 桌面端全球 ETF 宏观研究页面测试。"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from PySide6.QtWidgets import QApplication

from qteasy_research.core.global_etf_engine import GlobalEtfScoreResult
from qteasy_research.desktop.global_etf_page import GlobalEtfPage, GlobalEtfScoreWorker
from qteasy_research.pretrade.storage import ResearchStore


def _write_series(macro_dir: Path, series_id: str, values: list[float]) -> None:
    dates = ["2024-01-31", "2024-02-29"]
    pd.DataFrame({
        "observation_date": dates,
        "available_at": dates,
        "value": values,
        "quality_level": ["A", "A"] if series_id.startswith("DGS") or series_id == "DFII10" else ["B", "B"],
    }).to_csv(macro_dir / f"{series_id}.csv", index=False)


def _make_fixture(root: Path) -> None:
    macro_dir = root / "data" / "processed" / "global_macro"
    macro_dir.mkdir(parents=True)
    _write_series(macro_dir, "DGS30", [3.0, 3.3])
    _write_series(macro_dir, "DGS10", [2.5, 2.6])
    _write_series(macro_dir, "DGS2", [3.0, 3.1])
    _write_series(macro_dir, "DFII10", [1.0, 1.2])
    for asset in ("SPY", "TLT", "GLD"):
        _write_series(macro_dir, asset, [400.0 + i * 10.0 for i in range(2)])
    pd.DataFrame([
        {"asset": "SPY", "macro_state": "实际利率上行", "sample_count": 85, "monthly_return": -0.0023,
         "monthly_volatility": 0.047, "max_drawdown": -0.51, "win_rate": 0.55},
        {"asset": "SPY", "macro_state": "加息/利率上行", "sample_count": 52, "monthly_return": 0.0097,
         "monthly_volatility": 0.046, "max_drawdown": -0.29, "win_rate": 0.62},
    ]).to_csv(macro_dir / "condition_returns.csv", index=False, encoding="utf-8-sig")

    store = ResearchStore(root / "store")
    for asset in ("SPY", "TLT", "GLD"):
        store.upsert_global_etf_definition({
            "asset_code": asset, "research_asset_code": asset, "name": asset,
        })
        store.upsert_global_etf_activation({
            "asset_code": asset, "horizon": "medium", "enabled": 1, "frequency": "monthly",
        })
    store.upsert_global_etf_macro_rule({
        "asset_code": "SPY", "macro_state": "real_yield_up", "modifier": 0.92,
        "sample_count": 85, "confidence": "medium", "status": "APPROVED",
        "effective_date": "2024-01-01",
    })
    store.upsert_global_etf_macro_rule({
        "asset_code": "SPY", "macro_state": "rate_up", "modifier": 0.85,
        "sample_count": 52, "confidence": "high", "status": "DRAFT",
        "effective_date": "2024-01-01",
    })


class DesktopGlobalEtfPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="qteasy_global_etf_", dir="D:/Project DPS"))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_empty_page_constructs(self) -> None:
        page = GlobalEtfPage(self.root / "store", data_root=self.root / "data")
        self.assertEqual(page.score_table.rowCount(), 0)
        self.assertEqual(page.rules_table.rowCount(), 0)
        self.assertEqual(page.condition_table.rowCount(), 0)
        self.assertIn("数据目录", page.macro_browser.toPlainText())

    def test_fixture_refresh_shows_rules_and_condition(self) -> None:
        _make_fixture(self.root)
        page = GlobalEtfPage(self.root / "store", data_root=self.root / "data")
        page.refresh()
        self.assertEqual(page.rules_table.rowCount(), 2)
        self.assertEqual(page.condition_table.rowCount(), 2)
        macro_text = page.macro_browser.toPlainText()
        self.assertIn("DGS30", macro_text)
        self.assertIn("DGS10", macro_text)

    def test_recalculate_result_fills_score_table(self) -> None:
        _make_fixture(self.root)
        page = GlobalEtfPage(self.root / "store", data_root=self.root / "data")
        result = GlobalEtfScoreResult(
            target_date="2024-02-29",
            status="PARTIAL",
            rate_proxy="DGS30",
            scores=[{
                "asset": "SPY", "research_asset": "SPY", "data_as_of": "2024-02-29",
                "data_quality": "B", "macro_state": ["rate_up"], "rate_proxy": "DGS30",
                "frequency_used": "monthly", "base_score": 5.34, "macro_modifier": None,
                "final_score": None, "macro_support_factors": [], "macro_conflict_factors": [],
                "sample_count": 1, "status": "PARTIAL", "warnings": [],
            }],
            warnings=[],
        )
        page._on_score_completed(result)
        self.assertEqual(page.score_table.rowCount(), 1)
        self.assertIn("SPY", page.score_table.item(0, 0).text())

    def test_score_worker_runs_engine(self) -> None:
        _make_fixture(self.root)
        worker = GlobalEtfScoreWorker(
            target_date="2024-02-29",
            assets=["SPY"],
            horizon="medium",
            data_root=self.root / "data",
            store_root=self.root / "store",
            persist=False,
        )
        worker.run()  # 同步执行 run 逻辑；completed 无接收者，安全
        # 引擎可运行：再直接调用一次确认不抛异常
        page = GlobalEtfPage(self.root / "store", data_root=self.root / "data")
        page.target_date_edit.setText("2024-02-29")
        self.assertTrue(page.recalculate_button.isEnabled())


class DesktopGlobalEtfMappingUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="qteasy_map_ui_", dir="D:/Project DPS"))
        _make_fixture(self.root)
        self.page = GlobalEtfPage(self.root / "store", data_root=self.root / "data")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _fill_form(self, trade_code: str, **kwargs) -> None:
        self.page.mapping_trade_code.setText(trade_code)
        for attr, value in kwargs.items():
            getattr(self.page, attr).setText(value)

    def test_save_mapping_adds_row(self) -> None:
        self.assertEqual(self.page.mapping_table.rowCount(), 0)
        self.page.mapping_trade_code.setText("513500.SH")
        self.page.mapping_trade_name.setText("标普500 QDII")
        self.page.mapping_exchange_rate.setValue(7.2)
        self.page.mapping_management_fee.setValue(0.6)
        self.page.save_mapping()
        self.assertEqual(self.page.mapping_table.rowCount(), 1)
        self.assertIn("已保存", self.page.page_status.text())
        store = ResearchStore(self.root / "store")
        rows = store.get_global_etf_trade_mappings("SPY")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trade_asset_code"], "513500.SH")
        self.assertEqual(rows[0]["exchange_rate"], 7.2)

    def test_save_mapping_missing_code_rejected(self) -> None:
        self.page.mapping_trade_code.setText("")
        self.page.save_mapping()
        self.assertIn("请填写交易资产代码", self.page.page_status.text())
        self.assertEqual(self.page.mapping_table.rowCount(), 0)

    def test_deactivate_selected_mapping(self) -> None:
        self.page.mapping_trade_code.setText("513500.SH")
        self.page.save_mapping()
        self.page.mapping_table.selectRow(0)
        self.page.deactivate_mapping()
        store = ResearchStore(self.root / "store")
        rows = store.get_global_etf_trade_mappings("SPY", status=None)
        self.assertEqual(rows[0]["status"], "INACTIVE")
        self.assertIn("停用", self.page.page_status.text())

    def test_delete_selected_mapping(self) -> None:
        self.page.mapping_trade_code.setText("513500.SH")
        self.page.save_mapping()
        self.page.mapping_table.selectRow(0)
        self.page.delete_mapping()
        store = ResearchStore(self.root / "store")
        self.assertEqual(store.get_global_etf_trade_mappings("SPY", status=None), [])
        self.assertIn("删除", self.page.page_status.text())

    def test_effective_mapping_tip_updates(self) -> None:
        self.page.mapping_trade_code.setText("513500.SH")
        self.page.mapping_priority.setValue(1)
        self.page.save_mapping()
        tip = self.page.mapping_table.toolTip()
        self.assertIn("513500.SH", tip)
        self.assertIn("生效", tip)


if __name__ == "__main__":
    unittest.main()
