from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from qteasy_research.desktop.data_page import DataManagementPage


class DesktopDataPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="qteasy_data_page_", dir="D:/Project DPS"))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_page_defaults_to_direct_api_and_does_not_assume_all_etf(self) -> None:
        page = DataManagementPage(self.root)
        self.assertEqual(page.mode, "direct")
        page.update_data()
        self.assertIn("输入至少一个代码", page.status_label.text())

    def test_page_can_switch_to_middleware_mode(self) -> None:
        page = DataManagementPage(self.root)
        page.mode_input.setCurrentIndex(page.mode_input.findData("middleware"))
        self.assertEqual(page.mode, "middleware")

    def test_factor_diagnostic_prefills_codes_and_asset_type(self) -> None:
        page = DataManagementPage(self.root)
        page.prepare_from_factor_diagnostic(["518880.SH", "510300.SH"], "ETF", "low_volatility_20d")
        self.assertEqual(page.codes_input.toPlainText().splitlines(), ["518880.SH", "510300.SH"])
        self.assertEqual(page.asset_type_input.currentData(), "ETF")
        self.assertIn("low_volatility_20d", page.status_label.text())


if __name__ == "__main__":
    unittest.main()
