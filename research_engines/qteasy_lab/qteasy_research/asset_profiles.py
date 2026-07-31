"""资产档案、动态快照、因子结果和历史报告公共 API。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from qteasy_research.pretrade.factors import analyze_asset_factors as _analyze_asset_factors_frame
from qteasy_research.pretrade.providers import LocalCsvProvider
from qteasy_research.pretrade.schemas import AssetDynamicSnapshot, AssetFactorResult, AssetProfile
from qteasy_research.pretrade.storage import ResearchStore
from qteasy_research.pretrade.symbols import normalize_code


def _default_root() -> Path:
    return Path(__file__).resolve().parents[1] / "research_store"


def _store(output_dir: str | Path | None = None) -> ResearchStore:
    return ResearchStore(output_dir or _default_root())


def get_asset_profile(code: str, *, output_dir: str | Path | None = None) -> AssetProfile:
    normalized = normalize_code(code)[0]
    store = _store(output_dir)
    profile = store.get_asset_profile(normalized)
    if profile:
        return profile
    identity = LocalCsvProvider().resolve(normalized)
    profile = AssetProfile(
        code=identity.code,
        name=identity.name,
        asset_type=identity.asset_type,
        exchange=identity.exchange,
        benchmark=identity.benchmark,
        fixed_metadata=identity.metadata,
        source="local_csv",
    )
    return store.save_asset_profile(profile)


def list_asset_profile_snapshots(code: str, *, output_dir: str | Path | None = None) -> list[dict[str, Any]]:
    return _store(output_dir).list_asset_profile_snapshots(normalize_code(code)[0])


def list_asset_reports(
    code: str,
    *,
    include_deleted: bool = False,
    output_dir: str | Path | None = None,
) -> list[Any]:
    from qteasy_research.pretrade.orchestrator import _result_from_dict

    store = _store(output_dir)
    results = []
    for row in store.list_runs_for_code(normalize_code(code)[0], include_deleted=include_deleted):
        path = row.get("result_path")
        if path and Path(path).exists():
            result = _result_from_dict(store.read_json(Path(path)))
            result.deleted_at = row.get("deleted_at")
            result.deleted_by = row.get("deleted_by")
            result.delete_reason = row.get("delete_reason")
            results.append(result)
    return results


def get_latest_asset_report(code: str, *, output_dir: str | Path | None = None) -> Any | None:
    reports = list_asset_reports(code, output_dir=output_dir)
    return reports[0] if reports else None


def get_asset_dynamic_snapshot(code: str, *, as_of: str | None = None, output_dir: str | Path | None = None) -> AssetDynamicSnapshot | None:
    reports = list_asset_reports(code, output_dir=output_dir)
    if as_of:
        reports = [item for item in reports if item.data_as_of == as_of]
    if not reports:
        return None
    result = reports[0]
    return AssetDynamicSnapshot(
        code=result.asset_identity.code,
        run_id=result.run_id,
        as_of=result.data_as_of,
        quantitative_metrics=result.quantitative_metrics,
        benchmark_analysis=result.benchmark_analysis,
        cache_status=result.cache_status,
    )


def analyze_asset_factors(
    code: str,
    horizon: str = "medium",
    *,
    force_refresh: bool = False,
    output_dir: str | Path | None = None,
) -> AssetFactorResult:
    """读取最新动态研究并返回指定期限因子结果。

    首次调用没有历史研究时，使用本地可用数据建立一个离线研究版本；
    强制刷新仍通过既有研究编排层执行，避免因子模块重复实现数据采集。
    """
    result = get_latest_asset_report(code, output_dir=output_dir)
    if result is None or force_refresh:
        from qteasy_research.pretrade.orchestrator import run_instrument_research

        result = run_instrument_research(
            code,
            horizon=horizon,
            network_research=False,
            data_mode="local",
            update_policy="refresh" if force_refresh else "reuse",
            output_dir=str(output_dir or _default_root()),
        )
    data = result.quantitative_metrics.get("factor_analysis", {}).get(horizon, {})
    if not data:
        series = result.quantitative_metrics.get("series", {})
        import pandas as pd

        frame = pd.DataFrame({"trade_date": series.get("dates", []), "close": series.get("close", [])})
        data = _analyze_asset_factors_frame(
            result.asset_identity.code,
            frame,
            quantitative_metrics=result.quantitative_metrics,
            benchmark_analysis=result.benchmark_analysis,
            horizon=horizon,
        )
    return AssetFactorResult(
        code=result.asset_identity.code,
        horizon=horizon,
        as_of=data.get("as_of"),
        formula_version=data.get("formula_version", "asset-factors-v1"),
        factors=data.get("factors", []),
        composite_score=data.get("composite_score"),
        state=data.get("state", "unavailable"),
        consistency=data.get("consistency", "unavailable"),
        summary=data.get("summary", ""),
    )


def list_asset_factor_snapshots(code: str, horizon: str | None = None, *, output_dir: str | Path | None = None) -> list[dict[str, Any]]:
    return _store(output_dir).list_asset_factor_snapshots(normalize_code(code)[0], horizon=horizon)


def compare_asset_reports(run_id_a: str, run_id_b: str, *, output_dir: str | Path | None = None) -> dict[str, Any]:
    from qteasy_research.pretrade.projects import compare_research_versions

    return compare_research_versions(run_id_a, run_id_b, output_dir=output_dir)
