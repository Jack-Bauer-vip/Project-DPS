from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidget

from qteasy_research.desktop.factor_page import FactorResearchPage
from qteasy_research.pretrade.factor_scoring import calculate_factor_scores
from qteasy_research.pretrade.factor_ui import initialize_default_factor_definitions
from qteasy_research.pretrade.storage import ResearchStore


class DesktopFactorPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="qteasy_factor_page_", dir="D:/Project DPS"))

    def tearDown(self) -> None:
        for widget in QApplication.topLevelWidgets():
            if widget.objectName() == "factor-test-window":
                widget.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_empty_page_and_default_factor_initialization(self) -> None:
        page = FactorResearchPage(self.root)
        self.assertEqual(page.factor_list.count(), 0)
        created = initialize_default_factor_definitions(self.root)
        page.refresh_factors()
        self.assertEqual(
            [item["factor_id"] for item in created],
            ["momentum_60d", "momentum_120d", "liquidity_turnover", "low_volatility_20d", "pb_value"],
        )
        self.assertEqual(page.factor_list.count(), 5)
        self.assertTrue(any("60日动量" in page.factor_list.item(i).text() for i in range(page.factor_list.count())))

    def test_filters_and_independent_activation_profiles(self) -> None:
        page = FactorResearchPage(self.root)
        initialize_default_factor_definitions(self.root)
        page.refresh_factors()

        page.category_filter.setCurrentIndex(page.category_filter.findData("value"))
        self.assertEqual(page.factor_list.count(), 1)
        self.assertEqual(page.factor_list.item(0).data(Qt.ItemDataRole.UserRole), "pb_value")

        page.category_filter.setCurrentIndex(0)
        page.factor_list.setCurrentRow(0)
        factor_id = page.current_factor_id
        self.assertIsNotNone(factor_id)
        page.profile_asset_type.setCurrentIndex(page.profile_asset_type.findData("STOCK"))
        page.profile_horizon.setCurrentIndex(page.profile_horizon.findData("short"))
        page.profile_enabled.setChecked(True)
        page.profile_weight.setValue(0.75)
        page.save_profile()
        page.profile_horizon.setCurrentIndex(page.profile_horizon.findData("medium"))
        page.profile_weight.setValue(1.25)
        page.save_profile()

        profiles = [
            item for item in ResearchStore(self.root).list_factor_activations()
            if item["factor_id"] == factor_id
        ]
        self.assertEqual(len(profiles), 2)
        self.assertEqual({item["horizon"] for item in profiles}, {"short", "medium"})
        self.assertEqual({round(float(item["weight"]), 2) for item in profiles}, {0.75, 1.25})

    def test_score_preview_worker_can_render_fixture_result(self) -> None:
        initialize_default_factor_definitions(self.root)
        factor_dir = self.root / "data" / "factor_values"
        factor_dir.mkdir(parents=True)
        frame = pd.DataFrame(
            {
                "date": ["2025-01-01", "2025-01-01", "2025-01-01"],
                "asset_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                "value": [1.0, 2.0, 3.0],
                "available_at": ["2025-01-01"] * 3,
            }
        )
        frame.to_parquet(factor_dir / "momentum_60d.parquet", index=False)
        (factor_dir / "momentum_60d.manifest.json").write_text(
            '{"value_semantics":"neutralized_exposure"}', encoding="utf-8"
        )
        ResearchStore(self.root).upsert_factor_activation(
            {
                "factor_id": "momentum_60d",
                "asset_type": "STOCK",
                "horizon": "short",
                "enabled": 1,
                "weight": 1.0,
            }
        )
        page = FactorResearchPage(self.root)
        page.refresh_factors()
        page.preview_codes.setPlainText("000001.SZ\n000002.SZ\n000003.SZ")
        page.preview_date.setText("2025-01-01")
        page.preview_asset_type.setCurrentIndex(page.preview_asset_type.findData("STOCK"))
        page.preview_horizon.setCurrentIndex(page.preview_horizon.findData("short"))
        page.run_score_preview()
        while page.score_worker is not None and page.score_worker.isRunning():
            self.app.processEvents()
        self.app.processEvents()
        self.assertIn("完成", page.score_status.text())
        self.assertEqual(page.score_table.rowCount(), 3)
        headers = [page.score_table.horizontalHeaderItem(i).text() for i in range(page.score_table.columnCount())]
        self.assertIn("资产代码", headers)
        self.assertIn("60日动量得分", headers)
        self.assertIn("综合评分", headers)

    def test_unavailable_score_shows_diagnostic_and_solution(self) -> None:
        initialize_default_factor_definitions(self.root)
        page = FactorResearchPage(self.root)
        page.preview_codes.setPlainText("518880.SH")
        page.preview_asset_type.setCurrentIndex(page.preview_asset_type.findData("STOCK"))
        page.preview_horizon.setCurrentIndex(page.preview_horizon.findData("medium"))
        result = calculate_factor_scores(
            "2025-01-01", "STOCK", "medium", universe=["518880.SH"], store_root=self.root,
        )
        page.show_score_result(result)
        self.assertIn("诊断", page.diagnostic_summary.text())
        self.assertIn("已启用", page.diagnostic_view.toPlainText())
        self.assertIn("不可用", page.score_table.item(0, 1).text() or "")
        self.assertTrue(page.open_factor_config_button.isEnabled())
        page._switch_asset_type_from_diagnostic()
        self.assertEqual(page.preview_asset_type.currentData(), "ETF")

    def test_diagnostic_action_emits_data_context_without_starting_task(self) -> None:
        page = FactorResearchPage(self.root)
        captured = []
        page.diagnostic_action.connect(lambda action, payload: captured.append((action, payload)))
        page.preview_codes.setPlainText("518880.SH\n510300.SH")
        page._emit_diagnostic_action("prepare_factor")
        self.assertEqual(captured[0][0], "prepare_factor")
        self.assertEqual(captured[0][1]["codes"], ["518880.SH", "510300.SH"])

    def test_main_navigation_contains_factor_research(self) -> None:
        import qteasy_research.desktop.app as module

        module.load_settings = lambda: {"store_dir": str(self.root)}
        module.save_settings = lambda payload: None
        original_exec = QApplication.exec
        QApplication.exec = lambda self: 0
        try:
            module.launch()
            navigation = next(
                child for window in QApplication.topLevelWidgets() for child in window.findChildren(QListWidget)
                if child.objectName() == "main-navigation"
            )
            self.assertEqual(navigation.item(2).text(), "因子研究")
        finally:
            QApplication.exec = original_exec
            for window in QApplication.topLevelWidgets():
                window.close()


if __name__ == "__main__":
    unittest.main()
