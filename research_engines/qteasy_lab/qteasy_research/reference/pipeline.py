"""参考维度端到端管线：资产池对齐 → 维度计算 → 组装决策包 → 写共享目录。

产出决策包 ``decision_ref_package.json`` + ``assets_metadata.csv``，阶段一并入
网格参考表 ``grid_reference_table.csv``（B1-1）与宏观对冲效率
``macro_hedge_efficiency.parquet``（B1-2）。``output_root`` 非 None 时走 dry-run，
三件套只写到本地目录（共享目录未创建时的本地验证路径）。
任何路径都写心跳（失败也证明 B 在线）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qteasy_research.pretrade.metrics import analyze_price_history
from qteasy_research.reference.asset_pool import (
    align_pool_price_history,
    read_active_assets,
    report_pool_gaps,
)
from qteasy_research.reference.backtest_engine import parse_contract
from qteasy_research.reference.config import (
    BENCHMARKS,
    CONE_PERCENTILES,
    GRID_RECOMMENDATION_CADENCE,
    GRID_RECOMMENDATION_DIR,
    GRID_RECOMMENDATION_SCHEMA_VERSION,
    GRID_RECOMMENDATION_SUBDIR,
    GRID_SUGGESTION_DIR,
    GRID_SUGGESTION_SCHEMA_VERSION,
    GRID_SUGGESTION_SUBDIR,
    SYSTEM_B_DATA_ROOT,
    VOLATILITY_WINDOWS,
)
from qteasy_research.reference.duration_phase import build_duration_phase
from qteasy_research.reference.grid_recommendation import build_grid_recommendation
from qteasy_research.reference.grid_reference import build_grid_reference
from qteasy_research.reference.grid_suggestion import (
    build_grid_suggestion,
    build_grid_suggestion_table,
)
from qteasy_research.reference.hedge_efficiency import build_hedge_efficiency
from qteasy_research.reference.macro_scenarios import build_monthly_scenario_table
from qteasy_research.reference.red_flag import assess_red_flags, load_risk_thresholds
from qteasy_research.reference.metadata import (
    build_header,
    embed_header_any,
    embed_header_csv,
    today_iso,
)
from qteasy_research.reference.rolling_beta import multi_benchmark_beta
from qteasy_research.reference.schema import AssetDimensions, DecisionRefPackage
from qteasy_research.reference.shared_dir import IntegrationDir
from qteasy_research.reference.stress_simulator import build_stress_simulator
from qteasy_research.reference.volatility_cone import (
    build_volatility_cone,
    current_vol_rank,
)

_WINDOWS = VOLATILITY_WINDOWS


def _asset_returns(frame: pd.DataFrame) -> pd.Series:
    return frame["close"].pct_change().replace([np.inf, -np.inf], np.nan).dropna()


def _cone_pivot(returns: pd.Series) -> dict[str, dict[str, float]]:
    cone = build_volatility_cone(returns, _WINDOWS, CONE_PERCENTILES)
    if cone.empty:
        return {}
    pivot: dict[str, dict[str, float]] = {}
    for window in _WINDOWS:
        subset = cone.loc[cone["window_days"] == window]
        if subset.empty:
            continue
        pivot[f"{window}d"] = {
            f"p{pctile}": float(value)
            for pctile, value in zip(subset["percentile"], subset["value"])
        }
    return pivot


def _load_benchmark_frames(data_root: Path) -> dict[str, pd.DataFrame]:
    """加载可用基准行情为 ``{name: DataFrame(trade_date, close)}``。

    000300.SH 取自 ``index_daily.csv``；SPY/TLT/GLD 取自 ``processed/global_macro``。
    加载失败的基准跳过（beta 维度对应 name 缺失）。
    """
    frames: dict[str, pd.DataFrame] = {}
    # 指数基准
    index_path = data_root / "index_daily.csv"
    if index_path.exists():
        index_frame = pd.read_csv(index_path)
        for name, code in BENCHMARKS.items():
            if code.endswith(".SH") and "ts_code" in index_frame.columns:
                subset = index_frame.loc[
                    index_frame["ts_code"].astype(str).str.upper() == code.upper()
                ].copy()
                if not subset.empty:
                    frames[name] = _to_trade_date_close(subset)
    # 全球 ETF 基准（global_macro 标准化数据）
    for name in ("SPY", "TLT", "GLD"):
        path = data_root / "processed" / "global_macro" / f"{name}.csv"
        if path.exists():
            frame = pd.read_csv(path)
            if "observation_date" in frame.columns and "value" in frame.columns:
                cleaned = frame.rename(columns={"observation_date": "trade_date", "value": "close"})
                frames[name] = _to_trade_date_close(cleaned)
    return frames


def _to_trade_date_close(frame: pd.DataFrame) -> pd.DataFrame:
    cleaned = frame.copy()
    cleaned["trade_date"] = pd.to_datetime(cleaned["trade_date"], errors="coerce")
    cleaned["close"] = pd.to_numeric(cleaned["close"], errors="coerce")
    cleaned = cleaned.dropna(subset=["trade_date", "close"])
    cleaned = cleaned.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    cleaned = cleaned[cleaned["close"] > 0]
    return cleaned[["trade_date", "close"]]


def _compute_dimensions(
    asset_id: str,
    name: str | None,
    frame: pd.DataFrame,
    bench_frames: dict[str, pd.DataFrame],
) -> AssetDimensions:
    """从对齐行情计算单资产维度（基础层版：波动/收益/Beta，不含宏观压力）。"""
    quality = {"quality_level": "A", "source": "local"}
    if frame.empty:
        return AssetDimensions(
            asset_id=asset_id,
            name=name,
            data_quality={"quality_level": "D", "warning": "行情缺失，未虚构数据"},
        )
    metrics = analyze_price_history(frame)
    returns = _asset_returns(frame)
    volatility = {
        f"{window}d": metrics["rolling"][f"volatility_{window}d"]
        for window in _WINDOWS
    }
    vol_percentile = {
        f"{window}d": current_vol_rank(returns, window)
        for window in _WINDOWS
    }
    # 多基准滚动 Beta（每基准 latest_beta；基准缺失的键不输出）。
    beta: dict[str, dict[str, float]] = {}
    for bench_name, bench_frame in bench_frames.items():
        try:
            summary = multi_benchmark_beta(frame, {bench_name: bench_frame})
            if summary[bench_name].get("available"):
                beta[bench_name] = {
                    f"{window}d": value
                    for window, value in summary[bench_name].get("latest_beta", {}).items()
                    if value is not None
                }
        except Exception:
            continue
    if "source" in frame.columns and frame["source"].iloc[-1] == "online":
        quality["quality_level"] = "B"
        quality["source"] = "online"
    return AssetDimensions(
        asset_id=asset_id,
        name=name,
        volatility=volatility,
        volatility_percentile=vol_percentile,
        cone=_cone_pivot(returns),
        returns={f"{window}d": metrics["rolling"][f"return_{window}d"] for window in _WINDOWS},
        var95=metrics["var95"],
        es95=metrics["es95"],
        max_drawdown=metrics["max_drawdown"],
        beta=beta,
        data_quality=quality,
    )


def run_pipeline(
    *,
    target_date: str | None = None,
    data_root: str | Path | None = None,
    integration: IntegrationDir | None = None,
    online_ok: bool = True,
    asset_pool: str | Path | None = None,
    include_grid: bool = True,
    include_hedge: bool = True,
    include_duration_phase: bool = True,
    include_red_flag: bool = True,
    include_stress: bool = False,
    include_grid_suggestion: bool = True,
    include_grid_recommendation: bool = True,
    risk_params: str | Path | None = None,
    strategy_contract_path: str | Path | None = None,
    grid_suggestion_dir: str | Path | None = None,
    grid_recommendation_dir: str | Path | None = None,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    """执行参考维度管线，写共享目录（或 dry-run 到本地 output_root）。

    参数：
        target_date: 数据截止日（data_asof），默认今天；run_id 取其 YYYYMMDD。
        data_root: B 本地数据目录（默认 SYSTEM_B_DATA_ROOT）。
        integration: 共享目录操作对象（默认真实系统A共享目录）。
        online_ok: 行情缺失时是否允许在线补齐。
        asset_pool: 系统A asset_pool.csv 路径（默认 config 常量）。
        include_grid: 是否计算并输出网格参考表（B1-1）。
        include_hedge: 是否计算并输出宏观对冲效率表（B1-2）。
        include_duration_phase: 是否并入宏观持续期（phase/state_durations 到
            ``macro_regime``）；macro_table 为空时不并入任何键。
        include_red_flag: 是否逐资产评估红/橙/黄风控旗（读取系统A
            ``risk_thresholds`` 阈值；阈值缺失降级为无风险 + warning）。
        include_stress: 是否计算宏观压力情景损益（``build_stress_simulator``）
            并填充各资产 ``macro_stress``。默认 False（A 侧尚未消费该字段，
            零开销）；计算失败降级为 warning，不崩溃。
        include_grid_suggestion: 是否计算并输出网格建议包（P1-B：
            ``grid_suggestion`` 独立子目录包，``approval_policy="REFERENCE_ONLY"``）。
            默认 True；契约缺失时跳过 + warning，不崩溃。
        risk_params: 系统A ``strategy_params.json`` 路径（默认 ``config``
            常量）；测试注入临时 fixture 用，避免读到真实 A 配置。
        strategy_contract_path: A 侧策略规则契约 ``strategy_contract.json``
            路径（默认 ``config.STRATEGY_CONTRACT_PATH``）；测试注入临时 fixture
            用，避免读到真实 A 契约。
        grid_suggestion_dir: real 模式网格建议包本地源目录（默认
            ``config.GRID_SUGGESTION_DIR``）；测试注入临时目录用，避免写入
            真实 reports/ 目录。
        output_root: 非 None 时走 dry-run：三件套写到该目录（不写共享目录、
            不 write_run/backup/verify），供共享目录未创建时本地验证。

    返回：
        dry-run：``{"run_id", "status": "DRY_RUN", "warnings", "output_root",
        "files", "manifest"}``；真实写入：``{"run_id", "status", "warnings",
        "package", "verify", "manifest", "consumed"}``。
    """
    integration = integration or IntegrationDir()
    data_root_path = Path(data_root) if data_root else SYSTEM_B_DATA_ROOT
    generated_date = today_iso()
    data_asof = target_date or generated_date
    run_id = data_asof.replace("-", "")
    warnings: list[str] = []

    dry_run = output_root is not None
    # dry-run 用隔离的 IntegrationDir(output_root) 写心跳，证明链路在线但不碰真实共享目录。
    sink = IntegrationDir(output_root) if dry_run else integration

    sink.write_heartbeat(status="running")
    try:
        assets = read_active_assets(asset_pool)
        aligned = align_pool_price_history(
            assets, data_dir=data_root_path, online_ok=online_ok
        )
        gaps = report_pool_gaps(aligned)
        for gap in gaps:
            if gap.get("warning"):
                warnings.append(str(gap["warning"]))
        bench_frames = _load_benchmark_frames(data_root_path)

        # 宏观状态（最新月 + 全场景表，macro_regime 与 B1-2 共用一次计算）。
        macro_table: pd.DataFrame = pd.DataFrame()
        macro_regime: dict[str, Any] = {}
        try:
            macro_table = build_monthly_scenario_table(data_root_path)
            if not macro_table.empty:
                last = macro_table.iloc[-1]
                macro_regime = {
                    "states": list(last.get("states") or []),
                    "rate_proxy": last.get("rate_proxy"),
                    "month": last.get("month"),
                    "macro_unavailable": bool(last.get("macro_unavailable", False)),
                }
                # 阶段二：宏观持续期并入（空表不并入任何键，保住 macro_regime=={} 回归）。
                if include_duration_phase:
                    duration = build_duration_phase(macro_table)
                    macro_regime["phase"] = duration["phase"]
                    macro_regime["phase_confidence"] = duration["phase_confidence"]
                    macro_regime["phase_basis"] = duration["phase_basis"]
                    macro_regime["state_durations"] = duration["durations"]
                    if duration["reason"] is not None:
                        macro_regime["phase_reason"] = duration["reason"]
        except Exception as exc:
            warnings.append(f"宏观场景识别失败：{type(exc).__name__}: {exc}")

        dimensions: list[AssetDimensions] = []
        for _, row in assets.iterrows():
            asset_id = str(row["asset_id"]).strip()
            dimensions.append(
                _compute_dimensions(
                    asset_id, row.get("name"), aligned.get(asset_id, pd.DataFrame()), bench_frames
                )
            )

        # 阶段二：逐资产红/橙/黄风控旗（循环后填充；阈值缺失降级为全 None + warning）。
        if include_red_flag:
            thresholds, threshold_warnings = load_risk_thresholds(risk_params)
            warnings.extend(threshold_warnings)
            flags = assess_red_flags(assets, aligned, thresholds)
            for dim in dimensions:
                dim.red_flag = flags.get(dim.asset_id)

        # 阶段三：宏观压力情景损益（include_stress=True 时填充 macro_stress）。
        # 默认 False 零开销；宏观表为空 / 宏观序列缺失时各情景 sample_count=0
        # 不虚构；计算失败降级为 warning，不崩溃。
        if include_stress:
            try:
                stress_map = build_stress_simulator(assets, aligned, macro_table, data_root_path)
            except Exception as exc:
                warnings.append(f"压力模拟失败：{type(exc).__name__}: {exc}")
                stress_map = {}
            for dim in dimensions:
                dim.macro_stress = stress_map.get(dim.asset_id, {})

        package = DecisionRefPackage(
            generated_date=generated_date,
            data_asof=data_asof,
            assets=dimensions,
            macro_regime=macro_regime,
            warnings=warnings,
        )
        metadata_frame = pd.DataFrame([
            {"asset_id": gap["asset_id"], "quality_level": gap["quality_level"],
             "available": gap["available"], "warning": gap["warning"]}
            for gap in gaps
        ])
        payloads: dict[str, Any] = {
            "decision_ref_package.json": package.to_dict(),
            "assets_metadata.csv": metadata_frame,
        }
        # 阶段一：B1-1 网格参考表 + B1-2 宏观对冲效率并入管线产出。
        grid_ref_frame: pd.DataFrame | None = None
        if include_grid:
            grid_ref_frame = build_grid_reference(assets, aligned, bench_frames)
            payloads["grid_reference_table.csv"] = grid_ref_frame
        if include_hedge:
            payloads["macro_hedge_efficiency.parquet"] = build_hedge_efficiency(
                data_root_path, assets, aligned, macro_table
            )

        # 阶段六（P1-B）：网格建议包（独立子目录包，approval_policy=REFERENCE_ONLY）。
        # 计算口径唯一源 = contract.shared_config.grid；契约缺失 → 跳过 + warning。
        grid_suggestion_data: tuple[dict[str, Any], pd.DataFrame] | None = None
        if include_grid_suggestion:
            if grid_ref_frame is None:
                grid_ref_frame = build_grid_reference(assets, aligned, bench_frames)
            try:
                contract = parse_contract(strategy_contract_path)
                suggestion = build_grid_suggestion(
                    contract.strategies,
                    aligned,
                    grid_ref_frame,
                    contract.shared_grid or {},
                    data_asof,
                )
                grid_suggestion_data = (suggestion, build_grid_suggestion_table(suggestion))
            except FileNotFoundError:
                warnings.append(
                    "strategy_contract.json 缺失，跳过网格建议（grid_suggestion）"
                )
            except Exception as exc:
                warnings.append(f"网格建议失败：{type(exc).__name__}: {exc}")

        # 阶段（B2/B3）：网格推荐组合包（独立子目录包，approval_policy=REFERENCE_ONLY，
        # cadence="weekly"）。契约缺失/异常 → warning 降级不崩溃。复用 grid_suggestion
        # 包作为方案标的建议来源；缺失时对方案标的兜底重算。
        grid_recommendation_data: dict[str, Any] | None = None
        if include_grid_recommendation:
            if grid_ref_frame is None:
                grid_ref_frame = build_grid_reference(assets, aligned, bench_frames)
            try:
                reco_contract = parse_contract(strategy_contract_path)
                reco_suggestion = grid_suggestion_data[0] if grid_suggestion_data is not None else None
                grid_recommendation_data = build_grid_recommendation(
                    reco_contract.strategies,
                    aligned,
                    grid_ref_frame,
                    reco_suggestion,
                    reco_contract.shared_grid or {},
                    data_asof,
                )
            except FileNotFoundError:
                warnings.append(
                    "strategy_contract.json 缺失，跳过网格推荐组合（grid_recommendation）"
                )
            except Exception as exc:
                warnings.append(f"网格推荐组合失败：{type(exc).__name__}: {exc}")

        if dry_run:
            header = build_header(generated_date=generated_date, data_asof=data_asof)
            written: list[str] = []
            for filename, value in payloads.items():
                embed_header_any(sink.root / filename, header, value)
                written.append(filename)
            if grid_suggestion_data is not None:
                gs_header = build_header(
                    generated_date=generated_date,
                    data_asof=data_asof,
                    schema_version=GRID_SUGGESTION_SCHEMA_VERSION,
                )
                gs_dir = sink.root / GRID_SUGGESTION_SUBDIR
                gs_dir.mkdir(parents=True, exist_ok=True)
                embed_header_any(gs_dir / "grid_suggestion.json", gs_header, grid_suggestion_data[0])
                embed_header_csv(gs_dir / "grid_suggestion_table.csv", gs_header, grid_suggestion_data[1])
                written.append(f"{GRID_SUGGESTION_SUBDIR}/grid_suggestion.json")
                written.append(f"{GRID_SUGGESTION_SUBDIR}/grid_suggestion_table.csv")
            if grid_recommendation_data is not None:
                gr_header = build_header(
                    generated_date=generated_date,
                    data_asof=data_asof,
                    schema_version=GRID_RECOMMENDATION_SCHEMA_VERSION,
                )
                gr_dir = sink.root / GRID_RECOMMENDATION_SUBDIR
                gr_dir.mkdir(parents=True, exist_ok=True)
                embed_header_any(gr_dir / "grid_recommendations.json", gr_header, grid_recommendation_data)
                written.append(f"{GRID_RECOMMENDATION_SUBDIR}/grid_recommendations.json")
            sink.write_heartbeat(status="ok")
            return {
                "run_id": run_id,
                "status": "DRY_RUN",
                "warnings": warnings,
                "output_root": str(sink.root),
                "files": written,
                "manifest": sink.get_manifest(),
            }

        record = integration.write_run(
            run_id,
            payloads,
            data_asof=data_asof,
            generated_date=generated_date,
            warnings=warnings,
        )
        backup_path = integration.backup_run(run_id)
        # 阶段六（P1-B）：real 模式发布网格建议到共享目录独立子目录。
        grid_suggestion_published: dict[str, Any] | None = None
        if grid_suggestion_data is not None:
            gs_header = build_header(
                generated_date=generated_date,
                data_asof=data_asof,
                schema_version=GRID_SUGGESTION_SCHEMA_VERSION,
            )
            gs_out = (Path(grid_suggestion_dir) if grid_suggestion_dir else GRID_SUGGESTION_DIR) / run_id
            gs_out.mkdir(parents=True, exist_ok=True)
            embed_header_any(gs_out / "grid_suggestion.json", gs_header, grid_suggestion_data[0])
            embed_header_csv(gs_out / "grid_suggestion_table.csv", gs_header, grid_suggestion_data[1])
            grid_suggestion_published = integration.publish_run(
                run_id,
                gs_out,
                subdir=GRID_SUGGESTION_SUBDIR,
                data_asof=data_asof,
                generated_date=generated_date,
                schema_version=GRID_SUGGESTION_SCHEMA_VERSION,
                cadence=None,
                package_kind=GRID_SUGGESTION_SUBDIR,
                warnings=warnings or None,
            )
        # 阶段（B2/B3）：real 模式发布网格推荐组合到共享目录独立子目录
        # （cadence="weekly"，只更新 newest_grid_recommendation_run，绝不顶日度指针）。
        grid_recommendation_published: dict[str, Any] | None = None
        if grid_recommendation_data is not None:
            gr_header = build_header(
                generated_date=generated_date,
                data_asof=data_asof,
                schema_version=GRID_RECOMMENDATION_SCHEMA_VERSION,
            )
            gr_out = (Path(grid_recommendation_dir) if grid_recommendation_dir else GRID_RECOMMENDATION_DIR) / run_id
            gr_out.mkdir(parents=True, exist_ok=True)
            embed_header_any(gr_out / "grid_recommendations.json", gr_header, grid_recommendation_data)
            grid_recommendation_published = integration.publish_run(
                run_id,
                gr_out,
                subdir=GRID_RECOMMENDATION_SUBDIR,
                data_asof=data_asof,
                generated_date=generated_date,
                schema_version=GRID_RECOMMENDATION_SCHEMA_VERSION,
                cadence=GRID_RECOMMENDATION_CADENCE,
                package_kind=GRID_RECOMMENDATION_SUBDIR,
                warnings=warnings or None,
            )
        verify = integration.verify_run(run_id)
        integration.write_heartbeat(status="ok")
        return {
            "run_id": run_id,
            "status": "COMPLETED" if verify.get("ok") else "PARTIAL",
            "warnings": warnings,
            "backup_path": str(backup_path),
            "verify": verify,
            "manifest": integration.get_manifest(),
            "consumed": integration.list_consumed(),
            "grid_suggestion": grid_suggestion_published,
            "grid_recommendation": grid_recommendation_published,
        }
    except Exception:
        sink.write_heartbeat(status="error")
        raise
