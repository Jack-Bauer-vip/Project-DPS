"""阶段一端到端管线入口：三件套 → dry-run 写 outputs/ 或写共享目录。

- 默认 ``--dry-run``：调 ``run_pipeline(output_root=outputs/, ...)``，只产出三件套
  （grid_reference_table.csv / macro_hedge_efficiency.parquet /
  decision_ref_package.json + assets_metadata.csv）到本地 outputs/。
- ``--real``：写真实共享目录 ``D:\\FF Project\\data\\integration\\``；进入前显式调
  ``IntegrationDir.ensure_root()`` 兜底创建根目录（仅根 + WARNING 日志，不因 A 未建目录
  而崩溃），对齐评审决议 4.4-4（``require_exists`` 严格模式仅用于需人工确认的场景）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本可从任意目录运行（scripts/ 的父目录即 qteasy_lab 包根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qteasy_research.reference.config import (
    OUTPUTS_DIR,
    SYSTEM_A_ASSET_POOL,
    SYSTEM_B_DATA_ROOT,
)
from qteasy_research.reference.metadata import today_iso
from qteasy_research.reference.pipeline import run_pipeline
from qteasy_research.reference.shared_dir import IntegrationDir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="阶段一端到端参考维度管线（B1-1/B1-2 + 决策包）")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                        help="dry-run 写本地 outputs/（默认，不碰共享目录）")
    parser.add_argument("--real", dest="dry_run", action="store_false",
                        help="真实写入共享目录（A 建好目录后启用）")
    parser.add_argument("--target-date", default=today_iso(), help="数据截止日（data_asof），默认今天")
    parser.add_argument("--data-root", type=Path, default=SYSTEM_B_DATA_ROOT, help="B 本地数据目录")
    parser.add_argument("--asset-pool", type=Path, default=SYSTEM_A_ASSET_POOL, help="系统A asset_pool.csv")
    parser.add_argument("--output-root", type=Path, default=OUTPUTS_DIR,
                        help="dry-run 输出目录（默认 outputs/）")
    parser.add_argument("--include-stress", dest="include_stress", action="store_true",
                        help="计算宏观压力情景损益并填充 macro_stress（默认不计算）")
    return parser.parse_args()


def main(args: argparse.Namespace) -> dict:
    if args.dry_run:
        record = run_pipeline(
            target_date=args.target_date,
            data_root=args.data_root,
            asset_pool=args.asset_pool,
            output_root=args.output_root,
            include_stress=args.include_stress,
        )
        print(f"[dry-run] 三件套写入：{record['output_root']}")
        for filename in record["files"]:
            print(f"  - {Path(record['output_root']) / filename}")
        print(f"[dry-run] status={record['status']} warnings={len(record['warnings'])}")
        return record

    integration = IntegrationDir()
    # 非 dry-run 进入前显式兜底创建共享目录根目录（仅根 + WARNING），不崩溃。
    integration.ensure_root()
    record = run_pipeline(
        target_date=args.target_date,
        data_root=args.data_root,
        asset_pool=args.asset_pool,
        integration=integration,
    )
    print(f"[real] 共享目录写入：{integration.root / 'systemB_ref' / record['run_id']}")
    print(f"[real] status={record['status']} verify.ok={record['verify'].get('ok')} "
          f"warnings={len(record['warnings'])}")
    return record


if __name__ == "__main__":
    main(parse_args())
