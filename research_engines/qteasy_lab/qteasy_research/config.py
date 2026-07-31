"""
qteasy 配置管理。

提供统一的配置初始化和检查功能，
确保 qteasy 数据源路径、日志目录、Tushare Token 等配置正确。
"""

from __future__ import annotations

import os
from pathlib import Path

import qteasy as qt

# 项目根目录（qteasy_lab 目录）
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 默认路径
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_SYSTEM_LOG_DIR = PROJECT_ROOT / "logs" / "system"
DEFAULT_TRADE_LOG_DIR = PROJECT_ROOT / "logs" / "trades"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports"


def get_tushare_token() -> str:
    """从环境变量读取 Tushare Token。"""
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "没有读取到 TUSHARE_TOKEN。\n"
            "请先在当前 Shell 中设置环境变量：\n"
            '  set TUSHARE_TOKEN=your_token_here'
        )
    return token


def get_tushare_api_url() -> str:
    """从环境变量读取自定义 Tushare 接口地址（可选）。"""
    return os.environ.get("TUSHARE_API_URL", "").strip()


def ensure_directories() -> None:
    """确保数据、日志、输出目录存在。"""
    for directory in (DEFAULT_DATA_DIR, DEFAULT_SYSTEM_LOG_DIR,
                      DEFAULT_TRADE_LOG_DIR, DEFAULT_OUTPUT_DIR,
                      DEFAULT_REPORT_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    print(f"数据目录：{DEFAULT_DATA_DIR}")
    print(f"系统日志：{DEFAULT_SYSTEM_LOG_DIR}")
    print(f"交易日志：{DEFAULT_TRADE_LOG_DIR}")


def initialize(
    tushare_token: str | None = None,
) -> None:
    """
    初始化 qteasy 配置。

    Parameters
    ----------
    tushare_token:
        可选。不提供则从环境变量读取。
    """
    token = tushare_token or get_tushare_token()
    api_url = get_tushare_api_url()

    ensure_directories()

    qt.update_start_up_setting(
        tushare_token=token,
        local_data_source="file",
        local_data_file_type="csv",
        local_data_file_path=str(DEFAULT_DATA_DIR),
        sys_log_file_path=str(DEFAULT_SYSTEM_LOG_DIR),
        trade_log_file_path=str(DEFAULT_TRADE_LOG_DIR),
    )

    print("qteasy 配置已更新。")
    if api_url:
        print(f"自定义 Tushare 接口：{api_url}")


def check_status() -> dict:
    """
    检查 qteasy 当前配置状态。

    Returns
    -------
    dict
        包含数据源、各数据表概览的字典。
    """
    print(f"qteasy 版本：{qt.__version__}")
    print(f"数据源：{qt.QT_DATA_SOURCE}")

    overview_tables = [
        "trade_calendar",
        "index_basic",
        "index_daily",
        "fund_basic",
        "fund_daily",
    ]

    status = {"data_source": str(qt.QT_DATA_SOURCE)}

    for table in overview_tables:
        try:
            overview = qt.get_table_overview(tables=[table])
            status[table] = overview
            has_data = overview.loc[table, "has_data"] if table in overview.index else False
            print(f"  {table}: {'✅' if has_data else '❌'} "
                  f"{overview.loc[table, 'size'] if has_data else '无数据'}")
        except Exception as e:
            status[table] = str(e)
            print(f"  {table}: ❌ {e}")

    return status
