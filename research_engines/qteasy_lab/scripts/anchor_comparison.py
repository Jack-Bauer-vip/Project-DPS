"""锚公式对比实验：16 个可判定标的 × 5 锚取法（fixed 策略，spread 1.0）。

REFERENCE_ONLY：纯研究产出，输出到 reports/anchor_comparison/，不进共享目录。
用法（从 qteasy_lab 目录执行）:
    ./.venv/Scripts/python.exe -B scripts/anchor_comparison.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from qteasy_research.reference import grid_backtest_eval as gbe  # noqa: E402
from qteasy_research.reference.config import PROJECT_ROOT  # noqa: E402

# 16 个可判定标的（滚动样本外验证中历史 ≥876 日、窗口 ≥10）
ASSETS = [
    "159766.SZ", "159781.SZ", "159980.SZ", "159985.SZ", "159992.SZ", "161128.SZ",
    "164824.SZ", "513050.SH", "513100.SH", "513180.SH", "513520.SH", "513690.SH",
    "515180.SH", "515650.SH", "515880.SH", "518880.SH",
]
METHODS = ["geometric_mean", "vwap", "sma", "swing_mid", "fibonacci"]


def main() -> int:
    out_root = PROJECT_ROOT / "reports" / "anchor_comparison"
    out_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for asset in ASSETS:
        frame = gbe.load_asset_frame(asset)
        if frame.empty:
            print(f"[skip] {asset}: no data")
            continue
        for method in METHODS:
            r = gbe.run_grid_backtest(frame, "fixed", 1.0, 1.0, anchor_method=method)
            rows.append({
                "asset": asset,
                "method": method,
                "net_return": r.net_return,
                "max_drawdown": r.max_drawdown,
                "annual_turnover": r.annual_turnover,
                "buy_trades": r.buy_trades,
            })
    df = pd.DataFrame(rows)
    df.to_csv(out_root / "summary_comparison.csv", index=False)

    # 每标内按净收益排名（1=最优）
    df["rank"] = df.groupby("asset")["net_return"].rank(ascending=False)
    best_count = df[df["rank"] == 1].groupby("method")["asset"].count()
    avg_rank = df.groupby("method")["rank"].mean()
    avg_net = df.groupby("method")["net_return"].mean()
    avg_mdd = df.groupby("method")["max_drawdown"].mean()
    # 相对 geometric_mean 基准的胜场
    geom = df[df["method"] == "geometric_mean"].set_index("asset")["net_return"]
    beat_geom = (
        df[df["method"] != "geometric_mean"]
        .groupby("method")
        .apply(lambda g: int((g["net_return"] > geom.reindex(g["asset"]).values).sum()))
    )

    lines = [
        "# Anchor Method Comparison (16 assets x 5 methods)",
        "",
        f"- assets: {len(ASSETS)} | methods: {METHODS}",
        "- anchor strategy: fixed | spread_multiplier=1.0 | extreme=1.0",
        "",
        "| method | best_count | avg_rank | avg_net | avg_mdd | beat_geomean |",
        "|---|---|---|---|---|---|",
    ]
    for m in METHODS:
        lines.append(
            f"| {m} | {int(best_count.get(m, 0))} | {avg_rank.get(m, float('nan')):.2f} "
            f"| {avg_net.get(m, float('nan')) * 100:.2f}% "
            f"| {avg_mdd.get(m, float('nan')) * 100:.2f}% "
            f"| {int(beat_geom.get(m, 0))} |"
        )
    lines += ["", "## Per-asset net return by method", "",
              "| asset | geometric_mean | vwap | sma | swing_mid | fibonacci |",
              "|---|---|---|---|---|---|"]
    pivot = df.pivot_table(index="asset", columns="method", values="net_return")
    for asset in ASSETS:
        if asset not in pivot.index:
            continue
        row = pivot.loc[asset]
        lines.append(
            f"| {asset} | " + " | ".join(
                f"{row[m] * 100:.2f}%" if pd.notna(row[m]) else "-" for m in METHODS
            ) + " |"
        )
    md = out_root / "summary_comparison.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    print(f"done -> {out_root}")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
