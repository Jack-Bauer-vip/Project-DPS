"""投前研究任务编排。"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from qteasy_research.pretrade.metrics import analyze_benchmark, analyze_price_history
from qteasy_research.pretrade.factors import analyze_asset_factors
from qteasy_research.pretrade.llm import ResearchPrompt, build_llm_provider
from qteasy_research.pretrade.providers import (
    AkshareProvider,
    CompositeProvider,
    LocalCsvProvider,
    ResearchSnapshotProvider,
    TushareProvider,
)
from qteasy_research.pretrade.reporting import render_charts, render_markdown
from qteasy_research.pretrade.schemas import (
    AssetProfile,
    AssetIdentity,
    ResearchConfig,
    ResearchProjectStatus,
    ResearchRunResult,
    ResearchStatus,
    StageRecord,
)
from qteasy_research.pretrade.storage import ResearchStore
from qteasy_research.pretrade.symbols import normalize_code
from qteasy_research.pretrade.technical import (
    FORMULA_VERSION,
    TechnicalIndicatorConfig,
    analyze_technical_indicators,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_root() -> Path:
    return Path(__file__).resolve().parents[2] / "research_store"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    return value


def _cache_key(config: ResearchConfig, local_dir: Path) -> str:
    files = []
    for name in ("fund_daily.csv", "fund_basic.csv", "index_daily.csv", "stock_daily.csv"):
        path = local_dir / name
        if path.exists():
            stat = path.stat()
            files.append((name, stat.st_size, stat.st_mtime_ns))
    try:
        normalized_code = normalize_code(config.code)[0]
    except ValueError:
        normalized_code = config.code.strip().upper()
    payload = {
        "code": normalized_code,
        "benchmark": config.benchmark,
        "horizon": config.horizon,
        "llm_provider": config.llm_provider,
        "llm_model": config.llm_model,
        "network_research": config.network_research,
        "data_mode": config.data_mode,
        "evidence_research": config.evidence_research,
        "local_data_provider": config.local_data_provider,
        "as_of_date": config.as_of_date,
        "factor_params": config.factor_params,
        "transaction_cost": config.transaction_cost,
        "indicator_config": config.indicator_config,
        "technical_formula_version": FORMULA_VERSION,
        "files": files,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _technical_config(config: ResearchConfig) -> TechnicalIndicatorConfig:
    return TechnicalIndicatorConfig.from_dict(config.indicator_config)


def _technical_config_hash(config: ResearchConfig) -> str:
    payload = {
        "formula_version": FORMULA_VERSION,
        "config": _technical_config(config).to_dict(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _frame_content_hash(frame: pd.DataFrame) -> str:
    normalized = frame.copy()
    normalized = normalized.sort_values("trade_date") if "trade_date" in normalized else normalized
    payload = normalized.to_json(orient="records", date_format="iso")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _apply_as_of(provider_data: Any, as_of_date: str | None) -> Any:
    """按研究时点截断行情，避免把后来下载到的数据带入历史回放。"""

    if not as_of_date or not getattr(provider_data, "ok", False):
        return provider_data
    frame = provider_data.data.copy()
    if "trade_date" not in frame:
        return provider_data
    cutoff = pd.Timestamp(as_of_date)
    dates = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame = frame.loc[dates <= cutoff].copy()
    provider_data.data = frame.reset_index(drop=True)
    if frame.empty:
        provider_data.as_of = None
    else:
        provider_data.as_of = pd.to_datetime(frame["trade_date"], errors="coerce").max().strftime("%Y-%m-%d")
    return provider_data


def _add_stage(result: ResearchRunResult, store: ResearchStore, name: str, status: str, message: str = "", details: dict[str, Any] | None = None, progress_callback: Callable[[StageRecord], None] | None = None) -> None:
    stage = StageRecord(name=name, status=status, message=message, started_at=_now(), finished_at=_now(), details=details or {})
    result.stages.append(stage)
    store.save_stage(result.run_id, stage)
    callback = progress_callback or getattr(result, "_progress_callback", None)
    if callback:
        callback(stage)


def _provider_chain(data_mode: str, local: LocalCsvProvider, snapshot: ResearchSnapshotProvider) -> CompositeProvider:
    if data_mode == "local":
        providers = [local, snapshot]
    elif data_mode == "hybrid":
        providers = [local, snapshot, AkshareProvider(), TushareProvider()]
    else:
        providers = [AkshareProvider(), TushareProvider(), local, snapshot]
    return CompositeProvider(providers)


def run_instrument_research(
    code: str,
    benchmark: str | None = None,
    horizon: str = "medium",
    force_refresh: bool = False,
    *,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    network_research: bool = True,
    output_dir: str | None = None,
    project_id: str | None = None,
    update_policy: str = "reuse",
    data_mode: str = "direct",
    evidence_research: bool = True,
    local_data_provider: str | None = None,
    indicator_config: dict[str, Any] | None = None,
    as_of_date: str | None = None,
    factor_params: dict[str, Any] | None = None,
    transaction_cost: dict[str, Any] | None = None,
    progress_callback: Callable[[StageRecord], None] | None = None,
) -> ResearchRunResult:
    """执行一个标的的可复现投前研究任务。"""
    if update_policy not in {"reuse", "check_update", "refresh", "force_refresh"}:
        raise ValueError(f"无效的数据更新策略：{update_policy}")
    if force_refresh:
        update_policy = "force_refresh"
    if data_mode not in {"direct", "hybrid", "local"}:
        raise ValueError(f"无效的数据模式：{data_mode}")
    if not network_research:
        data_mode = "local"
        evidence_research = False
    config = ResearchConfig(
        code=code,
        benchmark=benchmark,
        horizon=horizon,
        force_refresh=force_refresh,
        llm_provider=llm_provider,
        llm_model=llm_model,
        network_research=network_research,
        output_dir=output_dir,
        project_id=project_id,
        update_policy=update_policy,
        data_mode=data_mode,
        evidence_research=evidence_research,
        local_data_provider=local_data_provider,
        indicator_config=indicator_config or {},
        as_of_date=as_of_date,
        factor_params=factor_params or {},
        transaction_cost=transaction_cost or {},
    )
    local = LocalCsvProvider()
    store = ResearchStore(output_dir or _default_root())
    snapshot = ResearchSnapshotProvider(store.root)
    project = store.get_project(project_id) if project_id else None
    if project:
        if normalize_code(code)[0] != normalize_code(project.code)[0]:
            raise ValueError(f"研究代码与项目标的不一致：{code} != {project.code}")
        if project.status in {ResearchProjectStatus.CLOSED.value, ResearchProjectStatus.ARCHIVED.value}:
            raise ValueError("已关闭或已归档项目不可直接生成新版本，请先重新打开项目")
        if project.status != ResearchProjectStatus.ACTIVE.value:
            store.set_project_status(project_id, ResearchProjectStatus.ACTIVE.value)
    cache_key = _cache_key(config, local.data_dir)
    provider = _provider_chain(data_mode, local, snapshot)
    preflight_price = None
    if update_policy in {"reuse", "check_update"} and not force_refresh:
        cached = store.find_cached(cache_key, project_id=project_id)
        if cached:
            cached_result = _result_from_dict(ResearchStore.read_json(cached))
            if update_policy == "reuse":
                cached_result.cache_status = {**cached_result.cache_status, "policy": update_policy, "run_reused": True}
                return cached_result
            try:
                identity = local.resolve(code)
                preflight_price = _apply_as_of(provider.get_price_history(identity), as_of_date)
                market_snapshot = next(
                    (item for item in cached_result.cache_status.get("snapshots", []) if item.get("data_type") == "market"),
                    {},
                )
                if not preflight_price.ok:
                    cached_result.cache_status = {
                        **cached_result.cache_status,
                        "policy": update_policy,
                        "run_reused": True,
                        "update_checked": True,
                        "update_check_error": preflight_price.message,
                    }
                    return cached_result
                current_hash = _frame_content_hash(preflight_price.data)
                if current_hash == market_snapshot.get("content_hash") and preflight_price.as_of == cached_result.data_as_of:
                    cached_result.cache_status = {
                        **cached_result.cache_status,
                        "policy": update_policy,
                        "run_reused": True,
                        "update_checked": True,
                        "latest_data_as_of": preflight_price.as_of,
                    }
                    return cached_result
            except Exception as exc:
                cached_result.cache_status = {
                    **cached_result.cache_status,
                    "policy": update_policy,
                    "run_reused": True,
                    "update_checked": True,
                    "update_check_error": f"{type(exc).__name__}: {exc}",
                }
                return cached_result

    run_id = uuid.uuid4().hex
    parent = store.latest_project_run(project_id) if project_id else None
    next_version_no = store.next_project_version_no(project_id) if project_id else 1
    result = ResearchRunResult(
        run_id=run_id,
        research_case_id=(parent or {}).get("research_case_id", uuid.uuid4().hex),
        asset_identity=AssetIdentity(code=code),
        project_id=project_id,
        parent_run_id=(parent or {}).get("run_id"),
        version_no=next_version_no,
        cache_status={
            "policy": update_policy,
            "run_reused": False,
            "data_mode": data_mode,
            "evidence_research": evidence_research,
            "as_of_date": as_of_date,
            "factor_params": factor_params or {},
            "local_data_provider": local_data_provider,
            "provider_order": [],
            "technical_formula_version": FORMULA_VERSION,
            "technical_config_hash": _technical_config_hash(config),
        },
    )
    result._progress_callback = progress_callback
    asset_profile = None
    store.create_run(
        run_id=run_id,
        case_id=result.research_case_id,
        code=code,
        cache_key=cache_key,
        project_id=project_id,
        parent_run_id=result.parent_run_id,
        version_no=result.version_no,
    )
    root = Path(output_dir or _default_root())
    relative_run_root = Path("projects") / project_id / "versions" if project_id else Path("runs")
    run_dir = root / relative_run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    result.cache_status["provider_order"] = [item.name for item in provider.providers]

    try:
        _add_stage(result, store, "identifying", ResearchStatus.IDENTIFYING.value)
        result.asset_identity = local.resolve(code)
        candidate_benchmark = benchmark or result.asset_identity.benchmark
        if not isinstance(candidate_benchmark, str) or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", candidate_benchmark.upper()):
            candidate_benchmark = "000300.SH"
        result.asset_identity.benchmark = candidate_benchmark.upper()
        if result.asset_identity.asset_type == "UNKNOWN":
            result.hard_blocks.append("无法确定标的类型")
        _add_stage(result, store, "identifying", "SUCCESS", f"识别为 {result.asset_identity.asset_type}")

        _add_stage(result, store, "fetching_data", ResearchStatus.FETCHING_DATA.value)
        price = preflight_price or _apply_as_of(provider.get_price_history(result.asset_identity), as_of_date)
        metadata = provider.get_metadata(result.asset_identity)
        benchmark_data = _apply_as_of(provider.get_benchmark_history(result.asset_identity.benchmark or "000300.SH"), as_of_date)
        result.source_status = provider.attempts.copy()
        if not price.ok:
            result.hard_blocks.append("没有取得可用历史行情")
            raise RuntimeError(price.message or "历史行情为空")
        result.data_as_of = price.as_of
        if metadata.ok and not result.asset_identity.metadata:
            result.asset_identity.metadata = _json_safe(metadata.data.iloc[0].to_dict())
            result.asset_identity.name = result.asset_identity.metadata.get("name") or result.asset_identity.name
        asset_profile = store.save_asset_profile(AssetProfile(
            code=result.asset_identity.code,
            name=result.asset_identity.name,
            asset_type=result.asset_identity.asset_type,
            exchange=result.asset_identity.exchange,
            benchmark=result.asset_identity.benchmark,
            fixed_metadata=_json_safe(result.asset_identity.metadata),
            source=metadata.source if metadata.ok else (price.source or "unknown"),
            as_of=metadata.as_of if metadata.ok else price.as_of,
        ))
        snapshot_records = []
        if price.ok:
            if price.raw is not None:
                snapshot_records.append(store.save_data_snapshot(
                    code=result.asset_identity.code,
                    data_type="raw_market",
                    source=price.source or "unknown",
                    as_of=price.as_of,
                    payload=price.raw,
                    request=price.request,
                    freshness_days=1,
                    official=price.official,
                    fields=price.fields_returned,
                ))
            snapshot_records.append(store.save_data_snapshot(
                code=result.asset_identity.code,
                data_type="market",
                source=price.source or "unknown",
                as_of=price.as_of,
                payload=price.data,
                request=price.request,
                freshness_days=1,
                official=price.official,
                fields=price.fields_returned,
            ))
        if metadata.ok:
            snapshot_records.append(store.save_data_snapshot(
                code=result.asset_identity.code,
                data_type="metadata",
                source=metadata.source or "unknown",
                as_of=metadata.as_of,
                payload=metadata.data,
                request=metadata.request,
                freshness_days=7,
                official=metadata.official,
                fields=metadata.fields_returned,
            ))
        if benchmark_data.ok:
            snapshot_records.append(store.save_data_snapshot(
                code=result.asset_identity.benchmark or "000300.SH",
                data_type="benchmark",
                source=benchmark_data.source or "unknown",
                as_of=benchmark_data.as_of,
                payload=benchmark_data.data,
                request=benchmark_data.request,
                freshness_days=1,
                official=benchmark_data.official,
                fields=benchmark_data.fields_returned,
            ))
        result.cache_status["snapshots"] = [
            {"snapshot_id": item["snapshot_id"], "data_type": item["data_type"], "source": item["source"], "as_of": item["as_of"], "content_hash": item.get("content_hash")}
            for item in snapshot_records
        ]
        store.save_payload("research_source_snapshot", run_id, {"snapshots": result.cache_status["snapshots"]})
        _add_stage(result, store, "fetching_data", "SUCCESS", price.message)

        _add_stage(result, store, "data_quality_check", ResearchStatus.DATA_QUALITY_CHECK.value)
        result.quantitative_metrics = analyze_price_history(price.data)
        quality = result.quantitative_metrics["quality"]
        if quality["sample_days"] < 60:
            result.hard_blocks.append("历史样本少于 60 个交易日")
        if quality["duplicate_dates"] or quality["large_daily_moves"]:
            result.risks.append({
                "type": "data_quality",
                "message": "存在重复日期或较大单日价格变动，需人工核验复权和事件影响",
            })
        _add_stage(result, store, "data_quality_check", "SUCCESS", "数据质量检查完成", quality)

        _add_stage(result, store, "quantitative_analysis", ResearchStatus.QUANTITATIVE_ANALYSIS.value)
        if benchmark_data.ok:
            result.benchmark_analysis = analyze_benchmark(price.data, benchmark_data.data)
        else:
            result.benchmark_analysis = {"available": False, "reason": benchmark_data.message}
            result.missing_items.append("基准历史行情")
        store.save_payload("research_benchmark_snapshot", run_id, result.benchmark_analysis)
        technical = analyze_technical_indicators(
            price.data,
            benchmark_data.data if benchmark_data.ok else None,
            _technical_config(config),
            risk_metrics=result.quantitative_metrics,
        )
        result.quantitative_metrics["technical_analysis"] = technical
        result.quantitative_metrics["composite_scores"] = technical.get("composite_scores", {})
        market_snapshot = next(
            (item for item in snapshot_records if item.get("data_type") == "market"),
            {},
        )
        technical_snapshot_id = store.save_technical_snapshot(
            run_id=run_id,
            code=result.asset_identity.code,
            as_of=technical.get("as_of"),
            input_snapshot_id=market_snapshot.get("snapshot_id"),
            data_hash=market_snapshot.get("content_hash"),
            config_hash=_technical_config_hash(config),
            formula_version=FORMULA_VERSION,
            payload=technical,
        )
        result.cache_status["technical_snapshot"] = {
            "technical_snapshot_id": technical_snapshot_id,
            "input_snapshot_id": market_snapshot.get("snapshot_id"),
            "data_hash": market_snapshot.get("content_hash"),
            "as_of": technical.get("as_of"),
            "config_hash": _technical_config_hash(config),
            "formula_version": FORMULA_VERSION,
            "computation": "full_history_recompute",
        }
        factor_analysis = {}
        for factor_horizon in ("short", "medium", "long"):
            factor_result = analyze_asset_factors(
                result.asset_identity.code,
                price.data,
                quantitative_metrics=result.quantitative_metrics,
                benchmark_analysis=result.benchmark_analysis,
                horizon=factor_horizon,
            )
            factor_analysis[factor_horizon] = factor_result
            store.save_asset_factor_snapshot(
                run_id=run_id,
                code=result.asset_identity.code,
                horizon=factor_horizon,
                as_of=factor_result.get("as_of"),
                formula_version=factor_result.get("formula_version", "asset-factors-v1"),
                payload=factor_result,
            )
        result.quantitative_metrics["factor_analysis"] = factor_analysis
        store.save_payload("research_metric_snapshot", run_id, result.quantitative_metrics)
        _add_stage(result, store, "quantitative_analysis", "SUCCESS", "定量分析完成")

        _add_stage(result, store, "instrument_analysis", ResearchStatus.INSTRUMENT_ANALYSIS.value)
        result.instrument_analysis = {
            "asset_type": result.asset_identity.asset_type,
            "metadata": result.asset_identity.metadata,
            "asset_profile": _json_safe(asset_profile.__dict__) if asset_profile else None,
            "analysis_scope": "ETF/LOF/QDII 产品层待补充" if result.asset_identity.asset_type != "STOCK" else "股票基础面待补充",
        }
        result.strategy_fit = _strategy_fit(result.quantitative_metrics)
        store.save_payload("research_strategy_fit", run_id, result.strategy_fit)
        _add_stage(result, store, "instrument_analysis", "SUCCESS", "完成首期通用标的分析")

        _add_stage(result, store, "online_research", ResearchStatus.ONLINE_RESEARCH.value)
        if llm_provider and network_research and evidence_research:
            try:
                model_provider = build_llm_provider(llm_provider, llm_model)
                response = model_provider.research_evidence(ResearchPrompt(
                    question="补齐标的产品结构、指数规则、近期事件和主要风险的可核验证据。",
                    context={
                        "asset_identity": _json_safe(result.asset_identity.__dict__),
                        "quantitative_metrics": _json_safe(result.quantitative_metrics),
                        "benchmark_analysis": _json_safe(result.benchmark_analysis),
                    },
                    source_requirements=["官方基金/指数/交易所/公司来源优先", "每个事实必须包含 URL 和截至日期"],
                ))
                result.evidence.extend(response.facts)
                result.missing_items.extend(response.missing_items)
                if response.conflicts:
                    result.risks.append({"type": "source_conflict", "items": response.conflicts})
                store.save_payload("research_evidence", run_id, {
                    "provider": llm_provider,
                    "model": llm_model,
                    "facts": response.facts,
                    "conflicts": response.conflicts,
                    "missing_items": response.missing_items,
                })
                _add_stage(result, store, "online_research", "SUCCESS", f"取得 {len(response.facts)} 条结构化证据")
            except Exception as exc:
                result.missing_items.append(f"模型补研失败：{type(exc).__name__}: {exc}")
                _add_stage(result, store, "online_research", "PARTIAL", "模型不可用，已保留定量结果")
        elif not evidence_research and not (llm_provider and not network_research):
            _add_stage(result, store, "online_research", "SUCCESS", "联网定性证据已关闭")
        elif llm_provider and not network_research:
            result.missing_items.append("离线模式已跳过模型补研")
            _add_stage(result, store, "online_research", "PARTIAL", "离线模式不调用模型服务")
        else:
            result.missing_items.append("联网定性资料和来源证据")
            _add_stage(result, store, "online_research", "PARTIAL", "未指定模型，未生成联网证据")

        _add_stage(result, store, "reporting", ResearchStatus.REPORTING.value)
        result.run_status = ResearchStatus.PARTIAL.value if result.missing_items else ResearchStatus.COMPLETED.value
        result.report = render_markdown(_json_safe(result.to_dict()))
        chart_paths = render_charts(result.quantitative_metrics, run_dir / "charts")
        report_path = run_dir / "report.md"
        json_path = run_dir / "result.json"
        report_path.write_text(result.report, encoding="utf-8")
        result.artifacts = {"report": str(report_path), "result": str(json_path), **chart_paths}
        payload = _json_safe(result.to_dict())
        store.write_json(json_path, payload)
        store.save_payload("research_report", run_id, {"report_path": str(report_path), "artifact_paths": result.artifacts})
        _add_stage(result, store, "reporting", "SUCCESS", "报告和图表已生成")
        result.report = report_path.read_text(encoding="utf-8")
        payload = _json_safe(result.to_dict())
        result.is_frozen = True
        payload = _json_safe(result.to_dict())
        store.write_json(json_path, payload)
        store.save_result(payload, cache_key=cache_key, result_path=json_path)
        store.set_run_frozen(run_id, True)
        if project_id:
            store.record_revision(project_id, "CREATE_RESEARCH_VERSION", {
                "run_id": run_id,
                "parent_run_id": result.parent_run_id,
                "version_no": result.version_no,
                "status": result.run_status,
            })
        return result
    except Exception as exc:
        result.run_status = ResearchStatus.FAILED.value if not result.quantitative_metrics else ResearchStatus.PARTIAL.value
        result.error = f"{type(exc).__name__}: {exc}"
        result.report = render_markdown(_json_safe(result.to_dict())) if result.asset_identity else "研究失败：标的无法识别"
        json_path = run_dir / "result.json"
        payload = _json_safe(result.to_dict())
        store.write_json(json_path, payload)
        store.save_result(payload, cache_key=cache_key, result_path=json_path)
        return result


def _strategy_fit(metrics: dict[str, Any]) -> dict[str, Any]:
    one_year = metrics.get("windows", {}).get("1y", {})
    trend = metrics.get("rolling", {}).get("return_120d")
    result = {}
    for name in ("long_term_core", "medium_term_rotation", "trend_following", "mean_reversion"):
        result[name] = {"rating": "资料不足", "reason": "首期仅使用通用量化指标，未接入组合和宏观暴露"}
    if one_year.get("sample_days", 0) >= 252:
        result["long_term_core"] = {
            "rating": "条件适合" if (one_year.get("max_drawdown") or 0) > -0.35 else "谨慎",
            "reason": "基于近一年收益、波动和最大回撤；不构成交易建议",
        }
    if trend is not None:
        result["trend_following"] = {
            "rating": "适合" if trend > 0 else "条件适合",
            "reason": "基于最近120个交易日动量",
        }
    return result


def _result_from_dict(payload: dict[str, Any]) -> ResearchRunResult:
    from qteasy_research.pretrade.schemas import AssetIdentity, StageRecord
    data = dict(payload)
    data["asset_identity"] = AssetIdentity(**data["asset_identity"])
    data["stages"] = [StageRecord(**stage) for stage in data.get("stages", [])]
    return ResearchRunResult(**data)


def continue_research(run_id: str, question: str, *, llm_provider: str, llm_model: str | None = None) -> ResearchRunResult:
    """基于已有版本追加证据，不覆盖原有定量结论。"""
    root = _default_root() / "runs" / run_id / "result.json"
    if not root.exists():
        raise FileNotFoundError(f"研究任务不存在：{run_id}")
    result = _result_from_dict(ResearchStore.read_json(root))
    provider = build_llm_provider(llm_provider, llm_model)
    response = provider.research_evidence(ResearchPrompt(
        question=question,
        context={
            "run_id": result.run_id,
            "asset_identity": result.asset_identity.__dict__,
            "quantitative_metrics": result.quantitative_metrics,
            "existing_evidence": result.evidence,
        },
        source_requirements=["只追加新证据", "每个事实必须包含 URL、截至日期和置信度"],
    ))
    result.evidence.extend(response.facts)
    result.missing_items.extend(response.missing_items)
    if response.conflicts:
        result.risks.append({"type": "source_conflict", "items": response.conflicts})
    result.run_status = ResearchStatus.PARTIAL.value
    result.report = render_markdown(_json_safe(result.to_dict()))
    root.write_text(json.dumps(_json_safe(result.to_dict()), ensure_ascii=False, indent=2), encoding="utf-8")
    root.with_name("followup.md").write_text(
        f"# 继续研究\n\n问题：{question}\n\n新增证据：{len(response.facts)} 条\n\n"
        + "\n".join(f"- {item['claim']}（{item['source_url']}）" for item in response.facts),
        encoding="utf-8",
    )
    return result


def continue_research(run_id: str, question: str, *, llm_provider: str, llm_model: str | None = None) -> ResearchRunResult:
    """基于既有版本继续研究，始终生成新版本，不修改旧 JSON。"""
    store = ResearchStore(_default_root())
    with store._connect() as connection:
        row = connection.execute("SELECT * FROM research_run WHERE run_id=?", (run_id,)).fetchone()
    if not row or not row["result_path"]:
        raise FileNotFoundError(f"研究任务不存在：{run_id}")
    old_result = _result_from_dict(store.read_json(Path(row["result_path"])))
    if old_result.project_id:
        project = store.get_project(old_result.project_id)
        if project.status in {ResearchProjectStatus.CLOSED.value, ResearchProjectStatus.ARCHIVED.value}:
            raise ValueError("已关闭或已归档项目不可直接追问，请先重新打开项目")

    import copy
    result = copy.deepcopy(old_result)
    result.run_id = uuid.uuid4().hex
    result.parent_run_id = old_result.run_id
    result.version_no = old_result.version_no + 1
    result.is_frozen = False
    result.cache_status = {"policy": "followup", "run_reused": False}
    result.stages = list(old_result.stages)
    store.create_run(
        run_id=result.run_id,
        case_id=result.research_case_id,
        code=result.asset_identity.code,
        cache_key=f"followup:{old_result.run_id}:{result.run_id}",
        project_id=result.project_id,
        parent_run_id=result.parent_run_id,
        version_no=result.version_no,
    )
    root = _default_root()
    relative_run_root = Path("projects") / result.project_id / "versions" if result.project_id else Path("runs")
    run_dir = root / relative_run_root / result.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    provider = build_llm_provider(llm_provider, llm_model)
    response = provider.research_evidence(ResearchPrompt(
        question=question,
        context={
            "run_id": old_result.run_id,
            "asset_identity": old_result.asset_identity.__dict__,
            "quantitative_metrics": old_result.quantitative_metrics,
            "existing_evidence": old_result.evidence,
        },
        source_requirements=["只追加新证据", "每个事实必须包含 URL、截至日期和置信度"],
    ))
    result.evidence.extend(response.facts)
    result.missing_items.extend(response.missing_items)
    if response.conflicts:
        result.risks.append({"type": "source_conflict", "items": response.conflicts})
    result.run_status = ResearchStatus.PARTIAL.value
    result.report = render_markdown(_json_safe(result.to_dict()))
    report_path = run_dir / "report.md"
    json_path = run_dir / "result.json"
    followup_path = run_dir / "followup.md"
    report_path.write_text(result.report, encoding="utf-8")
    followup_path.write_text(
        f"# 继续研究\n\n问题：{question}\n\n新增证据：{len(response.facts)} 条\n"
        + "\n".join(f"- {item['claim']}（{item['source_url']}）" for item in response.facts),
        encoding="utf-8",
    )
    result.artifacts = {"report": str(report_path), "result": str(json_path), "followup": str(followup_path)}
    result.is_frozen = True
    payload = _json_safe(result.to_dict())
    store.write_json(json_path, payload)
    store.save_payload("research_evidence", result.run_id, {
        "provider": llm_provider, "model": llm_model, "question": question,
        "facts": response.facts, "conflicts": response.conflicts, "missing_items": response.missing_items,
    })
    store.save_payload("research_followup", result.run_id, {"parent_run_id": old_result.run_id, "question": question})
    store.save_result(payload, cache_key=f"followup:{old_result.run_id}:{result.run_id}", result_path=json_path)
    store.set_run_frozen(result.run_id, True)
    if result.project_id:
        store.record_revision(result.project_id, "CREATE_FOLLOWUP_VERSION", {
            "run_id": result.run_id, "parent_run_id": old_result.run_id, "question": question,
        })
    return result
