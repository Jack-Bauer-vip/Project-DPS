"""锚取法滚动样本外对比：sma vs geometric_mean（fixed 策略，spread 1.5）。

回答「sma 是否稳健优于当前主锚（几何均值）」——滚动窗口（750 预热+126 评估+22 步长）
下统计 sma 相对 geom 的窗口超额胜率/最差窗口/平均超额。

REFERENCE_ONLY：输出到 reports/anchor_comparison/rolling_sma_vs_geom/。
用法（从 qteasy_lab 目录执行）:
    ./.venv/Scripts/python.exe -B scripts/anchor_rolling_compare.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from qteasy_research.reference import grid_backtest_eval as gbe  # noqa: E402
from qteasy_research.reference.config import PROJECT_ROOT  # noqa: E402

ASSETS = [
    "159766.SZ", "159781.SZ", "159980.SZ", "159985.SZ", "159992.SZ", "161128.SZ",
    "164824.SZ", "513050.SH", "513100.SH", "513180.SH", "513520.SH", "513690.SH",
    "515180.SH", "515650.SH", "515880.SH", "518880.SH",
]
WINDOW = 750
OOS = 126
STEP = 22


def main() -> int:
    out_root = PROJECT_ROOT / "reports" / "anchor_comparison" / "rolling_sma_vs_geom"
    out_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for asset in ASSETS:
        frame = gbe.load_asset_frame(asset)
        n = len(frame)
        total = WINDOW + OOS
        excess_list: list[float] = []
        start = 0
        while start + total <= n:
            window = frame.iloc[start:start + total]
            r_geom = gbe.run_grid_backtest(window, "fixed", 1.5, 1.0, anchor_method="geometric_mean")
            r_sma = gbe.run_grid_backtest(window, "fixed", 1.5, 1.0, anchor_method="sma")
            g_eval = gbe._eval_window_return(r_geom, OOS)
            s_eval = gbe._eval_window_return(r_sma, OOS)
            if np.isfinite(g_eval) and np.isfinite(s_eval):
                excess_list.append(s_eval - g_eval)
            start += STEP
        arr = np.asarray(excess_list, dtype=float)
        row = {
            "asset": asset,
            "windows": int(len(arr)),
            "win_rate_sma_vs_geom": float((arr > 0).mean()) if len(arr) else float("nan"),
            "avg_excess": float(arr.mean()) if len(arr) else float("nan"),
            "worst_excess": float(arr.min()) if len(arr) else float("nan"),
        }
        rows.append(row)
        print(f"[{asset}] win={row['win_rate_sma_vs_geom']:.2%} "
              f"avg={row['avg_excess'] * 100:.2f}% worst={row['worst_excess'] * 100:.2f}%")

    df = pd.DataFrame(rows)
    df.to_csv(out_root / "rolling_sma_vs_geom.csv", index=False)
    judged = df.dropna(subset=["win_rate_sma_vs_geom"])
    passed = judged[judged["win_rate_sma_vs_geom"] >= 0.60]
    lines = [
        "# Rolling Compare: sma vs geometric_mean (fixed, spread=1.5)",
        "",
        f"- windows: {WINDOW}+{OOS} step={STEP} | assets: {len(df)}",
        "",
        "| asset | windows | win_rate | avg_excess | worst_excess |",
        "|---|---|---|---|---|",
    ]
    for _, r in df.iterrows():
        lines.append(
            f"| {r['asset']} | {int(r['windows'])} | {r['win_rate_sma_vs_geom'] * 100:.1f}% "
            f"| {r['avg_excess'] * 100:.2f}% | {r['worst_excess'] * 100:.2f}% |"
        )
    lines += ["",
              f"- 判定（sma 相对 geom）：win_rate≥60% 的标的 = {len(passed)}/{len(judged)}",
              f"- 平均窗口胜率：{judged['win_rate_sma_vs_geom'].mean() * 100:.1f}%",
              f"- 平均超额：{judged['avg_excess'].mean() * 100:.2f}%",
              f"- 最差窗口平均：{judged['worst_excess'].mean() * 100:.2f}%"]
    md = out_root / "summary.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    print(f"done -> {out_root}")
    print(f"summary: {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
