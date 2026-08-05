"""把 docs/SYSTEM_MANUAL.md 编译为中文 PDF。

使用 reportlab（纯 Python）渲染中文，不依赖 Qt 打印引擎（Qt 在 offscreen
环境下会因字体光栅化把中文渲染成实心黑块）。中文采用 reportlab 内置的
STSong-Light CID 字体，无需外部字体文件。

用法：
    .venv\\Scripts\\python.exe -B scripts/build_system_manual_pdf.py
"""

from __future__ import annotations

import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
    SimpleDocTemplate,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE = PROJECT_ROOT / "docs" / "SYSTEM_MANUAL.md"
DESTINATION = PROJECT_ROOT / "docs" / "SYSTEM_MANUAL.pdf"

# 注册内置中文 CID 字体（无需字体文件）。
pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
CN = "STSong-Light"

INK = colors.HexColor("#173434")
GOLD = colors.HexColor("#b8872f")
MUTED = colors.HexColor("#667878")
LINE = colors.HexColor("#d8d2c4")
TH_BG = colors.HexColor("#e8eee8")
CODE_BG = colors.HexColor("#f5f2ec")

_sheet = getSampleStyleSheet()


def _style(name: str, **kwargs) -> ParagraphStyle:
    base = _sheet.get(name, _sheet["Normal"])
    defaults = dict(
        fontName=CN,
        fontSize=9.5,
        leading=15,
        textColor=INK,
        spaceAfter=6,
    )
    defaults.update(kwargs)
    return ParagraphStyle(name=name + str(len(kwargs)), parent=base, **defaults)


ST_H1 = _style("Title", fontSize=20, leading=26, spaceAfter=10)
ST_H2 = _style("Heading2", fontSize=14, leading=20, spaceBefore=16, spaceAfter=8)
ST_H3 = _style("Heading3", fontSize=12, leading=18, spaceBefore=12, spaceAfter=6)
ST_H4 = _style("Heading4", fontSize=11, leading=16, spaceBefore=10, spaceAfter=4)
ST_P = _style("BodyText", fontSize=9.5, leading=15)
ST_CODE = _style("Code", fontName="Courier", fontSize=8, leading=11, textColor=INK)
ST_QUOTE = _style(
    "BlockQuote", fontSize=9, leading=14, textColor=MUTED, leftIndent=12, spaceAfter=8,
)
ST_BULLET = _style("Bullet", fontSize=9.5, leading=15, leftIndent=10, spaceAfter=3)


def _inline(text: str) -> str:
    """把行内 markdown（粗体/行内代码）转为 reportlab 段落支持的标签。"""
    # 行内代码先占位（代码可能含中文，用 CN 字体但变色区分）
    def _code(match):
        return f'<font color="#b8872f">{match.group(1)}</font>'

    text = re.sub(r"`([^`]+)`", _code, text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", text)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # 恢复我们插入的标签
    text = re.sub(r"&lt;b&gt;(.*?)&lt;/b&gt;", r"<b>\1</b>", text)
    text = re.sub(r"&lt;i&gt;(.*?)&lt;/i&gt;", r"<i>\1</i>", text)
    text = re.sub(r'&lt;font color="#b8872f"&gt;(.*?)&lt;/font&gt;', r'<font color="#b8872f">\1</font>', text)
    return text


def _parse_table(lines: list[str]) -> Table:
    rows = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        # 跳过分隔行 |---|---|
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            continue
        rows.append([Paragraph(_inline(c), _style(f"cell{len(rows)}", fontSize=8.5, leading=12)) for c in cells])
    ncols = max(len(r) for r in rows)
    table = Table(rows, colWidths=None)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    if rows:
        style.append(("BACKGROUND", (0, 0), (-1, 0), TH_BG))
        style.append(("FONTNAME", (0, 0), (-1, 0), CN))
        style.append(("FONTSIZE", (0, 0), (-1, 0), 8.5))
    table.setStyle(TableStyle(style))
    return table


def _build_flowables(lines: list[str]) -> list:
    flow: list = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        # 分页标记（手动分段控制）
        if stripped == "<PAGEBREAK>":
            flow.append(PageBreak())
            i += 1
            continue
        # 分隔线 ---
        if re.fullmatch(r"-{3,}", stripped):
            flow.append(Spacer(1, 6))
            i += 1
            continue
        # 标题
        m = re.match(r"^(#{1,4})\s+(.*)", stripped)
        if m:
            level = len(m.group(1))
            st = {1: ST_H1, 2: ST_H2, 3: ST_H3, 4: ST_H4}[level]
            flow.append(Paragraph(_inline(m.group(2)), st))
            i += 1
            continue
        # 代码块
        if stripped.startswith("```"):
            buf = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            flow.append(Preformatted("\n".join(buf), ST_CODE))
            flow.append(Spacer(1, 6))
            continue
        # 表格
        if stripped.startswith("|"):
            buf = []
            while i < n and lines[i].strip().startswith("|"):
                buf.append(lines[i])
                i += 1
            flow.append(_parse_table(buf))
            flow.append(Spacer(1, 6))
            continue
        # 引用
        if stripped.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            flow.append(Paragraph(_inline("　".join(buf)), ST_QUOTE))
            continue
        # 无序列表
        if re.match(r"^[-*]\s+", stripped):
            items = []
            while i < n and re.match(r"^[-*]\s+", lines[i].strip()):
                items.append(Paragraph(_inline(re.sub(r"^[-*]\s+", "", lines[i].strip())), ST_BULLET))
                i += 1
            flow.append(ListFlowable(items, bulletType="bullet", start="•", leftIndent=12))
            continue
        # 有序列表
        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < n and re.match(r"^\d+\.\s+", lines[i].strip()):
                items.append(Paragraph(_inline(re.sub(r"^\d+\.\s+", "", lines[i].strip())), ST_BULLET))
                i += 1
            flow.append(ListFlowable(items, bulletType="1", leftIndent=12))
            continue
        # 普通段落（合并连续行）
        buf = [stripped]
        i += 1
        while i < n and lines[i].strip() and not re.match(r"^(#{1,4}\s|```|>|[-*]\s|\d+\.\s|\||-{3,})", lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        flow.append(Paragraph(_inline("".join(buf)), ST_P))
    return flow


def _build_document() -> None:
    markdown_text = SOURCE.read_text(encoding="utf-8")
    lines = markdown_text.splitlines()
    story = _build_flowables(lines)

    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(CN, 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(14 * mm, 12 * mm, "Project DPS 投前研究系统 · 功能总结与使用说明书")
        canvas.drawRightString(A4[0] - 14 * mm, 12 * mm, f"第 {doc.page} 页")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(DESTINATION),
        pagesize=A4,
        leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=16 * mm, bottomMargin=20 * mm,
        title="Project DPS 投前研究系统 · 功能总结与使用说明书",
        author="Project DPS",
    )
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)


def main() -> int:
    _build_document()
    size_kb = DESTINATION.stat().st_size / 1024
    print(f"PDF 已生成：{DESTINATION}")
    print(f"大小：{size_kb:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
