# -*- coding: utf-8 -*-

"""
直接从自定义Tushare兼容接口获取基金基础资料，
并写入qteasy的fund_basic表。

用途：
1. 让qteasy识别ETF和LOF代码属于基金类型FD；
2. 为后续ETF组合回测提供基础资料；
3. 避免qteasy错误地把ETF代码按股票E或指数IDX读取。
"""

from __future__ import annotations

import os

import tushare as ts
import qteasy as qt


# ============================================================
# 一、需要注册到qteasy中的基金代码
# ============================================================

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
    "515450.SH",  # 红利低波50ETF
    "159131.SZ",  # 港股通信息技术ETF
    "159516.SZ",  # 半导体设备ETF
]


# ============================================================
# 二、读取接口配置
# ============================================================

# 从当前PowerShell环境变量读取Token
TUSHARE_TOKEN = os.environ.get(
    "TUSHARE_TOKEN",
    "",
).strip()

# 从环境变量读取第三方Tushare兼容接口地址
TUSHARE_API_URL = os.environ.get(
    "TUSHARE_API_URL",
    "",
).strip()

if not TUSHARE_TOKEN:
    raise SystemExit(
        "没有读取到TUSHARE_TOKEN环境变量。"
    )

if not TUSHARE_API_URL:
    raise SystemExit(
        "没有读取到TUSHARE_API_URL环境变量。"
    )


def create_tushare_client():
    """
    创建使用自定义接口地址的Tushare客户端。

    Returns
    -------
    DataApi
        已配置Token和自定义接口地址的客户端。
    """

    client = ts.pro_api(TUSHARE_TOKEN)

    # 将默认官方接口修改为当前兼容接口
    client._DataApi__http_url = TUSHARE_API_URL

    return client


def main() -> None:
    """下载、检查并写入基金基础资料。"""

    print("=" * 70)
    print("导入qteasy基金基础资料")
    print("=" * 70)
    print(f"接口地址：{TUSHARE_API_URL}")

    # 创建Tushare客户端
    pro = create_tushare_client()

    print("\n正在获取场内基金基础资料……")

    # market="E"代表场内基金，包括ETF和部分LOF
    fund_basic = pro.fund_basic(
        market="E"
    )

    if fund_basic is None or fund_basic.empty:
        raise RuntimeError(
            "接口没有返回基金基础资料。"
        )

    # fund_basic必须包含ts_code主键
    if "ts_code" not in fund_basic.columns:
        raise RuntimeError(
            "返回数据缺少ts_code字段，"
            f"当前字段：{fund_basic.columns.tolist()}"
        )

    # 只保留当前研究池中的基金
    target_data = fund_basic[
        fund_basic["ts_code"].isin(FUND_CODES)
    ].copy()

    # 清除可能存在的重复基金代码
    target_data = (
        target_data
        .drop_duplicates(
            subset=["ts_code"],
            keep="last",
        )
        .sort_values("ts_code")
        .reset_index(drop=True)
    )

    found_codes = set(
        target_data["ts_code"].tolist()
    )

    missing_codes = [
        code
        for code in FUND_CODES
        if code not in found_codes
    ]

    print(
        f"接口返回场内基金数量："
        f"{len(fund_basic)}"
    )
    print(
        f"研究池匹配基金数量："
        f"{len(target_data)}"
    )

    print("\n匹配到的基金基础资料：")

    display_columns = [
        column
        for column in [
            "ts_code",
            "name",
            "fund_type",
            "list_date",
            "market",
        ]
        if column in target_data.columns
    ]

    print(
        target_data[
            display_columns
        ].to_string(index=False)
    )

    if missing_codes:
        print("\n以下代码未在fund_basic接口中找到：")

        for code in missing_codes:
            print(f"- {code}")

    if target_data.empty:
        raise RuntimeError(
            "没有可写入qteasy的基金基础资料。"
        )

    print("\n正在写入qteasy的fund_basic表……")

    # update模式：
    # 已有记录执行更新，新记录执行追加
    rows_affected = (
        qt.QT_DATA_SOURCE.update_table_data(
            table="fund_basic",
            df=target_data,
            merge_type="update",
        )
    )

    print(
        f"基金基础资料写入完成，"
        f"受影响行数：{rows_affected}"
    )

    print("\nqteasy数据表检查：")

    overview = qt.get_table_overview(
        tables=[
            "fund_basic",
            "fund_daily",
        ]
    )

    print(overview.to_string())


if __name__ == "__main__":
    try:
        main()

    except Exception as exc:
        print(
            "\n基金基础资料导入失败："
            f"{type(exc).__name__}: {exc}"
        )
        raise