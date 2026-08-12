"""组合分析 CLI（L3）：组合风险收益 + 比例建议 + L4 组合暴露。

实现《B侧研究系统能力需求规格》§8 L3 切入点：因子风险模型
``Σ = B Σf B' + diag(σ²ε)`` 一步统一 L3/L4。

- **输入**：A 侧只读契约（``--strategy``，从 ``strategy_contract.json`` 读取）
  或直接权重（``--weights asset:w,asset:w``）。
- **数据**：B 本地行情（``load_price_frames``）+ 因子面板
  （``research_store/factor_values/*.parquet``）+ 可选宏观月差
  （``data/processed/global_macro/DGS30.csv / DFII10.csv``，``--include-macro``）。
- **输出**：默认只写 B 本地 ``reports/portfolio_analysis/{run_id}/``；
  ``--publish`` 时经 ``IntegrationDir.publish_run`` 写共享目录
  ``systemB_ref/{run_id}/portfolio_analysis/``（明确 opt-in，契约铁律）。
- **安全边界**：不读 ``systemA_feedback/``；机器产出全 ASCII；
  一切展示为 ``approval_policy=REFERENCE_ONLY``，无决策阈值。

示例：
  python scripts/run_portfolio_analysis.py --strategy three_musketeers
  python scripts/run_portfolio_analysis.py --weights 512890.SH:0.43,513650.SH:0.19,518880.SH:0.38
  python scripts/run_portfolio_analysis.py --strategy three_musketeers --include-macro
  python scripts/run_portfolio_analysis.py --strategy three_musketeers --publish
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本可从任意目录运行（scripts/ 的父目录即 qteasy_lab 包根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from qteasy_research.reference.backtest_engine import (
    load_price_frames,
    parse_contract,
)
from qteasy_research.reference.config import STRATEGY_CONTRACT_PATH, SYSTEM_B_DATA_ROOT
from qteasy_research.reference.factor_tear import (
    FACTOR_IDS,
    load_factor_panel_from_parquet,
)
from qteasy_research.reference.portfolio_analysis import (
    PORTFOLIO_ANALYSIS_DIR,
    PORTFOLIO_ANALYSIS_SCHEMA,
    align_close_panel,
    analyze_portfolio,
    default_run_id,
    strategy_analysis_inputs,
    write_outputs,
)
from qteasy_research.reference.shared_dir import IntegrationDir

# 默认因子面板目录（阶段二生产管道：research_store/factor_values/*.parquet）。
DEFAULT_FACTOR_DIR = Path(__file__).resolve().parents[1] / "research_store" / "factor_values"
# --include-macro 时读取的宏观序列（月差）。
DEFAULT_MACRO_SERIES = ("DGS30", "DFII10")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="组合分析（L3）：只读契约 + B 本地数据，只写 reports/portfolio_analysis/"
    )
    parser.add_argument("--contract", type=Path, default=STRATEGY_CONTRACT_PATH,
                        help="A 侧策略规则契约（只读）")
    parser.add_argument("--strategy", type=str, default=None,
                        help="契约内策略 strategy_id（barbell/mid_line 用 target_weight；"
                             "grid 用 max_weight*0.5）")
    parser.add_argument("--weights", type=str, default=None,
                        help="直接权重 asset:w,asset:w（可未归一化；优先于 --strategy）")
    parser.add_argument("--data-root", type=Path, default=SYSTEM_B_DATA_ROOT,
                        help="B 本地数据目录（fund_daily/processed/global_macro）")
    parser.add_argument("--factor-dir", type=Path, default=DEFAULT_FACTOR_DIR,
                        help="因子面板目录（factor_values/*.parquet）")
    parser.add_argument("--output-root", type=Path, default=PORTFOLIO_ANALYSIS_DIR,
                        help="输出根目录（只写 B 本地 reports/portfolio_analysis）")
    parser.add_argument("--window", type=int, default=252,
                        help="X 暴露估计窗口（默认 252，下限 120）")
    parser.add_argument("--include-macro", action="store_true",
                        help="启用宏观因子月差（ΔDGS30/ΔDFII10）进入 Σf")
    parser.add_argument("--run-id", type=str, default=None,
                        help="输出 run_id（默认 YYYYMMDD_HHMMSS）")
    parser.add_argument("--publish", action="store_true",
                        help="发布到共享目录 systemB_ref/{run_id}/portfolio_analysis/（明确 opt-in）")
    return parser.parse_args()


def _parse_weights(text: str) -> dict[str, float]:
    """解析 ``asset:w,asset:w``（权重可未归一化，analyze_portfolio 会归一化）。"""
    weights: dict[str, float] = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"权重格式错误：{part!r}（应为 asset:w）")
        asset, value = part.rsplit(":", 1)
        try:
            number = float(value)
        except ValueError as exc:
            raise ValueError(f"权重值错误：{value!r}") from exc
        if number < 0:
            raise ValueError(f"权重不可为负：{asset}={number}")
        weights[asset.strip()] = number
    if not weights:
        raise ValueError("--weights 未解析出任何权重")
    return weights


def _load_macro_frames(data_root: Path, series_ids: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    """读全局宏观标准化 CSV（DGS30/DFII10），缺失序列自动跳过（不虚构）。"""
    macro_dir = data_root / "processed" / "global_macro"
    frames: dict[str, pd.DataFrame] = {}
    for series_id in series_ids:
        path = macro_dir / f"{series_id}.csv"
        if not path.exists():
            continue
        try:
            frames[series_id] = pd.read_csv(path)
        except Exception:
            continue
    return frames


def main(args: argparse.Namespace) -> dict:
    if not args.strategy and not args.weights:
        raise SystemExit("必须提供 --strategy 或 --weights 之一")

    # ---- 权重与 bounds ----
    bounds: dict[str, tuple[float, float]] | None = None
    if args.weights:
        weights = _parse_weights(args.weights)
        source = "direct"
    else:
        contract = parse_contract(args.contract)  # 契约缺失 → FileNotFoundError
        strategy = next(
            (s for s in contract.strategies if s.strategy_id == args.strategy), None
        )
        if strategy is None:
            raise SystemExit(f"契约中无策略 {args.strategy!r}")
        inputs = strategy_analysis_inputs(strategy)
        if inputs["error"]:
            raise SystemExit(f"策略 {args.strategy!r} 无可用权重：{inputs['error']}")
        weights = inputs["weights"]
        bounds = inputs["bounds"]
        source = inputs["source"]

    # ---- 数据 ----
    # 因子面板先读：截面多空收益 / X 暴露矩阵需要"全资产×全因子"，
    # 所以行情加载的范围 = 组合资产 ∪ 因子面板全部资产。
    factor_panels = load_factor_panel_from_parquet(args.factor_dir, FACTOR_IDS)
    universe_assets = set(weights.keys())
    for panel in factor_panels.values():
        universe_assets.update(panel.columns)
    universe_assets = sorted(universe_assets)
    frames = load_price_frames(universe_assets, data_dir=args.data_root, online_ok=False)
    close_panel = align_close_panel(frames)
    macro_frames = _load_macro_frames(args.data_root, DEFAULT_MACRO_SERIES) if args.include_macro else {}

    run_id = args.run_id or default_run_id()
    result = analyze_portfolio(
        weights,
        close_panel,
        factor_panels,
        macro_frames=macro_frames,
        include_macro=args.include_macro,
        window=args.window,
        bounds=bounds,
        run_id=run_id,
    )

    # ---- 输出 ----
    out_dir = args.output_root / run_id
    written = write_outputs(result, out_dir)

    published: dict | None = None
    if args.publish:
        integration = IntegrationDir()
        published = integration.publish_run(
            run_id,
            out_dir,
            subdir="portfolio_analysis",
            data_asof=result["generated_at"],
            generated_date=result["generated_at"],
            schema_version=PORTFOLIO_ANALYSIS_SCHEMA,
            cadence=None,
            package_kind="portfolio_analysis",
            warnings=result["warnings"] or None,
        )

    # ---- ASCII 控制台摘要 ----
    print(f"[portfolio_analysis] run_id={run_id} schema={result['schema']} "
          f"approval_policy={result['approval_policy']}")
    print(f"[portfolio_analysis] source={source} assets={','.join(result['assets'])}")
    if result["dropped_assets"]:
        print(f"[portfolio_analysis] dropped_assets={','.join(result['dropped_assets'])}")
    if result["dropped_factors"]:
        print(f"[portfolio_analysis] dropped_factors={','.join(result['dropped_factors'])}")
    portfolio = result["portfolio"]
    print(f"[portfolio_analysis] annual_return={portfolio['annual_return']:.4f}")
    print(f"[portfolio_analysis] annual_vol_factor_model={portfolio['annual_vol_factor_model']}")
    print(f"[portfolio_analysis] annual_vol_sample={portfolio['annual_vol_sample']}")
    print(f"[portfolio_analysis] annual_vol_ewma={portfolio['annual_vol_ewma']}")
    print(f"[portfolio_analysis] max_drawdown={portfolio['max_drawdown']}")
    print(f"[portfolio_analysis] sharpe={portfolio['sharpe']}")
    for item in portfolio["vol_consistency_warnings"]:
        print(f"[portfolio_analysis] VOL_WARNING {item}")
    rp = result["proportions"]["risk_parity"]
    print(f"[portfolio_analysis] risk_parity status={rp.get('status')} method={rp.get('method')} "
          f"weights={','.join(f'{a}={v:.4f}' for a, v in rp.get('weights', {}).items())}")
    frontier = result["proportions"]["efficient_frontier"]
    print(f"[portfolio_analysis] efficient_frontier status={frontier.get('status')} "
          f"points={len(frontier.get('points', []))}")
    for warning in result["warnings"]:
        print(f"[portfolio_analysis] WARNING {warning}")
    print(f"[portfolio_analysis] outputs: {out_dir.resolve()}")
    if published:
        print(f"[portfolio_analysis] published: systemB_ref/{run_id}/portfolio_analysis/ "
              f"status={published['status']}")
    return result


if __name__ == "__main__":
    main(parse_args())
