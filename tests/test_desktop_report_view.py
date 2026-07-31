from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl
from PySide6.QtGui import QImage, QTextDocument
from PySide6.QtWidgets import QApplication, QTextBrowser

from qteasy_research.desktop.report_view import (
    build_report_markdown,
    show_report_with_charts,
)
from qteasy_research.pretrade.schemas import AssetIdentity, ResearchRunResult


class DesktopReportViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="qteasy_report_view_", dir="D:/Project DPS"))
        self.charts = self.root / "projects" / "project-1" / "versions" / "run-1" / "charts"
        self.charts.mkdir(parents=True)
        self._write_png(self.charts / "normalized_price.png", 0xFF1769AA)
        self._write_png(self.charts / "drawdown.png", 0xFFC0392B)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def _write_png(path: Path, color: int) -> None:
        image = QImage(20, 20, QImage.Format.Format_RGB32)
        image.fill(color)
        assert image.save(str(path))

    def _report(self, **artifacts: str) -> ResearchRunResult:
        return ResearchRunResult(
            run_id="run-1",
            research_case_id="case-1",
            asset_identity=AssetIdentity(code="518880.SH", name="华安易富黄金ETF"),
            artifacts=artifacts,
            report="# 研究报告\n\n正文内容",
        )

    def test_report_loads_its_own_chart_resources(self) -> None:
        report = self._report(
            normalized_price=str(self.charts / "normalized_price.png"),
            drawdown=str(self.charts / "drawdown.png"),
        )
        markdown, charts = build_report_markdown(report, report.report)
        self.assertEqual(len(charts), 2)
        self.assertIn("normalized_price.png", markdown)
        self.assertIn("drawdown.png", markdown)

        browser = QTextBrowser()
        self.assertEqual(show_report_with_charts(browser, report, report.report), 2)
        for path in (self.charts / "normalized_price.png", self.charts / "drawdown.png"):
            resource = browser.document().resource(
                QTextDocument.ResourceType.ImageResource,
                QUrl.fromLocalFile(str(path.resolve())),
            )
            self.assertFalse(resource.isNull())

    def test_missing_or_corrupt_charts_do_not_hide_report(self) -> None:
        corrupt = self.charts / "broken.png"
        corrupt.write_bytes(b"not-a-png")
        report = self._report(
            normalized_price=str(self.charts / "missing.png"),
            drawdown=str(corrupt),
        )
        browser = QTextBrowser()
        self.assertEqual(show_report_with_charts(browser, report, report.report), 0)
        self.assertIn("正文内容", browser.toMarkdown())

    def test_chart_paths_are_isolated_by_report_version(self) -> None:
        other = self.root / "projects" / "project-1" / "versions" / "run-2" / "charts"
        other.mkdir(parents=True)
        other_price = other / "normalized_price.png"
        self._write_png(other_price, 0xFF00AA00)
        report = self._report(normalized_price=str(self.charts / "normalized_price.png"))
        markdown, charts = build_report_markdown(report, report.report)
        self.assertEqual(len(charts), 1)
        self.assertIn(str(self.charts / "normalized_price.png"), str(charts[0][2]))
        self.assertNotIn(str(other_price), markdown)


if __name__ == "__main__":
    unittest.main()
