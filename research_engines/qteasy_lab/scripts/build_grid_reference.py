"""B1-1 CLI：生成网格参考表 CSV（每资产一行宽表）。

默认从系统A资产池 + 项目B本地行情构建；输出 CSV 带元数据头
（``# generated_date=…; data_asof=…``），与共享目录读侧约定一致。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本可从任意目录运行（scripts/ 的父目录即 qteasy_lab 包根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from qteasy_research.reference.asset_pool import align_pool_price_history, read_active_assets
from qteasy_research.reference.config import OUTPUT_DIR, SYSTEM_A_ASSET_POOL, SYSTEM_B_DATA_ROOT
from qteasy_research.reference.grid_reference import build_grid_reference
from qteasy_research.reference.metadata import build_header, embed_header_any, today_iso


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成网格参考表 CSV（B1-1）")
    parser.add_argument("--data-root", type=Path, default=SYSTEM_B_DATA_ROOT, help="项目B本地数据目录")
    parser.add_argument("--asset-pool", type=Path, default=SYSTEM_A_ASSET_POOL, help="系统A asset_pool.csv")
    parser.add_argument("--target-date", default=today_iso(), help="数据截止日（data_asof），默认今天")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "grid_reference_table.csv",
                        help="输出 CSV 路径")
    return parser.parse_args()


def main(args: argparse.Namespace) -> Path:
    assets = read_active_assets(args.asset_pool)
    aligned = align_pool_price_history(assets, data_dir=args.data_root)
    grid = build_grid_reference(assets, aligned)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    header = build_header(generated_date=today_iso(), data_asof=args.target_date)
    embed_header_any(args.output, header, grid)
    return args.output


if __name__ == "__main__":
    arguments = parse_args()
    path = main(arguments)
    print(f"网格参考表已写入：{path}（{sum(1 for _ in open(path, encoding='utf-8')) - 1} 行资产）")
