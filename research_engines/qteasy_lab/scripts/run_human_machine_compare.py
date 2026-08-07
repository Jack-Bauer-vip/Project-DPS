"""人机对比月报 CLI：B 参考维度（应然） vs A 人工干预（实然）月度报告（离线分析）。

读 A 只读数据（``data/logs/human_override_log.csv``）+ 可选 B 决策包
（``decision_ref_package.json``）+ B 本地行情 → 产出
``reports/human_machine_compare/{YYYY-MM}_hmc.md``。

- 只写 B 本地 ``reports/``，不写共享目录、不写系统A、不读 ``systemA_feedback/``。
- 决策包可选项：不指定 ``--package`` 时 B 维度缺失 → 信号中性，报告仍产出骨架
  （不虚构）。联调/正式运行建议指定最新决策包（B 自身产物，读自己合法）。
- 干预后表现用 B 本地行情（``fund_daily.csv``）月收益，行情缺失时该维度降级。

纪律：报告内只出现 ``strategy_id``/``asset_id``（全 ASCII），中文仅限 reason 原文
引用与月报标题。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 让脚本可从任意目录运行（scripts/ 的父目录即 qteasy_lab 包根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qteasy_research.reference.asset_pool import align_pool_price_history, read_active_assets
from qteasy_research.reference.config import (
    PROJECT_ROOT,
    SYSTEM_A_ASSET_POOL,
    SYSTEM_A_HUMAN_OVERRIDE_LOG,
    SYSTEM_B_DATA_ROOT,
)
from qteasy_research.reference.human_machine_compare import (
    build_hmc_report,
    monthly_returns_from_prices,
    parse_human_override_log,
    render_hmc_markdown,
)
from qteasy_research.reference.metadata import today_iso


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="人机对比月报：B 参考维度 vs A 人工干预（离线分析，只写 B reports/）"
    )
    parser.add_argument("--target-month", default=today_iso()[:7],
                        help="报告月份 YYYY-MM，默认当前月")
    parser.add_argument("--human-log", type=Path, default=SYSTEM_A_HUMAN_OVERRIDE_LOG,
                        help="系统A human_override_log.csv（只读）")
    parser.add_argument("--package", type=Path, default=None,
                        help="B 决策包 decision_ref_package.json 路径（可选）")
    parser.add_argument("--data-root", type=Path, default=SYSTEM_B_DATA_ROOT,
                        help="B 本地数据目录（fund_daily.csv）")
    parser.add_argument("--asset-pool", type=Path, default=SYSTEM_A_ASSET_POOL,
                        help="系统A asset_pool.csv")
    parser.add_argument("--output-root", type=Path,
                        default=PROJECT_ROOT / "reports" / "human_machine_compare",
                        help="输出目录（只写 B 本地）")
    parser.add_argument("--no-online", dest="online_ok", action="store_false", default=True,
                        help="行情缺失时不走在线补齐")
    return parser.parse_args()


def _load_package(path: Path | None) -> dict | None:
    if path is None:
        return None
    if not path.exists():
        print(f"[hmc] 决策包不存在（跳过维度对照）：{path}")
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main(args: argparse.Namespace) -> dict:
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    human_df = parse_human_override_log(args.human_log)
    package = _load_package(args.package)

    # 干预后表现：B 本地行情月收益（对齐 active 资产池）。
    monthly: dict = {}
    try:
        assets = read_active_assets(args.asset_pool)
        aligned = align_pool_price_history(assets, data_dir=args.data_root,
                                           online_ok=args.online_ok)
        monthly = monthly_returns_from_prices(aligned)
    except Exception as exc:
        print(f"[hmc] 行情准备失败（干预后表现将降级）：{type(exc).__name__}: {exc}")

    report = build_hmc_report(human_df, package=package, monthly=monthly,
                              month=args.target_month)
    md = render_hmc_markdown(report, args.target_month)
    md_path = output_root / f"{args.target_month}_hmc.md"
    md_path.write_text(md, encoding="utf-8")

    summary = report["summary"]
    print(f"[hmc] 报告写入：{md_path}")
    print(f"[hmc] 干预 {summary['interventions']} 次 / 策略 {summary['strategies']} 个 "
          f"/ 一致 {summary['agree']} 背离 {summary['diverge']} 中性 {summary['neutral']}"
          f" / 一致率 {summary['agree_rate']}%")
    return {"md": str(md_path), "summary": summary}


if __name__ == "__main__":
    main(parse_args())
