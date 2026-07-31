"""研究报告的 Markdown、HTML、PDF 和完整报告包导出。"""

from __future__ import annotations

import base64
import json
import shutil
import zipfile
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from qteasy_research.pretrade.schemas import ResearchExportResult
from qteasy_research.pretrade.storage import ResearchStore


_ALLOWED_FORMATS = {"md", "html", "pdf", "bundle"}
_STATUS_LABELS = {
    "COMPLETED": "已完成",
    "PARTIAL": "部分完成",
    "FAILED": "失败",
    "REVIEW": "待人工复核",
}
_CSS = """
:root { --ink:#173434; --muted:#667878; --paper:#f7f4ec; --panel:#fffdf8; --gold:#b8872f; --line:#d8d2c4; --warn:#a94d3d; }
* { box-sizing:border-box; }
body { margin:0; color:var(--ink); background:var(--paper); font-family:"Microsoft YaHei","Noto Sans CJK SC","Segoe UI",sans-serif; line-height:1.65; }
.page { max-width:1120px; margin:0 auto; padding:42px 56px 64px; background:var(--panel); min-height:100vh; }
.hero { border-left:6px solid var(--gold); padding:8px 0 14px 22px; margin-bottom:28px; }
.eyebrow { color:var(--gold); font-size:12px; letter-spacing:.16em; text-transform:uppercase; font-weight:700; }
.hero h1 { margin:8px 0 6px; font-size:30px; line-height:1.25; }
.meta { color:var(--muted); font-family:Consolas,"Cascadia Mono",monospace; font-size:12px; }
.summary { background:#eef2ec; border:1px solid #d4dfd4; border-radius:8px; padding:16px 20px; margin:20px 0 30px; }
h1,h2,h3 { color:var(--ink); }
h2 { border-top:2px solid var(--gold); padding-top:15px; margin-top:34px; }
h3 { margin-top:24px; }
table { width:100%; border-collapse:collapse; margin:12px 0 22px; font-size:13px; }
th { background:#e8eee8; color:var(--ink); text-align:left; font-weight:700; }
th,td { border:1px solid var(--line); padding:7px 9px; vertical-align:top; }
tr:nth-child(even) td { background:#fcfaf4; }
code, pre { font-family:Consolas,"Cascadia Mono",monospace; }
code { background:#f0eee7; padding:1px 4px; border-radius:3px; }
blockquote { border-left:4px solid var(--gold); margin:14px 0; padding:8px 16px; color:var(--muted); background:#fbf8ef; }
img.chart { display:block; max-width:100%; height:auto; margin:18px auto 28px; border:1px solid var(--line); }
.draft { border:1px dashed var(--gold); background:#fff9e9; padding:16px 20px; margin-top:32px; }
.footer { border-top:1px solid var(--line); margin-top:42px; padding-top:12px; color:var(--muted); font-size:11px; }
@media print { body { background:#fff; } .page { max-width:none; padding:18mm 16mm; } h2 { break-before:auto; } table { break-inside:auto; } tr { break-inside:avoid; } }
"""

# Qt's QTextDocument supports a smaller CSS subset than a browser.  Keep a
# separate print stylesheet so CSS variables and newer layout declarations do
# not turn Chinese glyphs into opaque blocks in the PDF renderer.
_PDF_CSS = """
body { margin:0; color:#173434; background:#ffffff; font-family:"Microsoft YaHei"; font-size:10pt; line-height:1.5; }
.page { padding:20px; background:#ffffff; }
.hero { border-left:5px solid #b8872f; padding:6px 0 10px 16px; margin-bottom:18px; }
.eyebrow { color:#b8872f; font-size:9pt; font-weight:bold; }
.hero h1 { margin:6px 0; font-size:20pt; }
.meta { color:#667878; font-family:Consolas; font-size:8pt; }
.summary { background:#eef2ec; border:1px solid #d4dfd4; padding:10px 14px; margin:12px 0 18px; }
h1,h2,h3 { color:#173434; }
h2 { border-top:1px solid #b8872f; padding-top:8px; margin-top:20px; }
h3 { margin-top:14px; }
table { width:100%; border-collapse:collapse; margin:8px 0 14px; font-size:9pt; }
th { background:#e8eee8; color:#173434; text-align:left; font-weight:bold; }
th,td { border:1px solid #d8d2c4; padding:4px 6px; }
code, pre { font-family:Consolas; }
code { background:#f0eee7; }
blockquote { border-left:3px solid #b8872f; margin:8px 0; padding:5px 10px; color:#667878; background:#fbf8ef; }
img.chart { display:block; max-width:100%; height:auto; margin:10px auto 18px; }
.draft { border:1px dashed #b8872f; background:#fff9e9; padding:10px 14px; margin-top:20px; }
.footer { border-top:1px solid #d8d2c4; margin-top:20px; padding-top:8px; color:#667878; font-size:8pt; }
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_root() -> Path:
    return Path(__file__).resolve().parents[2] / "research_store"


def _load_run(store: ResearchStore, run_id: str) -> tuple[dict[str, Any], dict[str, Any], Path]:
    from qteasy_research.pretrade.orchestrator import _result_from_dict

    row = store.get_run(run_id)
    if not row:
        deleted = store.get_run(run_id, include_deleted=True)
        if deleted and deleted.get("deleted_at"):
            raise ValueError("已删除的研究版本不能直接导出，请先恢复后再导出")
        raise KeyError(f"研究版本不存在：{run_id}")
    result_path = Path(str(row.get("result_path", "")))
    if not result_path.exists():
        raise FileNotFoundError(f"研究结果文件不存在：{result_path}")
    return row, _result_from_dict(store.read_json(result_path)).to_dict(), result_path


def _draft_markdown(store: ResearchStore, run_id: str, include_draft: bool) -> tuple[str, str | None]:
    draft = store.get_report_draft(run_id) if include_draft else None
    if not draft:
        return "", None
    sections = [
        ("人工摘要", draft.manual_summary),
        ("人工确认结论", draft.manual_conclusion),
        ("风险判断", draft.risk_judgment),
        ("证伪/退出条件", draft.falsification_conditions),
        ("后续研究计划", draft.followup_plan),
    ]
    lines = ["", "## 人工研究补充（草稿）", ""]
    for title, content in sections:
        if content.strip():
            lines.extend([f"### {title}", "", content.strip(), ""])
    return "\n".join(lines).rstrip() + "\n", draft.draft_id


def _markdown_to_html(markdown_text: str) -> str:
    try:
        import mistune

        renderer = mistune.create_markdown(escape=True, plugins=["table", "strikethrough"])
        return renderer(markdown_text)
    except ImportError as exc:
        raise RuntimeError("HTML 导出需要 mistune，请先安装 mistune") from exc


def _image_data_uri(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _render_html(markdown_text: str, result: dict[str, Any], chart_paths: list[Path], generated_at: str) -> str:
    identity = result.get("asset_identity", {})
    code = identity.get("code") or "未知标的"
    name = identity.get("name") or code
    version = result.get("version_no", "未知")
    status = _STATUS_LABELS.get(str(result.get("run_status", "")).upper(), result.get("run_status", "未知"))
    body = _markdown_to_html(markdown_text)
    charts = []
    for chart in chart_paths:
        charts.append(
            f'<figure><img class="chart" src="{_image_data_uri(chart)}" alt="{escape(chart.stem)}"><figcaption>{escape(chart.stem)}</figcaption></figure>'
        )
    chart_html = "".join(charts)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(str(name))} 投前研究报告</title><style>{_CSS}</style></head>
<body><div class="page">
<div class="hero"><div class="eyebrow">PRE-TRADE RESEARCH DOSSIER</div><h1>{escape(str(name))}</h1><div class="meta">{escape(str(code))} · 研究版本 v{escape(str(version))} · 状态：{escape(str(status))} · 数据截至：{escape(str(result.get('data_as_of') or '未知'))}</div></div>
<div class="summary"><strong>研究边界：</strong>定量指标由确定性程序生成；来源事实、联网证据、AI 综合推断和人工补充分别标识。</div>
<div class="article">{body}</div>
{f'<div><h2>图表</h2>{chart_html}</div>' if chart_html else ''}
<div class="footer">导出时间：{escape(generated_at)} · 研究运行：{escape(str(result.get('run_id')))}</div>
</div></body></html>"""


def _write_pdf(html_text: str, destination: Path) -> None:
    try:
        from PySide6.QtCore import QMarginsF
        from PySide6.QtGui import QFont, QPageLayout, QPageSize, QTextDocument
        from PySide6.QtPrintSupport import QPrinter
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        raise RuntimeError("缺少 PySide6，无法生成 PDF") from exc

    app = QApplication.instance()
    owned_app = None
    if app is None:
        owned_app = QApplication([])
    document = QTextDocument()
    document.setDefaultFont(QFont("Microsoft YaHei", 10))
    pdf_html = html_text.replace(f"<style>{_CSS}</style>", f"<style>{_PDF_CSS}</style>")
    document.setHtml(pdf_html)
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(destination))
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    printer.setPageMargins(QMarginsF(12, 12, 12, 12), QPageLayout.Unit.Millimeter)
    document.print_(printer)
    if owned_app is not None:
        owned_app.quit()
    if not destination.exists() or destination.stat().st_size < 100:
        raise RuntimeError("PDF 文件未成功生成")


def export_research_report(
    run_id: str,
    *,
    formats: tuple[str, ...] = ("md", "html", "pdf"),
    include_draft: bool = True,
    destination_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> ResearchExportResult:
    requested = list(dict.fromkeys(formats))
    invalid = set(requested) - _ALLOWED_FORMATS
    if invalid:
        raise ValueError(f"不支持的导出格式：{sorted(invalid)}")
    if "bundle" in requested:
        requested = [item for item in requested if item != "bundle"]
        for item in ("md", "html", "pdf"):
            if item not in requested:
                requested.append(item)
    store = ResearchStore(output_dir or _default_root())
    row, result, source_result_path = _load_run(store, run_id)
    source_dir = source_result_path.parent
    identity = result.get("asset_identity", {})
    code = str(identity.get("code") or "asset").replace("/", "_")
    version = str(result.get("version_no") or "1")
    if destination_dir:
        destination = Path(destination_dir) / f"{code}_v{version}_{run_id}"
    else:
        destination = source_dir
    destination.mkdir(parents=True, exist_ok=True)
    chart_source = source_dir / "charts"
    chart_dest = destination / "charts"
    chart_paths: list[Path] = []
    if chart_source.exists():
        chart_dest.mkdir(parents=True, exist_ok=True)
        for source_chart in chart_source.glob("*.png"):
            target_chart = chart_dest / source_chart.name
            if source_chart.resolve() != target_chart.resolve():
                shutil.copy2(source_chart, target_chart)
            chart_paths.append(target_chart)
    original_report = source_dir / "report.md"
    if not original_report.exists():
        original_report.write_text(str(result.get("report", "")), encoding="utf-8")
    original_text = original_report.read_text(encoding="utf-8")
    draft_text, draft_id = _draft_markdown(store, run_id, include_draft)
    complete_text = original_text.rstrip() + (draft_text if draft_text else "") + "\n"
    generated_at = _now()
    export = ResearchExportResult(run_id=run_id, formats_requested=list(formats), generated_at=generated_at)
    statuses: dict[str, str] = {}

    try:
        shutil.copy2(original_report, destination / "report.md")
        (destination / "report_with_draft.md").write_text(complete_text, encoding="utf-8")
        shutil.copy2(source_result_path, destination / "result.json")
        export.paths["md"] = str(destination / "report_with_draft.md")
        export.formats_generated.append("md")
        statuses["md"] = "success"
    except Exception as exc:
        statuses["md"] = f"failed: {type(exc).__name__}: {exc}"
        export.warnings.append(f"Markdown 导出失败：{type(exc).__name__}: {exc}")

    if "html" in requested:
        try:
            html_path = destination / "report.html"
            html_path.write_text(_render_html(complete_text, result, chart_paths, generated_at), encoding="utf-8")
            export.paths["html"] = str(html_path)
            export.formats_generated.append("html")
            statuses["html"] = "success"
        except Exception as exc:
            statuses["html"] = f"failed: {type(exc).__name__}: {exc}"
            export.warnings.append(f"HTML 导出失败：{type(exc).__name__}: {exc}")

    if "pdf" in requested:
        try:
            html_for_pdf = _render_html(complete_text, result, chart_paths, generated_at)
            pdf_path = destination / "report.pdf"
            _write_pdf(html_for_pdf, pdf_path)
            export.paths["pdf"] = str(pdf_path)
            export.formats_generated.append("pdf")
            statuses["pdf"] = "success"
        except Exception as exc:
            statuses["pdf"] = f"failed: {type(exc).__name__}: {exc}"
            export.warnings.append(f"PDF 导出失败：{type(exc).__name__}: {exc}")

    manifest = {
        "run_id": run_id,
        "code": identity.get("code"),
        "version_no": result.get("version_no"),
        "data_as_of": result.get("data_as_of"),
        "generated_at": generated_at,
        "include_draft": include_draft,
        "formats": statuses,
        "source_report": str(original_report),
        "draft_id": draft_id,
        "warnings": export.warnings,
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    export.paths["manifest"] = str(manifest_path)

    if "bundle" in formats:
        try:
            bundle_parent = destination.parent if destination_dir else destination
            bundle_path = bundle_parent / f"{code}_v{version}_{run_id}.zip"
            with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(destination.rglob("*")):
                    if path.is_file() and path != bundle_path:
                        archive.write(path, path.relative_to(destination).as_posix())
            export.paths["bundle"] = str(bundle_path)
            export.formats_generated.append("bundle")
            statuses["bundle"] = "success"
        except Exception as exc:
            statuses["bundle"] = f"failed: {type(exc).__name__}: {exc}"
            export.warnings.append(f"完整报告包生成失败：{type(exc).__name__}: {exc}")
        manifest["formats"] = statuses
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    store.save_payload("research_report", run_id, {
        "exported_at": generated_at,
        "paths": export.paths,
        "formats": statuses,
        "include_draft": include_draft,
    })
    return export


def export_project_report(
    project_id: str,
    *,
    run_id: str | None = None,
    formats: tuple[str, ...] = ("md", "html", "pdf"),
    include_draft: bool = True,
    destination_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> ResearchExportResult:
    store = ResearchStore(output_dir or _default_root())
    target = store.get_run(run_id) if run_id else store.latest_project_run(project_id)
    if not target:
        raise KeyError(f"项目没有可导出的有效研究版本：{project_id}")
    if target.get("project_id") != project_id:
        raise ValueError("研究版本不属于指定项目")
    return export_research_report(
        target["run_id"], formats=formats, include_draft=include_draft,
        destination_dir=destination_dir, output_dir=output_dir,
    )


def export_report_bundle(
    run_id: str,
    *,
    include_draft: bool = True,
    destination_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> ResearchExportResult:
    return export_research_report(
        run_id,
        formats=("bundle",),
        include_draft=include_draft,
        destination_dir=destination_dir,
        output_dir=output_dir,
    )
