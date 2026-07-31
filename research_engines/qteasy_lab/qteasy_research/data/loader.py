"""
数据加载器 — 检查 qteasy 本地数据源的基金/指数数据完整性。
"""

from __future__ import annotations

import pandas as pd
import qteasy as qt


class DataLoader:
    """
    数据加载器 — 从 qteasy 本地数据源读取并验证数据。

    负责读取 fund_daily、index_daily 等表，
    检查数据覆盖区间和缺失值。
    """

    # 必需的字段
    REQUIRED_COLUMNS = {"ts_code", "trade_date", "close"}

    @classmethod
    def load_fund_daily(cls) -> pd.DataFrame:
        """
        读取基金日线数据。

        Returns
        -------
        pd.DataFrame
            包含 ts_code、trade_date、close 等字段的日线数据。

        Raises
        ------
        RuntimeError
            数据表为空或缺少必需字段。
        """
        data = qt.QT_DATA_SOURCE.read_table_data("fund_daily")

        if data is None or data.empty:
            raise RuntimeError("fund_daily 表为空，请先下载 ETF 历史行情。")

        data = data.reset_index()
        cls._validate_columns(data, "fund_daily")
        data["trade_date"] = pd.to_datetime(data["trade_date"])
        return data

    @classmethod
    def load_index_daily(cls) -> pd.DataFrame:
        """
        读取指数日线数据。

        Returns
        -------
        pd.DataFrame
            包含 ts_code、trade_date、close 等字段的日线数据。
        """
        data = qt.QT_DATA_SOURCE.read_table_data("index_daily")

        if data is None or data.empty:
            raise RuntimeError("index_daily 表为空，请先下载指数行情。")

        data = data.reset_index()
        cls._validate_columns(data, "index_daily")
        data["trade_date"] = pd.to_datetime(data["trade_date"])
        return data

    @classmethod
    def _validate_columns(
        cls, data: pd.DataFrame, table_name: str
    ) -> None:
        """检查数据表是否包含必需字段。"""
        missing = cls.REQUIRED_COLUMNS.difference(data.columns)
        if missing:
            raise RuntimeError(
                f"{table_name} 缺少必要字段：{sorted(missing)}"
            )

    @classmethod
    def get_symbol_coverage(
        cls,
        symbols: list[str],
        fund_data: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """
        获取指定 ETF 代码的数据覆盖情况。

        Parameters
        ----------
        symbols : list[str]
            ETF 代码列表。
        fund_data : pd.DataFrame, optional
            已加载的基金日线数据。不提供则自动加载。

        Returns
        -------
        pd.DataFrame
            每只 ETF 的 has_data、start_date、end_date、rows、missing_close。
        """
        if fund_data is None:
            fund_data = cls.load_fund_daily()

        records = []
        for code in symbols:
            symbol_data = fund_data[fund_data["ts_code"] == code]

            if symbol_data.empty:
                records.append({
                    "ts_code": code,
                    "has_data": False,
                    "start_date": None,
                    "end_date": None,
                    "rows": 0,
                    "missing_close": None,
                })
                continue

            records.append({
                "ts_code": code,
                "has_data": True,
                "start_date": symbol_data["trade_date"].min(),
                "end_date": symbol_data["trade_date"].max(),
                "rows": len(symbol_data),
                "missing_close": int(symbol_data["close"].isna().sum()),
            })

        return pd.DataFrame(records)

    @classmethod
    def get_common_range(
        cls, symbols: list[str]
    ) -> tuple[pd.Timestamp, pd.Timestamp]:
        """
        获取所有指定 ETF 的共同数据区间。

        Parameters
        ----------
        symbols : list[str]
            ETF 代码列表。

        Returns
        -------
        tuple[pd.Timestamp, pd.Timestamp]
            (共同开始日期, 共同结束日期)。
        """
        fund_data = cls.load_fund_daily()
        coverage = cls.get_symbol_coverage(symbols, fund_data)

        available = coverage[coverage["has_data"]]
        if available.empty:
            raise RuntimeError("清单中的 ETF 均无数据。")

        common_start = available["start_date"].max()
        common_end = available["end_date"].min()
        return common_start, common_end
