"""B1-2 CLI：生成宏观对冲效率 parquet（四情景 × 每资产，long 格式）。

输出 parquet 并携带 pyarrow key-value 元数据头，供系统A按读侧约定读取
（``pd.read_parquet`` + ``validate_freshness`` 防漂移）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本可从任意目录运行（scripts/ 的父目录即 qteasy_lab 包根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qteasy_research.reference.asset_pool import align_pool_price_history, read_active_assets
from qteasy_research.reference.config import OUTPUT_DIR, SYSTEM_A_ASSET_POOL, SYSTEM_B_DATA_ROOT
from qteasy_research.reference.hedge_efficiency import build_hedge_efficiency
from qteasy_research.reference.macro_scenarios import build_monthly_scenario_table
from qteasy_research.reference.metadata import build_header, embed_header_any, today_iso


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成宏观对冲效率 parquet（B1-2）")
    parser.add_argument("--data-root", type=Path, default=SYSTEM_B_DATA_ROOT, help="项目B本地数据目录")
    parser.add_argument("--asset-pool", type=Path, default=SYSTEM_A_ASSET_POOL, help="系统A asset_pool.csv")
    parser.add_argument("--target-date", default=today_iso(), help="数据截止日（data_asof），默认今天")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "macro_hedge_efficiency.parquet",
                        help="输出 parquet 路径")
    return parser.parse_args()


def main(args: argparse.Namespace) -> Path:
    assets = read_active_assets(args.asset_pool)
    aligned = align_pool_price_history(assets, data_dir=args.data_root)
    macro_table = build_monthly_scenario_table(args.data_root)
    hedge = build_hedge_efficiency(args.data_root, assets, aligned, macro_table)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    header = build_header(generated_date=today_iso(), data_asof=args.target_date)
    embed_header_any(args.output, header, hedge)
    return args.output


if __name__ == "__main__":
    arguments = parse_args()
    path = main(arguments)
    import pandas as pd

    print(f"宏观对冲效率已写入：{path}（{len(pd.read_parquet(path))} 行）")
