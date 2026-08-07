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
from qteasy_research.reference.config import (
    BENCHMARKS,
    CONE_PERCENTILES,
    SYSTEM_B_DATA_ROOT,
    VOLATILITY_WINDOWS,
)
from qteasy_research.reference.grid_reference import build_grid_reference
from qteasy_research.reference.hedge_efficiency import build_hedge_efficiency
from qteasy_research.reference.macro_scenarios import build_monthly_scenario_table
from qteasy_research.reference.metadata import build_header, embed_header_any, today_iso
from qteasy_research.reference.rolling_beta import multi_benchmark_beta
from qteasy_research.reference.schema import AssetDimensions, DecisionRefPackage
from qteasy_research.reference.shared_dir import IntegrationDir
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
        if include_grid:
            payloads["grid_reference_table.csv"] = build_grid_reference(
                assets, aligned, bench_frames
            )
        if include_hedge:
            payloads["macro_hedge_efficiency.parquet"] = build_hedge_efficiency(
                data_root_path, assets, aligned, macro_table
            )

        if dry_run:
            header = build_header(generated_date=generated_date, data_asof=data_asof)
            written: list[str] = []
            for filename, value in payloads.items():
                embed_header_any(sink.root / filename, header, value)
                written.append(filename)
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
        }
    except Exception:
        sink.write_heartbeat(status="error")
        raise
