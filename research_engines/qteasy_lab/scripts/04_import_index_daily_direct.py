"""直接获取并写入沪深300指数日线，绕过qteasy基础表参数解析。"""

from __future__ import annotations

import os

import tushare as ts

from datetime import date

API_URL = os.environ.get("TUSHARE_API_URL", "").strip()
TOKEN = os.environ.get("TUSHARE_TOKEN", "").strip()

if not API_URL:
    raise SystemExit("没有读取到 TUSHARE_API_URL。")

if not TOKEN:
    raise SystemExit("没有读取到 TUSHARE_TOKEN。")


# 保存原始Tushare客户端创建函数
_original_pro_api = ts.pro_api


def custom_pro_api(token: str = ""):
    """创建使用自定义接口和当前Token的Tushare客户端。"""

    client = _original_pro_api(TOKEN)
    client._DataApi__http_url = API_URL
    return client


ts.pro_api = custom_pro_api
ts.set_token(TOKEN)

import qteasy as qt  # noqa: E402


def main() -> None:
    print("=" * 60)
    print("直接导入沪深300指数日线")
    print("=" * 60)
    print(f"接口地址：{API_URL}")
    print(f"本地数据源：{qt.QT_DATA_SOURCE}")

    pro = ts.pro_api()

    print("\n正在获取2022—2025年沪深300指数日线……")

    data = pro.index_daily(
    # 沪深300指数代码
    ts_code="000300.SH",

    # 从2018年开始下载，为2019年的回测预留历史数据
    start_date="20180101",

    # 自动使用当前日期，不再手工修改结束日期
    end_date=date.today().strftime("%Y%m%d"),
    )

    if data is None or data.empty:
        raise RuntimeError("没有获取到沪深300指数日线数据。")

    required_columns = {"ts_code", "trade_date"}
    missing_columns = required_columns.difference(data.columns)

    if missing_columns:
        raise RuntimeError(
            f"返回数据缺少必要字段：{sorted(missing_columns)}"
        )

    data = (
        data
        .drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        .sort_values(["ts_code", "trade_date"])
        .reset_index(drop=True)
    )

    print(f"获取数据行数：{len(data)}")
    print(f"最早日期：{data['trade_date'].min()}")
    print(f"最新日期：{data['trade_date'].max()}")
    print("\n数据示例：")
    print(data.head())

    print("\n正在写入qteasy的index_daily表……")

    rows_written = qt.QT_DATA_SOURCE.update_table_data(
        table="index_daily",
        df=data.copy(),
        merge_type="update",
    )

    print(f"写入完成，受影响行数：{rows_written}")

    print("\n数据表检查：")

    overview = qt.get_table_overview(
        tables=[
            "trade_calendar",
            "index_basic",
            "index_daily",
        ]
    )

    print(overview.to_string())


if __name__ == "__main__":
    main()