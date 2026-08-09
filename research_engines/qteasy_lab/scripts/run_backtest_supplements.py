"""回测补充产出 CLI：per-asset 归因 + 网格触网汇总（阶段四 补充）。

应对 A 侧确认清单阻塞级 #2（网格触发明细）与 #3（per-asset 归因）：
- ``{strategy_id}_attribution.csv``：每标平均权重 / 期间收益 / 年化收益贡献 /
  组合最大回撤区间内的权重与收益贡献。
- ``{strategy_id}_grid_summary.csv``（grid 策略）：每标的档距 / 档距来源 / 触网笔数 /
  买卖笔数 / 初始建仓日期与股数。
- ``attribution_{YYYYMM}.md``：汇总 Markdown（含两策略网格汇总）。

口径为 B 侧假设（A 未定义），报告 Assumptions 标注 "B-side; awaiting A confirmation"。
只读契约 + B 本地 data/，只写 ``reports/backtest/``（M-003）。机器输出全 ASCII，
``strategy_id``/``asset_id`` 标识，零中文策略名。

示例：
  python scripts/run_backtest_supplements.py --strategy-id all --no-online
  python scripts/run_backtest_supplements.py --strategy-id grid_lh,grid_scz --no-online
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from qteasy_research.reference.backtest_engine import (
    BACKTEST_START_DEFAULT,
    TRADE_LOT_SIZE,
    BacktestResult,
    build_phase_lookup,
    build_trading_calendar,
    align_asset_prices,
    load_price_frames,
    parse_contract,
    run_backtest,
)
from qteasy_research.reference.config import (
    BACKTEST_REPORT_DIR,
    STRATEGY_CONTRACT_PATH,
    SYSTEM_B_DATA_ROOT,
)
from qteasy_research.reference.metadata import embed_header_csv, today_iso

RULE_ALIASES = ("barbell", "mid_line", "grid", "short_term")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="回测补充产出：per-asset 归因 + 网格触网汇总"
    )
    parser.add_argument(
        "--strategy-id", default="all",
        help="逗号分隔 strategy_id 或规则别名 barbell/mid_line/grid/short_term；默认 all",
    )
    parser.add_argument("--contract", type=Path, default=STRATEGY_CONTRACT_PATH)
    parser.add_argument("--data-root", type=Path, default=SYSTEM_B_DATA_ROOT)
    parser.add_argument("--grid-reference", type=Path, default=None)
    parser.add_argument("--start", default=BACKTEST_START_DEFAULT)
    parser.add_argument("--end", default=None)
    parser.add_argument("--output-root", type=Path, default=BACKTEST_REPORT_DIR)
    parser.add_argument("--no-online", dest="online_ok", action="store_false", default=True)
    parser.add_argument("--lot", type=int, default=TRADE_LOT_SIZE)
    return parser.parse_args()


def _select_strategies(contract, selector: str):
    if selector == "all":
        return list(contract.strategies)
    if selector in RULE_ALIASES:
        return [s for s in contract.strategies if s.decision_rule == selector]
    ids = [p.strip() for p in selector.split(",") if p.strip()]
    by_id = {s.strategy_id: s for s in contract.strategies}
    return [by_id[sid] for sid in ids if sid in by_id]


def _run_one(strategy, contract, args) -> BacktestResult:
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
            online_ok=args.online_ok,
            lot=args.lot,
            phase_lookup=build_phase_lookup(args.data_root),
        )
    except Exception as exc:
        return BacktestResult(
            strategy_id=strategy.strategy_id,
            decision_rule=strategy.decision_rule,
            status="ERROR",
            error=f"{type(exc).__name__}: {exc}",
        )


def _build_price_matrix(strategy, data_root):
    """对齐后的 close 矩阵 + 日历（上市前 NaN）。"""
    frames = load_price_frames(list(strategy.enabled_assets), data_root, online_ok=False)
    calendar = build_trading_calendar(frames)
    prices = align_asset_prices(frames, calendar)
    return prices, calendar


def _replay_positions(trades: pd.DataFrame, enabled: list[str]) -> dict[pd.Timestamp, dict[str, int]]:
    """逐日回放 trades → 每交易日收盘持仓快照（{date: {asset: shares}}）。"""
    holdings = {a: 0 for a in enabled}
    snapshot: dict[pd.Timestamp, dict[str, int]] = {}
    if trades is None or trades.empty:
        return snapshot
    for date, group in trades.groupby("trade_date"):
        for _, row in group.iterrows():
            delta = row["shares"] if row["side"] == "BUY" else -row["shares"]
            holdings[row["asset_id"]] += int(delta)
        snapshot[pd.Timestamp(date)] = dict(holdings)
    return snapshot


def _drawdown_window(nav: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp, float]:
    """组合最大回撤区间 [peak, trough] 与回撤深度。"""
    equity = nav["equity"]
    peak_idx = equity.cummax()
    trough_pos = int(np.argmin(equity / peak_idx - 1.0))
    trough = nav.index[trough_pos]
    dd = float(equity.iloc[trough_pos] / peak_idx.iloc[trough_pos] - 1.0)
    pre = equity[: trough_pos + 1]
    peak_pos = int(np.argmax(pre.values)) if len(pre) else trough_pos
    peak = nav.index[peak_pos]
    return peak, trough, dd


def _annualize(start: pd.Timestamp, end: pd.Timestamp, total_return: float) -> float:
    years = max((end - start).days / 365.25, 1e-9)
    return (1.0 + total_return) ** (1.0 / years) - 1.0


def build_attribution(result: BacktestResult, prices: pd.DataFrame) -> pd.DataFrame:
    """per-asset 归因：平均权重 / 期间收益 / 年化收益贡献 / 回撤区间贡献。

    口径（B-side；awaiting A confirmation）：
    - 平均权重 = 全程每日 标的市值/组合equity 的均值（未持有日计 0）。
    - 期间收益 = 标的自身全程收益（hfq close 首/末）。
    - 年化收益贡献 = 平均权重 × 标的年化收益（自身数据跨度）。
    - 回撤区间贡献 = 组合最大回撤窗口内 平均权重 × 区间收益。
    """
    if result.nav is None or result.trades is None:
        return pd.DataFrame()
    nav = result.nav.set_index("trade_date")
    trades = result.trades
    enabled = sorted(prices.columns)
    snapshots = _replay_positions(trades, enabled)
    equity = nav["equity"]

    peak, trough, dd = _drawdown_window(nav)
    window = (nav.index >= peak) & (nav.index <= trough)

    rows = []
    for asset_id in enabled:
        close = prices[asset_id]
        valid = close.dropna()
        if valid.empty:
            continue
        first_t, last_t = valid.index[0], valid.index[-1]
        period_return = float(valid.iloc[-1] / valid.iloc[0] - 1.0)
        annual = _annualize(first_t, last_t, period_return)

        mktval = pd.Series(0.0, index=nav.index)
        for date, hold in snapshots.items():
            if date in nav.index and hold.get(asset_id, 0) > 0:
                px = close.loc[date]
                if px is not None and np.isfinite(px) and px > 0:
                    mktval.loc[date] = hold[asset_id] * px
        w = mktval / equity.replace(0, np.nan)
        avg_weight = float(w.mean()) if equity.notna().any() else 0.0

        w_win = (mktval / equity.replace(0, np.nan)).loc[window]
        window_ret = float(close.loc[trough] / close.loc[peak] - 1.0) if (
            peak in close.index and trough in close.index
            and np.isfinite(close.loc[peak]) and np.isfinite(close.loc[trough])
        ) else 0.0

        rows.append({
            "strategy_id": result.strategy_id,
            "asset_id": asset_id,
            "avg_weight": round(avg_weight, 4),
            "period_return": round(period_return, 4),
            "annualized_return": round(annual, 4),
            "annual_return_contribution": round(avg_weight * annual, 4),
            "window_peak": peak.strftime("%Y-%m-%d"),
            "window_trough": trough.strftime("%Y-%m-%d"),
            "window_drawdown_pct": round(dd, 4),
            "window_avg_weight": round(float(w_win.mean()), 4),
            "window_return": round(window_ret, 4),
            "window_return_contribution": round(float(w_win.mean()) * window_ret, 4),
        })
    return pd.DataFrame(rows)


def build_grid_summary(result: BacktestResult, prices: pd.DataFrame) -> pd.DataFrame:
    """网格触网汇总：档距/来源/触网笔数/买卖笔数/初始建仓。"""
    if result.trades is None:
        return pd.DataFrame()
    trades = result.trades
    rows = []
    for asset_id in sorted(prices.columns):
        sub = trades[trades["asset_id"] == asset_id]
        buys = sub[sub["side"] == "BUY"]
        sells = sub[sub["side"] == "SELL"]
        first = sub["trade_date"].min() if not sub.empty else None
        init = buys["trade_date"].min() if not buys.empty else None
        init_shares = int(buys.loc[buys["trade_date"] == init, "shares"].sum()) if init else 0
        rows.append({
            "strategy_id": result.strategy_id,
            "asset_id": asset_id,
            "grid_spread": round(result.grid_spreads.get(asset_id, 0.05), 4),
            "spread_source": result.grid_spread_sources.get(asset_id, "fallback"),
            "trigger_count": int(len(sub)),
            "buy_count": int(len(buys)),
            "sell_count": int(len(sells)),
            "first_trigger": first if first else "",
            "initial_buy_date": init if init else "",
            "initial_shares": init_shares,
        })
    return pd.DataFrame(rows)


def main(args: argparse.Namespace) -> list[str]:
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    contract = parse_contract(args.contract)

    written: list[str] = []
    ok: list[BacktestResult] = []
    for strategy in _select_strategies(contract, args.strategy_id):
        result = _run_one(strategy, contract, args)
        if result.status != "OK":
            print(f"[supplement] {strategy.strategy_id} {result.status}  reason={result.error}")
            continue
        ok.append(result)
        prices, _ = _build_price_matrix(strategy, args.data_root)

        att = build_attribution(result, prices)
        if not att.empty:
            att_path = output_root / f"{strategy.strategy_id}_attribution.csv"
            embed_header_csv(
                att_path,
                {"schema_version": "1.0", "source_system": "systemB",
                 "generated_date": today_iso(), "strategy_id": strategy.strategy_id,
                 "content": "per_asset_attribution", "assumption": "B-side"},
                att,
            )
            written.append(str(att_path))
            print(f"[supplement] {strategy.strategy_id} attribution "
                  f"{len(att)} rows -> {att_path.name}")

        if strategy.decision_rule == "grid":
            gs = build_grid_summary(result, prices)
            if not gs.empty:
                gs_path = output_root / f"{strategy.strategy_id}_grid_summary.csv"
                embed_header_csv(
                    gs_path,
                    {"schema_version": "1.0", "source_system": "systemB",
                     "generated_date": today_iso(), "strategy_id": strategy.strategy_id,
                     "content": "grid_trigger_summary", "assumption": "B-side"},
                    gs,
                )
                written.append(str(gs_path))
                print(f"[supplement] {strategy.strategy_id} grid summary "
                      f"{len(gs)} rows -> {gs_path.name}")

    if ok:
        md = _render_markdown(ok, output_root)
        md_path = output_root / f"attribution_{today_iso()[:7]}.md"
        md_path.write_text(md, encoding="utf-8")
        written.append(str(md_path))
        print(f"[supplement] markdown -> {md_path.name}")

    print(f"[supplement] done {len(ok)} strategies, {len(written)} files written")
    return written


def _render_markdown(results: list[BacktestResult], output_root: Path) -> str:
    """汇总 Markdown：per-asset 归因表 + 网格触网汇总表（全 ASCII）。"""
    lines = [
        "# Backtest Supplement - per-asset attribution & grid summary",
        "",
        f"- Generated by systemB; generated_date: {today_iso()}",
        f"- Output dir: {output_root}",
        "",
        "## Assumptions (B-side; awaiting A confirmation)",
        "- avg_weight = mean of daily asset_market_value / portfolio_equity over the full window "
        "(0 on non-holding days)",
        "- period_return = asset hfq close first-to-last return over its own data span",
        "- annual_return_contribution = avg_weight * annualized asset return",
        "- drawdown window = portfolio max drawdown [peak, trough]; "
        "window_return_contribution = window avg_weight * asset return in window",
        "- grid spread = contract suggested_spread -> grid_reference_table -> fallback 5%",
        "",
    ]
    for result in results:
        lines.append(f"## {result.strategy_id} (rule={result.decision_rule})")
        att_path = output_root / f"{result.strategy_id}_attribution.csv"
        lines.append(f"- Attribution CSV: `{att_path.name}`")
        if att_path.exists():
            att = pd.read_csv(att_path, comment="#")
            keep = ["asset_id", "avg_weight", "annualized_return",
                    "annual_return_contribution", "window_drawdown_pct",
                    "window_return_contribution"]
            lines.append("")
            lines.append("| asset_id | avg_weight | annual_ret | annual_contrib | wnd_dd | wnd_contrib |")
            lines.append("|---|---|---|---|---|---|")
            for _, r in att.iterrows():
                lines.append(
                    f"| {r['asset_id']} | {r['avg_weight']:.4f} | {r['annualized_return']:.4f} "
                    f"| {r['annual_return_contribution']:.4f} | {r['window_drawdown_pct']:.4f} "
                    f"| {r['window_return_contribution']:.4f} |"
                )
        if result.decision_rule == "grid":
            gs_path = output_root / f"{result.strategy_id}_grid_summary.csv"
            lines.append("")
            lines.append(f"### Grid trigger summary (`{gs_path.name}`)")
            if gs_path.exists():
                gs = pd.read_csv(gs_path, comment="#")
                lines.append("")
                lines.append("| asset_id | spread | source | triggers | buys | sells | first | init_date | init_shares |")
                lines.append("|---|---|---|---|---|---|---|---|---|")
                for _, r in gs.iterrows():
                    lines.append(
                        f"| {r['asset_id']} | {r['grid_spread']:.4f} | {r['spread_source']} "
                        f"| {r['trigger_count']} | {r['buy_count']} | {r['sell_count']} "
                        f"| {r['first_trigger']} | {r['initial_buy_date']} | {r['initial_shares']} |"
                    )
        lines.append("")
    lines.append("## Disclaimer")
    lines.append("- Machine output by systemB for reference only; all assumptions are B-side "
                 "pending A confirmation (M-003 single direction).")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main(parse_args())
