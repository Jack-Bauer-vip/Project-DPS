"""阶段二第一刀：A 池 active 标的 grid_backtest_eval 批量扫描 + 汇总对比表。

REFERENCE_ONLY：纯研究产出，输出到 B 本地 reports/grid_backtest_eval/{run_prefix}/，
不触碰共享目录 systemB_ref。

用法（从 qteasy_lab 目录执行）:
    ./.venv/Scripts/python.exe -B scripts/grid_backtest_batch.py [--run-prefix 20260825_batch] [--limit-20 588230.SH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from qteasy_research.reference import grid_backtest_eval as gbe  # noqa: E402
from qteasy_research.reference.asset_pool import read_active_assets  # noqa: E402
from qteasy_research.reference.config import PROJECT_ROOT, SYSTEM_A_ROOT  # noqa: E402

DEFAULT_ANCHORS = ["fixed", "weekly_dynamic", "floating"]
DEFAULT_SPREAD_MULTS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
DEFAULT_EXTREME_MULTS = [1.0, 1.5, 2.0, 3.0]


def _load_pool_assets(pool: str) -> tuple[list[str], set[str]]:
    """按池加载标的清单。pool=active（asset_pool.csv active 行）| grid_config（grid_config.csv 去重 30 标）。

    返回 (asset_ids, active_set)；active_set 仅用于汇总表 is_active 标注。
    """
    active_df = read_active_assets()
    active_set = {str(x).strip() for x in active_df["asset_id"] if str(x).strip()}
    if pool == "grid_config":
        path = SYSTEM_A_ROOT / "config" / "grid_config.csv"
        frame = pd.read_csv(path, dtype=str)
        ids = sorted({str(x).strip() for x in frame["asset_id"] if pd.notna(x) and str(x).strip()})
        return ids, active_set
    ids = [str(x).strip() for x in active_df["asset_id"] if str(x).strip()]
    return ids, active_set


def main() -> int:
    ap = argparse.ArgumentParser(description="Grid backtest evaluator batch scan (B-side, REFERENCE_ONLY)")
    ap.add_argument("--run-prefix", default="20260825_batch")
    ap.add_argument("--limit-20", default="588230.SH", help="20% price-limit assets (comma list)")
    ap.add_argument("--pool", default="active", choices=["active", "grid_config"],
                    help="asset pool: active (asset_pool.csv) | grid_config (grid_config.csv 30 assets)")
    args = ap.parse_args()

    limit20 = {s.strip() for s in args.limit_20.split(",") if s.strip()}
    asset_ids, active_set = _load_pool_assets(args.pool)
    print(f"[batch] pool={args.pool} assets: {len(asset_ids)} {asset_ids}")

    out_root = PROJECT_ROOT / "reports" / "grid_backtest_eval" / args.run_prefix
    out_root.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict] = []

    for asset_id in asset_ids:
        limit = 0.20 if asset_id in limit20 else 0.10
        frame = gbe.load_asset_frame(asset_id)
        if frame.empty:
            print(f"[batch] SKIP {asset_id}: no data")
            continue
        summary, matrices = gbe.run_eval(
            asset_id, frame, DEFAULT_ANCHORS, DEFAULT_SPREAD_MULTS, DEFAULT_EXTREME_MULTS, limit
        )
        run_dir = out_root / asset_id
        gbe.write_report(asset_id, summary, matrices, run_dir, limit)

        best_idx = summary["excess_return"].fillna(-999).idxmax()
        best = summary.loc[best_idx]
        per_anchor: dict[str, float] = {}
        for anchor in DEFAULT_ANCHORS:
            sub = summary[summary["anchor_strategy"] == anchor]
            if not sub.empty:
                bi = sub["net_return"].idxmax()
                per_anchor[anchor] = float(sub.loc[bi, "net_return"])
        row = {
            "asset_id": asset_id,
            "is_active": "Y" if asset_id in active_set else "",
            "limit": limit,
            "best_anchor": best["anchor_strategy"],
            "best_spread_mult": float(best["spread_multiplier"]),
            "best_extreme_mult": float(best["extreme_multiplier"]),
            "best_net_return": float(best["net_return"]),
            "best_excess_return": float(best["excess_return"]),
            "benchmark_return": float(best["benchmark_return"]),
            **{f"best_{a}_net": per_anchor.get(a) for a in DEFAULT_ANCHORS},
        }
        summary_rows.append(row)
        print(f"[batch] {asset_id}: best={row['best_anchor']} "
              f"net={row['best_net_return'] * 100:.1f}% excess={row['best_excess_return'] * 100:.1f}%")

    if not summary_rows:
        print("[batch] no results")
        return 1

    df = pd.DataFrame(summary_rows)
    df.to_csv(out_root / "summary_batch.csv", index=False)

    lines = [
        "# Grid Backtest Eval Batch Summary",
        "",
        f"- Assets: {len(df)} | anchors: {DEFAULT_ANCHORS}",
        f"- Combos per asset: {len(DEFAULT_SPREAD_MULTS)}x{len(DEFAULT_EXTREME_MULTS)} spread x extreme",
        "- best_* = best by excess return; fixed/weekly_dynamic/floating cols = best NET return per anchor",
        "",
        "| asset_id | is_active | limit | best_anchor | spread | extreme | net | excess | benchmark | fixed | weekly_dynamic | floating |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for _, r in df.iterrows():
        lines.append(
            f"| {r['asset_id']} | {r['is_active']} | {r['limit']:.2f} | {r['best_anchor']} | "
            f"{r['best_spread_mult']:.2f} | {r['best_extreme_mult']:.2f} | "
            f"{r['best_net_return'] * 100:.2f}% | {r['best_excess_return'] * 100:.2f}% | "
            f"{r['benchmark_return'] * 100:.2f}% | "
            f"{_pct(r.get('best_fixed_net'))} | {_pct(r.get('best_weekly_dynamic_net'))} | {_pct(r.get('best_floating_net'))} |"
        )

    win = df.groupby("best_anchor").size()
    lines += ["", "## Anchor win counts (best by excess)", ""]
    for a in DEFAULT_ANCHORS:
        lines.append(f"- {a}: {int(win.get(a, 0))} assets")
    avg_excess = df.groupby("best_anchor")["best_excess_return"].mean()
    lines += ["", "## Avg best excess by anchor", ""]
    for a in DEFAULT_ANCHORS:
        if a in avg_excess.index:
            lines.append(f"- {a}: {avg_excess[a] * 100:.2f}%")
    avg_net = df.groupby("best_anchor")["best_net_return"].mean()
    lines += ["", "## Avg best net by anchor", ""]
    for a in DEFAULT_ANCHORS:
        if a in avg_net.index:
            lines.append(f"- {a}: {avg_net[a] * 100:.2f}%")

    md = out_root / "summary_batch.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    print(f"[batch] done -> {out_root}")
    print(f"[batch] summary: {md}")
    return 0


def _pct(v: object) -> str:
    return f"{float(v) * 100:.2f}%" if isinstance(v, float) and v == v else "-"


if __name__ == "__main__":
    raise SystemExit(main())
