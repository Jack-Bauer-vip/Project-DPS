"""通过自定义 Tushare 兼容接口下载 qteasy 最小数据。"""

from __future__ import annotations

import os
from pathlib import Path

import tushare as ts


API_URL = os.environ.get("TUSHARE_API_URL", "").strip()
TOKEN = os.environ.get("TUSHARE_TOKEN", "").strip()

if not API_URL:
    raise SystemExit("未设置 TUSHARE_API_URL。")

if not TOKEN:
    raise SystemExit("未设置 TUSHARE_TOKEN。")


# 保存 Tushare 原始 pro_api 函数
_original_pro_api = ts.pro_api


def custom_pro_api(token: str = ""):
    """
    强制 qteasy 创建的每一个 Tushare 客户端：
    1. 使用当前环境变量里的 Token；
    2. 使用自定义 API 地址。
    """
    client = _original_pro_api(TOKEN)
    client._DataApi__http_url = API_URL
    return client


# qteasy 内部调用的是 ts.pro_api()，因此在导入 qteasy 前替换
ts.pro_api = custom_pro_api
ts.set_token(TOKEN)

import qteasy as qt  # noqa: E402
from qteasy import tsfuncs  # noqa: E402


def preflight_test() -> None:
    """先通过 qteasy 自己的数据函数测试接口，避免等待很久后才报错。"""

    print("=" * 60)
    print("qteasy Tushare通道预检查")
    print("=" * 60)

    client = ts.pro_api()

    print(f"自定义接口：{client._DataApi__http_url}")
    print(f"Token长度：{len(TOKEN)}")

    data = tsfuncs.trade_cal(
        exchange="SSE",
        start="20260101",
        end="20260110",
    )

    if data is None or data.empty:
        raise RuntimeError("qteasy通道测试未返回交易日历数据。")

    print(data.head())
    print(f"预检查返回行数：{len(data)}")
    print("qteasy通道预检查成功。\n")


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    data_dir = project_root / "data"

    preflight_test()

    print("=" * 60)
    print("qteasy最小数据下载")
    print("=" * 60)
    print(f"当前数据源：{qt.QT_DATA_SOURCE}")

    print("\n[1/3] 下载交易日历……")
    qt.refill_data_source(
        tables="trade_calendar",
        channel="tushare",
        parallel=False,
    )

    print("\n[2/3] 下载指数基础资料……")
    qt.refill_data_source(
        tables="index_basic",
        channel="tushare",
        parallel=False,
    )

    print("\n[3/3] 下载沪深300指数日线……")
    qt.refill_data_source(
    tables="index_daily",
    channel="tushare",
    symbols="000300.SH",
    start_date="20220101",
    end_date="20251231",

    # index_basic 已经下载成功，不再重复下载依赖表
    refill_dependent_tables=False,

    parallel=False,
)

    print("\n数据目录文件：")

    files = sorted(data_dir.glob("*"))

    if not files:
        print("数据目录为空。")
    else:
        for file_path in files:
            size_kb = file_path.stat().st_size / 1024
            print(f"- {file_path.name}: {size_kb:.1f} KB")

    print("\n下载流程完成。")


if __name__ == "__main__":
    main()