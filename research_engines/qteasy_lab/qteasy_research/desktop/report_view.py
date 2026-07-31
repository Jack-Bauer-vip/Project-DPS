"""桌面端研究报告显示辅助工具。

报告正文仍来自 Markdown；本模块只在桌面端显示时追加当前研究版本的
图表资源，不修改数据库、原始报告或导出文件。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QUrl
from PySide6.QtGui import QImage, QTextCursor, QTextDocument, QTextImageFormat

if TYPE_CHECKING:
    from PySide6.QtWidgets import QTextBrowser

    from qteasy_research.pretrade.schemas import ResearchRunResult


_CHART_SPECS = (
    ("normalized_price", "归一化价格走势"),
    ("drawdown", "回撤曲线"),
)


def report_chart_paths(
    report: ResearchRunResult,
    *,
    existing_only: bool = True,
) -> list[tuple[str, str, Path]]:
    """返回报告自身版本对应的图表路径，不跨版本寻找图表。"""

    paths: list[tuple[str, str, Path]] = []
    for key, title in _CHART_SPECS:
        raw_path = (report.artifacts or {}).get(key)
        if not raw_path:
            continue
        path = Path(raw_path).expanduser()
        if existing_only:
            if not path.is_file() or QImage(str(path)).isNull():
                continue
        paths.append((key, title, path.resolve()))
    return paths


def build_report_markdown(
    report: ResearchRunResult,
    markdown: str,
) -> tuple[str, list[tuple[str, str, Path]]]:
    """在报告副本末尾追加当前版本图表引用。"""

    charts = report_chart_paths(report)
    if not charts:
        return markdown, []
    lines = [markdown.rstrip(), "", "## 图表", ""]
    for _key, title, path in charts:
        lines.extend([f"### {title}", "", f"![{title}]({QUrl.fromLocalFile(str(path)).toString()})", ""])
    return "\n".join(lines).rstrip() + "\n", charts


def show_report_with_charts(
    browser: QTextBrowser,
    report: ResearchRunResult,
    markdown: str,
) -> int:
    """显示报告并返回成功加载的图表数量。"""

    _rendered, charts = build_report_markdown(report, markdown)
    browser.setSearchPaths([str(path.parent) for _key, _title, path in charts])
    browser.setMarkdown(markdown)

    document = browser.document()
    resources: list[tuple[str, str, Path, QImage, QUrl]] = []
    for key, title, path in charts:
        url = QUrl.fromLocalFile(str(path))
        image = QImage(str(path))
        document.addResource(QTextDocument.ResourceType.ImageResource, url, image)
        resources.append((key, title, path, image, url))
    if charts:
        cursor = QTextCursor(document)
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertBlock()
        cursor.insertText("图表")
        max_width = max(320, browser.viewport().width() - 32)
        for _key, title, _path, image, url in resources:
            cursor.insertBlock()
            cursor.insertText(title)
            cursor.insertBlock()
            image_format = QTextImageFormat()
            image_format.setName(url.toString())
            if image.width() > max_width:
                scale = max_width / image.width()
                image_format.setWidth(max_width)
                image_format.setHeight(image.height() * scale)
            else:
                image_format.setWidth(image.width())
                image_format.setHeight(image.height())
            cursor.insertImage(image_format)
            cursor.insertBlock()
        document.markContentsDirty(0, document.characterCount())
        browser.viewport().update()
    return len(charts)


__all__ = [
    "build_report_markdown",
    "report_chart_paths",
    "show_report_with_charts",
]
