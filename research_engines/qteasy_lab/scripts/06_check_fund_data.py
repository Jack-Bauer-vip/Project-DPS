"""检查ETF/LOF日线数据是否适合回测。"""

from __future__ import annotations

import pandas as pd
import qteasy as qt


FUND_CODES = [
    "562800.SH",
    "588230.SH",
    "513520.SH",
    "159941.SZ",
    "513650.SH",
    "159985.SZ",
    "513050.SH",
    "518880.SH",
    "515180.SH",
    "512890.SH",
    "515450.SH",
    "159131.SZ",
    "159516.SZ",
]


def main() -> None:
    print("=" * 70)
    print("检查 qteasy fund_daily 数据")
    print("=" * 70)

    data = qt.QT_DATA_SOURCE.read_table_data("fund_daily")

    if data is None or data.empty:
        raise SystemExit("fund_daily表为空，请先下载ETF数据。")

    # qteasy读取后，ts_code和trade_date通常位于索引中
    data = data.reset_index()

    required_columns = {"ts_code", "trade_date", "close"}
    missing_columns = required_columns.difference(data.columns)

    if missing_columns:
        raise SystemExit(
            f"fund_daily缺少字段：{sorted(missing_columns)}\n"
            f"当前字段：{data.columns.tolist()}"
        )

    data["trade_date"] = pd.to_datetime(data["trade_date"])

    records = []

    for code in FUND_CODES:
        symbol_data = data[data["ts_code"] == code].copy()

        if symbol_data.empty:
            records.append(
                {
                    "ts_code": code,
                    "has_data": False,
                    "start_date": None,
                    "end_date": None,
                    "rows": 0,
                    "missing_close": None,
                }
            )
            continue

        records.append(
            {
                "ts_code": code,
                "has_data": True,
                "start_date": symbol_data["trade_date"].min().date(),
                "end_date": symbol_data["trade_date"].max().date(),
                "rows": len(symbol_data),
                "missing_close": int(symbol_data["close"].isna().sum()),
            }
        )

    report = pd.DataFrame(records)

    print("\n各标的数据覆盖情况：")
    print(report.to_string(index=False))

    available = report[report["has_data"]].copy()

    if available.empty:
        raise SystemExit("清单中的ETF均无数据。")

    common_start = pd.to_datetime(available["start_date"]).max()
    common_end = pd.to_datetime(available["end_date"]).min()

    print("\n所有有数据ETF的共同区间：")
    print(f"共同开始日期：{common_start.date()}")
    print(f"共同结束日期：{common_end.date()}")

    missing_symbols = report.loc[
        ~report["has_data"],
        "ts_code",
    ].tolist()

    if missing_symbols:
        print("\n没有数据的代码：")
        for code in missing_symbols:
            print(f"- {code}")
    else:
        print("\n清单中的ETF均有数据。")


if __name__ == "__main__":
    main()