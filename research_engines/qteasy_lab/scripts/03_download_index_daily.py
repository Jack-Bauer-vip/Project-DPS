"""只下载沪深300指数日线，不重复刷新基础资料。"""

from __future__ import annotations

import os

import tushare as ts


API_URL = os.environ.get("TUSHARE_API_URL", "").strip()
TOKEN = os.environ.get("TUSHARE_TOKEN", "").strip()

if not API_URL:
    raise SystemExit("没有读取到 TUSHARE_API_URL。")

if not TOKEN:
    raise SystemExit("没有读取到 TUSHARE_TOKEN。")


# 保存原始函数
_original_pro_api = ts.pro_api


def custom_pro_api(token: str = ""):
    """强制所有Tushare客户端使用自定义接口和当前Token。"""

    client = _original_pro_api(TOKEN)
    client._DataApi__http_url = API_URL
    return client


# 必须在导入qteasy前完成替换
ts.pro_api = custom_pro_api
ts.set_token(TOKEN)

import qteasy as qt  # noqa: E402
from qteasy import tsfuncs  # noqa: E402


def main() -> None:
    print("=" * 60)
    print("下载沪深300指数日线")
    print("=" * 60)
    print(f"接口地址：{API_URL}")
    print(f"本地数据源：{qt.QT_DATA_SOURCE}")

    # 先通过qteasy函数测试一小段指数日线
    test_data = tsfuncs.index_daily(
        ts_code="000300.SH",
        start="20250101",
        end="20250110",
    )

    if test_data is None or test_data.empty:
        raise RuntimeError("指数日线预检查失败，没有返回数据。")

    print("\n指数日线预检查成功：")
    print(test_data.head())
    print(f"返回行数：{len(test_data)}")

    print("\n开始写入2022—2025年沪深300指数日线……")

    qt.refill_data_source(
        tables="index_daily",
        channel="tushare",
        symbols="000300.SH",
        start_date="20220101",
        end_date="20251231",

        # 关键：不再重复下载index_basic
        refill_dependent_tables=False,

        # 当前测试阶段关闭并行，便于排错
        parallel=False,

        merge_type="update",
    )

    print("\n下载结果检查：")
    overview = qt.get_table_overview(
        tables=["trade_calendar", "index_basic", "index_daily"]
    )
    print(overview.to_string())


if __name__ == "__main__":
    main()