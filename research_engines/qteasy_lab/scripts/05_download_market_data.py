"""批量下载指数和ETF数据并写入qteasy。"""

from __future__ import annotations

import os

import tushare as ts

from data_downloader import download_symbols


API_URL = os.environ.get("TUSHARE_API_URL", "").strip()
TOKEN = os.environ.get("TUSHARE_TOKEN", "").strip()

if not API_URL:
    raise SystemExit("没有读取到 TUSHARE_API_URL。")

if not TOKEN:
    raise SystemExit("没有读取到 TUSHARE_TOKEN。")


# 强制Tushare使用自定义接口
_original_pro_api = ts.pro_api


def custom_pro_api(token: str = ""):
    client = _original_pro_api(TOKEN)
    client._DataApi__http_url = API_URL
    return client


ts.pro_api = custom_pro_api
ts.set_token(TOKEN)

import qteasy as qt  # noqa: E402

from datetime import date

START_DATE = "20180101"
END_DATE = date.today().strftime("%Y%m%d")

# 下载任务开关
#True  = 执行下载
#False = 跳过下载
DOWNLOAD_INDEX = True
DOWNLOAD_FUND = False


INDEX_CODES = [
    "000300.SH",  # 沪深300
    "000905.SH",  # 中证500
    "000852.SH",  # 中证1000
]


FUND_CODES = [
    "562800.SH",  # 稀有金属ETF
    "588230.SH",  # 科创200ETF
    "513520.SH",  # 日经ETF
    "159941.SZ",  # 纳指ETF
    "513650.SH",  # 标普500ETF
    "159985.SZ",  # 豆粕ETF
    "513050.SH",  # 中概互联网ETF
    "518880.SH",  # 黄金ETF
    "515180.SH",  # 红利ETF
    "512890.SH",  # 红利低波ETF
    "515450.SH",  # 红利低波50ETF南方
    "159131.SZ",  # 港股通信息技术ETF华宝
    "159516.SZ",  # 半导体设备ETF国泰
    "164824.SZ",  # 印度基金LOF
]


def write_to_qteasy(
    table: str,
    data,
    failures: list[dict[str, str]],
) -> None:
    """检查并写入qteasy数据表。"""

    if data.empty:
        print(f"{table}没有可写入数据。")
    else:
        rows_written = qt.QT_DATA_SOURCE.update_table_data(
            table=table,
            df=data,
            merge_type="update",
        )

        print(
            f"{table}写入完成，"
            f"返回数据{len(data)}行，"
            f"本地表受影响{rows_written}行。"
        )

    if failures:
        print(f"{table}失败记录：")

        for failure in failures:
            print(
                f"- {failure['symbol']}："
                f"{failure['error']}"
            )


def main() -> None:
    pro = ts.pro_api()

    print(f"下载区间：{START_DATE} 至 {END_DATE}")
    print(f"下载指数：{DOWNLOAD_INDEX}")
    print(f"下载基金：{DOWNLOAD_FUND}")
    print()

    if DOWNLOAD_INDEX:
        print("=" * 60)
        print("下载指数日线")
        print("=" * 60)

        index_data, index_failures = download_symbols(
            api_function=pro.index_daily,
            symbols=INDEX_CODES,
            start_date=START_DATE,
            end_date=END_DATE,
        )

        write_to_qteasy(
            table="index_daily",
            data=index_data,
            failures=index_failures,
        )
    else:
        print("已跳过指数下载。")

    if DOWNLOAD_FUND:
        print("\n" + "=" * 60)
        print("下载ETF和LOF日线")
        print("=" * 60)

        fund_data, fund_failures = download_symbols(
            api_function=pro.fund_daily,
            symbols=FUND_CODES,
            start_date=START_DATE,
            end_date=END_DATE,
        )

        write_to_qteasy(
            table="fund_daily",
            data=fund_data,
            failures=fund_failures,
        )
    else:
        print("已跳过ETF和LOF下载。")

    print("\n数据表检查：")

    overview = qt.get_table_overview(
        tables=[
            "index_daily",
            "fund_daily",
        ]
    )

    print(overview.to_string())

if __name__ == "__main__":
    main()
