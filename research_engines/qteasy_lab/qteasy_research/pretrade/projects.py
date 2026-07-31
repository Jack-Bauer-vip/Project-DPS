"""研究项目、项目版本和人工研究内容的公共 API。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from qteasy_research.pretrade.providers import LocalCsvProvider
from qteasy_research.pretrade.schemas import (
    PortfolioAssetContext,
    ResearchReportDraft,
    ResearchProject,
    ResearchProjectStatus,
    ResearchProjectType,
)
from qteasy_research.pretrade.storage import ResearchStore
from qteasy_research.pretrade.symbols import normalize_code


def _default_root() -> Path:
    return Path(__file__).resolve().parents[2] / "research_store"


def _store(root: str | Path | None = None) -> ResearchStore:
    return ResearchStore(root or _default_root())


def create_research_project(
    name: str,
    code: str,
    objective: str | None = None,
    *,
    project_type: str = ResearchProjectType.ASSET_PROFILE.value,
    strategy_name: str | None = None,
    settings: dict[str, Any] | None = None,
    output_dir: str | Path | None = None,
) -> ResearchProject:
    if project_type not in {item.value for item in ResearchProjectType}:
        raise ValueError(f"无效的研究项目类型：{project_type}")
    normalized = "PORTFOLIO" if project_type == ResearchProjectType.STRATEGY_PORTFOLIO.value else normalize_code(code)[0]
    identity = LocalCsvProvider().resolve(normalized) if normalized != "PORTFOLIO" else None
    project = ResearchProject(
        project_id=uuid.uuid4().hex,
        name=name.strip() or (f"{identity.name or normalized} 投前研究" if identity else "策略组合研究"),
        code=normalized,
        objective=objective,
        project_type=project_type,
        strategy_name=strategy_name,
        settings=settings or {},
        status=ResearchProjectStatus.DRAFT.value,
    )
    metadata = {
        "asset_type": identity.asset_type if identity else "PORTFOLIO",
        "exchange": identity.exchange if identity else None,
        "name": identity.name if identity else project.name,
        "benchmark": identity.benchmark if identity else None,
        "metadata": identity.metadata if identity else {},
    }
    return _store(output_dir).create_project(project, metadata=metadata)


def create_strategy_project(
    name: str,
    strategy_name: str,
    objective: str | None = None,
    *,
    horizon: str = "medium",
    settings: dict[str, Any] | None = None,
    output_dir: str | Path | None = None,
) -> ResearchProject:
    project = create_research_project(
        name,
        "PORTFOLIO",
        objective,
        project_type=ResearchProjectType.STRATEGY_PORTFOLIO.value,
        strategy_name=strategy_name,
        settings=settings,
        output_dir=output_dir,
    )
    return update_research_project(project.project_id, output_dir=output_dir, horizon=horizon)


def update_research_project(project_id: str, *, output_dir: str | Path | None = None, **changes: Any) -> ResearchProject:
    return _store(output_dir).update_project(project_id, **changes)


def update_project_portfolio_assets(
    project_id: str,
    assets: list[dict[str, Any]],
    *,
    output_dir: str | Path | None = None,
) -> ResearchProject:
    """保存组合项目的标的、目标比例和独立触发条件草稿。

    这里不把组合强行转换成固定回测策略；不同标的的买入/卖出条件先作为
    项目配置保留，后续由用户选择具体策略实现。
    """
    project = get_research_project(project_id, output_dir=output_dir)
    if project.project_type != ResearchProjectType.STRATEGY_PORTFOLIO.value:
        raise ValueError("只有策略/组合项目可以保存组合标的")
    normalized_assets: list[dict[str, Any]] = []
    for item in assets:
        raw_code = str(item.get("code", "")).strip()
        if not raw_code:
            continue
        code, _ = normalize_code(raw_code)
        try:
            weight = float(item.get("weight", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"组合比例无效：{raw_code}") from exc
        if weight < 0 or weight > 1:
            raise ValueError(f"组合比例必须在 0 到 1 之间：{raw_code}")
        horizon = str(item.get("horizon", "medium")).strip() or "medium"
        if horizon not in {"short", "medium", "long"}:
            raise ValueError(f"投资期限必须是 short、medium 或 long：{raw_code}")
        normalized_assets.append({
            "code": code,
            "weight": weight,
            "horizon": horizon,
            "buy_condition": str(item.get("buy_condition", "")).strip(),
            "sell_condition": str(item.get("sell_condition", "")).strip(),
            "take_profit_condition": str(item.get("take_profit_condition", item.get("take_profit", ""))).strip(),
            "stop_loss_condition": str(item.get("stop_loss_condition", item.get("stop_loss", ""))).strip(),
            "hypothesis": str(item.get("hypothesis", "")).strip(),
            "role": str(item.get("role", "candidate")).strip() or "candidate",
            "enabled": bool(item.get("enabled", True)),
        })
    settings = dict(project.settings or {})
    settings["portfolio_assets"] = normalized_assets
    store = _store(output_dir)
    updated = store.update_project(project_id, settings=settings)
    active_codes = {item["code"] for item in normalized_assets}
    for existing in store.list_portfolio_asset_contexts(project_id=project_id):
        if existing.code not in active_codes:
            store.delete_portfolio_asset_context(project_id, existing.code)
    for item in normalized_assets:
        store.upsert_portfolio_asset_context(PortfolioAssetContext(
            context_id="",
            project_id=project_id,
            code=item["code"],
            horizon=item["horizon"],
            weight=item["weight"],
            role=item["role"],
            buy_condition=item["buy_condition"],
            sell_condition=item["sell_condition"],
            take_profit_condition=item["take_profit_condition"],
            stop_loss_condition=item["stop_loss_condition"],
            hypothesis=item["hypothesis"],
            enabled=item["enabled"],
        ))
    return updated


def list_project_portfolio_assets(project_id: str, *, output_dir: str | Path | None = None) -> list[dict[str, Any]]:
    store = _store(output_dir)
    contexts = store.list_portfolio_asset_contexts(project_id=project_id)
    if contexts:
        return [
            {
                "code": item.code,
                "weight": item.weight,
                "horizon": item.horizon,
                "role": item.role,
                "buy_condition": item.buy_condition,
                "sell_condition": item.sell_condition,
                "take_profit_condition": item.take_profit_condition,
                "stop_loss_condition": item.stop_loss_condition,
                "hypothesis": item.hypothesis,
                "enabled": item.enabled,
            }
            for item in contexts
        ]
    project = store.get_project(project_id)
    return list((project.settings or {}).get("portfolio_assets", []))


def update_portfolio_asset_context(
    project_id: str,
    code: str,
    *,
    horizon: str = "medium",
    weight: float = 0.0,
    role: str = "candidate",
    buy_condition: str | None = None,
    sell_condition: str | None = None,
    take_profit_condition: str | None = None,
    stop_loss_condition: str | None = None,
    hypothesis: str | None = None,
    enabled: bool = True,
    output_dir: str | Path | None = None,
) -> PortfolioAssetContext:
    normalized, _ = normalize_code(code)
    if horizon not in {"short", "medium", "long"}:
        raise ValueError("期限必须是 short、medium 或 long")
    if not 0 <= float(weight) <= 1:
        raise ValueError("组合比例必须在 0 到 1 之间")
    return _store(output_dir).upsert_portfolio_asset_context(PortfolioAssetContext(
        context_id="", project_id=project_id, code=normalized, horizon=horizon,
        weight=float(weight), role=role, buy_condition=buy_condition or "",
        sell_condition=sell_condition or "", take_profit_condition=take_profit_condition or "",
        stop_loss_condition=stop_loss_condition or "", hypothesis=hypothesis or "", enabled=enabled,
    ))


def list_portfolio_asset_contexts(
    *,
    project_id: str | None = None,
    code: str | None = None,
    output_dir: str | Path | None = None,
) -> list[PortfolioAssetContext]:
    normalized = normalize_code(code)[0] if code else None
    return _store(output_dir).list_portfolio_asset_contexts(project_id=project_id, code=normalized)


def get_research_project(project_id: str, *, output_dir: str | Path | None = None) -> ResearchProject:
    return _store(output_dir).get_project(project_id)


def list_research_projects(status: str | None = None, *, output_dir: str | Path | None = None) -> list[ResearchProject]:
    return _store(output_dir).list_projects(status)


def close_research_project(project_id: str, *, output_dir: str | Path | None = None) -> ResearchProject:
    return _store(output_dir).set_project_status(project_id, ResearchProjectStatus.CLOSED.value)


def archive_research_project(project_id: str, *, output_dir: str | Path | None = None) -> ResearchProject:
    return _store(output_dir).set_project_status(project_id, ResearchProjectStatus.ARCHIVED.value)


def reopen_research_project(project_id: str, *, output_dir: str | Path | None = None) -> ResearchProject:
    """显式重新打开冻结项目，所有历史版本仍保持只读。"""
    return _store(output_dir).set_project_status(project_id, ResearchProjectStatus.ACTIVE.value)


def add_project_note(project_id: str, content: str, *, title: str | None = None, output_dir: str | Path | None = None):
    return _store(output_dir).add_note(project_id, content, title=title)


def list_project_notes(project_id: str, *, include_deleted: bool = False, output_dir: str | Path | None = None):
    return _store(output_dir).list_notes(project_id, include_deleted=include_deleted)


def update_project_note(note_id: str, content: str, *, title: str | None = None, output_dir: str | Path | None = None):
    return _store(output_dir).update_note(note_id, content, title=title)


def delete_project_note(note_id: str, *, output_dir: str | Path | None = None) -> None:
    _store(output_dir).delete_note(note_id)


def add_project_decision(project_id: str, content: str, *, decision_type: str = "manual_confirmation", author: str = "user", output_dir: str | Path | None = None):
    return _store(output_dir).add_decision(project_id, content, decision_type=decision_type, author=author)


def list_project_decisions(project_id: str, *, include_deleted: bool = False, output_dir: str | Path | None = None):
    return _store(output_dir).list_decisions(project_id, include_deleted=include_deleted)


def update_project_decision(decision_id: str, content: str, *, decision_type: str | None = None, author: str | None = None, output_dir: str | Path | None = None):
    return _store(output_dir).update_decision(decision_id, content, decision_type=decision_type, author=author)


def delete_project_decision(decision_id: str, *, output_dir: str | Path | None = None) -> None:
    _store(output_dir).delete_decision(decision_id)


def add_asset_reference(
    project_id: str,
    asset_project_id: str,
    *,
    asset_version_no: int | None = None,
    role: str = "candidate",
    weight_limit: float | None = None,
    output_dir: str | Path | None = None,
):
    return _store(output_dir).add_asset_reference(
        project_id,
        asset_project_id,
        asset_version_no=asset_version_no,
        role=role,
        weight_limit=weight_limit,
    )


def list_asset_references(project_id: str, *, include_deleted: bool = False, output_dir: str | Path | None = None):
    return _store(output_dir).list_asset_references(project_id, include_deleted=include_deleted)


def delete_asset_reference(reference_id: str, *, output_dir: str | Path | None = None) -> None:
    _store(output_dir).delete_asset_reference(reference_id)


def list_project_versions(
    project_id: str,
    *,
    include_deleted: bool = False,
    output_dir: str | Path | None = None,
) -> list[Any]:
    store = _store(output_dir)
    from qteasy_research.pretrade.orchestrator import _result_from_dict

    results = []
    for row in store.list_project_versions(project_id, include_deleted=include_deleted):
        result_path = row.get("result_path")
        if result_path and Path(result_path).exists():
            result = _result_from_dict(store.read_json(Path(result_path)))
            result.deleted_at = row.get("deleted_at")
            result.deleted_by = row.get("deleted_by")
            result.delete_reason = row.get("delete_reason")
            results.append(result)
    return results


def delete_research_report(
    run_id: str,
    *,
    reason: str | None = None,
    author: str = "user",
    output_dir: str | Path | None = None,
):
    from qteasy_research.pretrade.orchestrator import _result_from_dict

    store = _store(output_dir)
    row = store.delete_run(run_id, author=author, reason=reason)
    result_path = row.get("result_path")
    result = _result_from_dict(store.read_json(Path(result_path))) if result_path else None
    if result is None:
        raise FileNotFoundError(f"研究版本结果文件不存在：{run_id}")
    result.deleted_at = row.get("deleted_at")
    result.deleted_by = row.get("deleted_by")
    result.delete_reason = row.get("delete_reason")
    return result


def restore_research_report(run_id: str, *, output_dir: str | Path | None = None):
    from qteasy_research.pretrade.orchestrator import _result_from_dict

    store = _store(output_dir)
    row = store.restore_run(run_id)
    result_path = row.get("result_path")
    result = _result_from_dict(store.read_json(Path(result_path))) if result_path else None
    if result is None:
        raise FileNotFoundError(f"研究版本结果文件不存在：{run_id}")
    result.deleted_at = None
    result.deleted_by = None
    result.delete_reason = None
    return result


def save_report_draft(
    run_id: str,
    *,
    manual_summary: str = "",
    manual_conclusion: str = "",
    risk_judgment: str = "",
    falsification_conditions: str = "",
    followup_plan: str = "",
    author: str = "user",
    output_dir: str | Path | None = None,
) -> ResearchReportDraft:
    return _store(output_dir).save_report_draft(ResearchReportDraft(
        draft_id="",
        run_id=run_id,
        manual_summary=manual_summary,
        manual_conclusion=manual_conclusion,
        risk_judgment=risk_judgment,
        falsification_conditions=falsification_conditions,
        followup_plan=followup_plan,
        author=author,
    ))


def get_report_draft(run_id: str, *, output_dir: str | Path | None = None) -> ResearchReportDraft | None:
    return _store(output_dir).get_report_draft(run_id)


def discard_report_draft(run_id: str, *, output_dir: str | Path | None = None) -> None:
    _store(output_dir).discard_report_draft(run_id)


def compare_research_versions(
    run_id_a: str,
    run_id_b: str,
    *,
    include_deleted: bool = False,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    store = _store(output_dir)
    rows = []
    with store._connect() as connection:
        for run_id in (run_id_a, run_id_b):
            row = connection.execute(
                "SELECT * FROM research_run WHERE run_id=?" + ("" if include_deleted else " AND deleted_at IS NULL"),
                (run_id,),
            ).fetchone()
            if not row:
                raise KeyError(f"研究版本不存在：{run_id}")
            rows.append(dict(row))
    first = store.read_json(Path(rows[0]["result_path"]))
    second = store.read_json(Path(rows[1]["result_path"]))
    changed: dict[str, Any] = {}
    for field in ("asset_identity", "quantitative_metrics", "benchmark_analysis", "instrument_analysis", "strategy_fit", "risks", "missing_items"):
        if first.get(field) != second.get(field):
            changed[field] = {"before": first.get(field), "after": second.get(field)}
    first_technical = first.get("quantitative_metrics", {}).get("technical_analysis")
    second_technical = second.get("quantitative_metrics", {}).get("technical_analysis")
    if first_technical != second_technical:
        changed["technical_analysis"] = {"before": first_technical, "after": second_technical}
    first_scores = first.get("quantitative_metrics", {}).get("composite_scores")
    second_scores = second.get("quantitative_metrics", {}).get("composite_scores")
    if first_scores != second_scores:
        changed["composite_scores"] = {"before": first_scores, "after": second_scores}
    return {
        "run_a": {"run_id": run_id_a, "version_no": rows[0].get("version_no")},
        "run_b": {"run_id": run_id_b, "version_no": rows[1].get("version_no")},
        "changed": changed,
    }
