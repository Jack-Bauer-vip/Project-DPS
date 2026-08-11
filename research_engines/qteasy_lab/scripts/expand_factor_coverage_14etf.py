"""因子链路覆盖扩展到全部 14 个 active ETF（阶段一数据迁移）。

步骤：
1. 备份既有 SQLite + factor_values Parquet/manifest 到 ``reports/safety_backup_*``。
2. 复用 ``data_manager.import_existing_local_data`` 把 ``data/fund_daily.csv``
   全量导入 ``data_market_daily``（14 个 active ETF + 既有 index 数据一并入库）。
3. 数据卫生：对同一 (code, trade_date) 同时存在旧 source 与 ``local_csv`` 的行，
   把旧 source 行 ``is_latest=0``（记录保留，仅不再作为最新版本参与计算），
   保证 ``build_factor_values`` 的日期序列无重复。
4. 复用 ``data_manager.build_factor_values`` 重建 4 个因子 Parquet，覆盖 14 标的。
5. 打印验证：``data_market_daily`` 与 4 个 factor Parquet 的 code 集合。

不修改任何既有计算函数，不写共享目录，不触发 ``run_reference_pipeline --real``。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# 让脚本可从任意目录运行（scripts/ 的父目录即 qteasy_lab 包根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from qteasy_research.pretrade.data_manager import DataManager

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
STORE_ROOT = PROJECT_ROOT / "research_store"
FACTOR_IDS = ("momentum_60d", "momentum_120d", "low_volatility_20d", "liquidity_turnover")
BACKUP_DIR = PROJECT_ROOT / "reports" / f"safety_backup_{time.strftime('%Y%m%d')}_factor_phase1"


def _backup() -> Path:
    """把 SQLite 与 factor_values Parquet/manifest 复制到 backup 目录。"""
    if BACKUP_DIR.exists():
        raise FileExistsError(f"备份目录已存在，先人工确认：{BACKUP_DIR}")
    factor_backup = BACKUP_DIR / "factor_values"
    factor_backup.mkdir(parents=True)
    # SQLite 整库拷贝（含 data_market_daily / data_factor_snapshot 等）。
    shutil.copy2(STORE_ROOT / "research.sqlite3", BACKUP_DIR / "research.sqlite3")
    # 既有因子 Parquet + manifest。
    copied: list[str] = []
    for factor_id in FACTOR_IDS:
        parquet = STORE_ROOT / "factor_values" / f"{factor_id}.parquet"
        manifest = STORE_ROOT / "factor_values" / f"{factor_id}.manifest.json"
        if parquet.exists():
            shutil.copy2(parquet, factor_backup / parquet.name)
            copied.append(parquet.name)
        if manifest.exists():
            shutil.copy2(manifest, factor_backup / manifest.name)
    summary = {
        "backup_dir": str(BACKUP_DIR),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sqlite": str(STORE_ROOT / "research.sqlite3"),
        "factor_parquets": copied,
        "note": "阶段一：factor_tear.py 前的因子链路 14 标的扩展备份",
    }
    (BACKUP_DIR / "backup_manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return BACKUP_DIR


def _dedupe_sources() -> int:
    """旧 source 行在 local_csv 行存在时置 is_latest=0（保留记录，仅改最新标记）。"""
    db_path = STORE_ROOT / "research.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE data_market_daily AS old
            SET is_latest = 0
            WHERE old.source != 'local_csv' AND old.is_latest = 1
              AND EXISTS (
                  SELECT 1 FROM data_market_daily AS new
                  WHERE new.code = old.code
                    AND new.trade_date = old.trade_date
                    AND new.source = 'local_csv'
                    AND new.is_latest = 1
              )
            """
        )
        connection.commit()
        return connection.total_changes


def _active_codes() -> list[str]:
    """从 fund_daily.csv 取 14 个 active ETF 代码（与 A asset_pool active 对齐）。"""
    frame = pd.read_csv(DATA_DIR / "fund_daily.csv")
    return sorted(frame["ts_code"].astype(str).str.upper().unique().tolist())


def main() -> None:
    backup_dir = _backup()
    print(f"[1] 备份完成：{backup_dir}")

    manager = DataManager(STORE_ROOT, data_dir=DATA_DIR)
    result = manager.import_existing_local_data(data_dir=DATA_DIR)
    print(f"[2] 导入完成 status={result.status} rows_added={result.rows_added} rows_updated={result.rows_updated}")
    if result.warnings:
        print(f"    warnings: {result.warnings}")

    updated = _dedupe_sources()
    print(f"[3] 数据卫生：旧 source 重复行置非最新 {updated} 行")

    codes = _active_codes()
    print(f"[4] 重建因子（{len(codes)} 标的）...")
    built = manager.build_factor_values(list(FACTOR_IDS), asset_type="ETF", horizon="medium", codes=codes)
    print(f"    build status={built.status} row_count={built.row_count} as_of={built.as_of}")

    # 验证
    rows = manager.store.query_market_daily(limit=10_000_000)
    market_codes = sorted(rows["code"].unique().tolist())
    print("[5] 验证")
    print(f"    data_market_daily 行数={len(rows)} code 数={len(market_codes)}")
    print(f"    data_market_daily codes={market_codes}")
    for factor_id in FACTOR_IDS:
        frame = pd.read_parquet(STORE_ROOT / "factor_values" / f"{factor_id}.parquet")
        fc = sorted(frame["asset_code"].unique().tolist())
        coverage = set(codes).issubset(set(fc))
        print(f"    {factor_id}: rows={len(frame)} codes={fc} 覆盖14标的={coverage}")
    print(f"    14 active codes={codes}")


if __name__ == "__main__":
    main()
