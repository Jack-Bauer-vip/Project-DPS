"""阶段三参数网格扫描 CLI：间距 → 换手/成本权衡曲线（离线研究，只写 B 本地 reports/）。

读系统A ``asset_pool.csv`` 的 active 资产 + B 本地行情 → 对每资产跑
``sweep_spread_grid``（网格间距 = 基准 × multipliers）→ 输出：
- ``reports/param_sweep/param_sweep_{date}.csv``（long 表，全 ASCII：
  asset_id/spread/trigger_count/annualized_trigger_count/estimated_cost_bps/confidence）
- ``reports/param_sweep/param_sweep_{date}.md``（中文摘要：间距→换手/成本权衡）

只写 B 本地 ``reports/``，不写共享目录、不写系统A（离线研究工具）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本可从任意目录运行（scripts/ 的父目录即 qteasy_lab 包根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from qteasy_research.reference.asset_pool import (
    align_pool_price_history,
    read_active_assets,
)
from qteasy_research.reference.config import (
    PROJECT_ROOT,
    SYSTEM_A_ASSET_POOL,
    SYSTEM_B_DATA_ROOT,
)
from qteasy_research.reference.metadata import today_iso
from qteasy_research.reference.param_sweep import sweep_spread_grid


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="参数网格扫描：间距→换手/成本权衡（离线研究，只写 B reports/）"
    )
    parser.add_argument("--target-date", default=today_iso(), help="数据截止日，默认今天")
    parser.add_argument("--data-root", type=Path, default=SYSTEM_B_DATA_ROOT, help="B 本地数据目录")
    parser.add_argument("--asset-pool", type=Path, default=SYSTEM_A_ASSET_POOL,
                        help="系统A asset_pool.csv")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "reports" / "param_sweep",
                        help="输出目录（只写 B 本地）")
    parser.add_argument("--multipliers", default="0.5,0.75,1.0,1.25,1.5,2.0",
                        help="网格间距乘子（逗号分隔）")
    parser.add_argument("--base-spread", type=float, default=None,
                        help="基准间距（价格比例）；缺省从 60 日年化波动率推导")
    parser.add_argument("--no-online", dest="online_ok", action="store_false", default=True,
                        help="行情缺失时不走在线补齐")
    return parser.parse_args()


def _md_summary(rows: list[dict], names: dict[str, str]) -> str:
    """生成中文摘要：每资产间距→换手/成本权衡表 + 一句话结论。"""
    lines = ["# 参数网格扫描：间距 → 换手/成本权衡", ""]
    by_asset: dict[str, list[dict]] = {}
    for row in rows:
        by_asset.setdefault(row["asset_id"], []).append(row)
    for asset_id, asset_rows in by_asset.items():
        name = names.get(asset_id, "")
        lines.append(f"## {asset_id}（{name}）" if name else f"## {asset_id}")
        lines.append("")
        lines.append("| 间距 | 触发次数(回放期) | 年化触发(次/年) | 估计成本(bps/年) | 置信度 |")
        lines.append("|---|---|---|---|---|")
        for r in asset_rows:
            spread = "-" if r["spread"] is None else f"{r['spread'] * 100:.2f}%"
            lines.append(
                f"| {spread} | {r['trigger_count']} | {r['annualized_trigger_count']} "
                f"| {r['estimated_cost_bps']} | {r['confidence']} |"
            )
        valid = [r for r in asset_rows if r["spread"] is not None]
        if valid:
            costs = [r["estimated_cost_bps"] for r in valid]
            triggers = [r["annualized_trigger_count"] for r in valid]
            lines.append("")
            lines.append(
                f"结论：间距越小换手越高、成本越高；本档成本区间 "
                f"{min(costs)}~{max(costs)} bps/年，年化触发 {min(triggers)}~{max(triggers)} 次/年。"
            )
        else:
            lines.append("")
            lines.append("结论：历史不足或无法推导基准间距，未产出扫描结果。")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def main(args: argparse.Namespace) -> dict:
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    multipliers = tuple(float(x) for x in args.multipliers.split(","))

    assets = read_active_assets(args.asset_pool)
    aligned = align_pool_price_history(assets, data_dir=args.data_root, online_ok=args.online_ok)

    rows: list[dict] = []
    names: dict[str, str] = {}
    for _, row in assets.iterrows():
        asset_id = str(row["asset_id"]).strip()
        names[asset_id] = str(row.get("name") or "")
        frame = aligned.get(asset_id, pd.DataFrame())
        for sweep in sweep_spread_grid(frame, base_spread=args.base_spread,
                                       multipliers=multipliers):
            rows.append({
                "asset_id": asset_id,
                "spread": sweep["spread"],
                "trigger_count": sweep["trigger_count"],
                "annualized_trigger_count": sweep["annualized_trigger_count"],
                "estimated_cost_bps": sweep["estimated_cost_bps"],
                "confidence": sweep["confidence"],
            })

    date_str = args.target_date.replace("-", "")
    csv_path = output_root / f"param_sweep_{date_str}.csv"
    md_path = output_root / f"param_sweep_{date_str}.md"

    csv_frame = pd.DataFrame(rows)
    csv_frame.to_csv(csv_path, index=False, encoding="utf-8")
    md_path.write_text(_md_summary(rows, names), encoding="utf-8")

    print(f"[param_sweep] CSV 写入：{csv_path}")
    print(f"[param_sweep] Markdown 摘要：{md_path}")
    print(f"[param_sweep] 资产数={len(names)} 扫描行={len(rows)} 缺失行情="
          f"{sum(1 for r in rows if r['spread'] is None)}")
    return {"csv": str(csv_path), "md": str(md_path), "rows": len(rows)}


if __name__ == "__main__":
    main(parse_args())
