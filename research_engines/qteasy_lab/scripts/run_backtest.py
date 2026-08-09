"""策略级别回测 CLI：基于 A 侧策略规则契约的轻量事件驱动回测（阶段四）。

读 A 侧只读契约（``strategy_contract.json``）+ B 本地行情/宏观数据 → 产出三件套到
``reports/backtest/``：``{strategy_id}_nav.csv`` + ``{strategy_id}_trades.csv`` +
``{strategy_id}_{YYYYMM}.md``（绩效摘要 Markdown）。

- 只写 B 本地 ``reports/backtest/``，不写共享目录、不写系统A、不读 ``systemA_feedback/``
  （M-003 单向数据流）。
- 防未来函数：T 收盘信号 → T+1 收盘成交；首日建仓 T0 先验；宏观 phase 点内（``< T``）。
- 机器输出全 ASCII，``strategy_id``/``asset_id`` 标识，零中文策略名。

示例：
  python scripts/run_backtest.py --strategy-id all --no-online
  python scripts/run_backtest.py --strategy-id barbell,grid_lh --no-online
  python scripts/run_backtest.py --strategy-id mid_line --no-macro-tilt --no-online
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本可从任意目录运行（scripts/ 的父目录即 qteasy_lab 包根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qteasy_research.reference.backtest_engine import (
    BACKTEST_START_DEFAULT,
    TRADE_LOT_SIZE,
    BacktestResult,
    build_phase_lookup,
    parse_contract,
    run_backtest,
    write_backtest_outputs,
)
from qteasy_research.reference.config import (
    BACKTEST_REPORT_DIR,
    STRATEGY_CONTRACT_PATH,
    SYSTEM_B_DATA_ROOT,
)

# decision_rule 别名 → 匹配该规则的全部策略（CLI 便捷入口）。
RULE_ALIASES = ("barbell", "mid_line", "grid", "short_term")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="策略级别回测：只读契约 + B 本地数据，只写 reports/backtest/"
    )
    parser.add_argument(
        "--strategy-id", default="all",
        help="逗号分隔的 strategy_id；或决策规则别名 barbell/mid_line/grid/short_term；"
             "默认 all（全部策略）",
    )
    parser.add_argument("--contract", type=Path, default=STRATEGY_CONTRACT_PATH,
                        help="A 侧策略规则契约（只读）")
    parser.add_argument("--data-root", type=Path, default=SYSTEM_B_DATA_ROOT,
                        help="B 本地数据目录（fund_daily/index_daily/macro）")
    parser.add_argument("--grid-reference", type=Path, default=None,
                        help="网格参考表 CSV（默认 B 侧 outputs/grid_reference_table.csv）")
    parser.add_argument("--start", default=BACKTEST_START_DEFAULT,
                        help="回测起始日期 YYYY-MM-DD（默认 2018-01-02）")
    parser.add_argument("--end", default=None,
                        help="回测截止日期 YYYY-MM-DD（默认数据末端）")
    parser.add_argument("--initial-cash", type=float, default=None,
                        help="初始资金（默认取契约 shared_config.backtest）")
    parser.add_argument("--output-root", type=Path, default=BACKTEST_REPORT_DIR,
                        help="输出目录（只写 B 本地 reports/backtest）")
    parser.add_argument("--no-online", dest="online_ok", action="store_false", default=True,
                        help="行情缺失时不走在线补齐（回测确定性优先）")
    parser.add_argument("--no-macro-tilt", dest="enable_macro_tilt", action="store_false",
                        default=True, help="关闭 mid_line 宏观倾斜（对照实验）")
    parser.add_argument("--lot", type=int, default=TRADE_LOT_SIZE,
                        help="最小整手（默认 100）")
    return parser.parse_args()


def _select_strategies(contract, selector: str):
    """按 selector 选策略：all / 决策规则别名 / 逗号分隔 strategy_id。"""
    if selector == "all":
        return list(contract.strategies)
    if selector in RULE_ALIASES:
        return [s for s in contract.strategies if s.decision_rule == selector]
    ids = [part.strip() for part in selector.split(",") if part.strip()]
    by_id = {s.strategy_id: s for s in contract.strategies}
    selected: list = []
    for sid in ids:
        if sid in by_id:
            selected.append(by_id[sid])
        else:
            print(f"[backtest] {sid} SKIPPED  reason=unknown_strategy_id")
    return selected


def _fmt_metrics(result: BacktestResult) -> str:
    """ASCII 绩效行：total/annual/mdd 用 +-4 位小数，sharpe/turnover 用 2 位。"""
    metrics = result.metrics
    if metrics is None:
        return "total=n/a annual=n/a sharpe=n/a mdd=n/a turnover=n/a"
    turnover = f"{result.turnover:.2f}" if result.turnover is not None else "n/a"
    return (
        f"total={metrics.total_return:+.4f} annual={metrics.annual_return:+.4f} "
        f"sharpe={metrics.sharpe_ratio:.2f} mdd={metrics.max_drawdown:+.4f} "
        f"turnover={turnover}"
    )


def _run_one(strategy, contract, args, phase_lookup) -> BacktestResult:
    """单策略回测；任何引擎异常降级为 ERROR 结果（不中断其他策略）。"""
    if not strategy.enabled:
        return BacktestResult(
            strategy_id=strategy.strategy_id,
            decision_rule=strategy.decision_rule,
            status="SKIPPED",
            error="strategy_disabled",
        )
    try:
        return run_backtest(
            strategy,
            contract,
            data_root=args.data_root,
            grid_reference=args.grid_reference,
            start=args.start,
            end=args.end,
            initial_cash=args.initial_cash,
            online_ok=args.online_ok,
            enable_macro_tilt=args.enable_macro_tilt,
            lot=args.lot,
            phase_lookup=phase_lookup,
        )
    except Exception as exc:
        return BacktestResult(
            strategy_id=strategy.strategy_id,
            decision_rule=strategy.decision_rule,
            status="ERROR",
            error=f"{type(exc).__name__}: {exc}",
        )


def main(args: argparse.Namespace) -> dict:
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    contract = parse_contract(args.contract)  # 契约缺失 → FileNotFoundError
    # 宏观 phase 表全策略共享（点内构建，失败 → 空表，引擎侧处理）。
    phase_lookup = build_phase_lookup(args.data_root)

    counts = {"OK": 0, "SKIPPED": 0, "ERROR": 0}
    reports: list[dict] = []
    for strategy in _select_strategies(contract, args.strategy_id):
        result = _run_one(strategy, contract, args, phase_lookup)
        counts[result.status] += 1
        # 三件套全状态落盘：SKIPPED/ERROR 亦产出占位报告（含 Note/error 说明）。
        written = write_backtest_outputs(result, output_root)
        if result.status == "OK":
            nav = written["nav"]
            print(f"[backtest] {strategy.strategy_id} OK   {_fmt_metrics(result)} nav={nav}")
            reports.append({
                "strategy_id": strategy.strategy_id,
                "status": "OK",
                "nav": str(nav),
            })
        else:
            print(f"[backtest] {strategy.strategy_id} {result.status}  reason={result.error}")
            reports.append({"strategy_id": strategy.strategy_id, "status": result.status,
                            "reason": result.error})

    tail = f" + {counts['ERROR']} ERROR" if counts["ERROR"] else ""
    print(f"[backtest] done {counts['OK']} OK + {counts['SKIPPED']} SKIPPED{tail}")
    return {"reports": reports, "counts": counts}


if __name__ == "__main__":
    main(parse_args())
