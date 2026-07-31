"""
宏观因子数据获取器 — 本地 CSV 优先，联网仅用于更新。

数据流：
  AKShare API  →  update_data()  →  data/macro/*.csv  →  load_local()  → 评分系统
     (联网)           (写入)            (本地存储)          (读取)
"""

from __future__ import annotations

from pathlib import Path

import akshare as ak
import numpy as np
import pandas as pd

from qteasy_research.macro import config

# AKShare 函数映射（按名称缓存引用）
_API_FUNCS: dict[str, callable] = {}


def _get_api_func(name: str) -> callable:
    """获取 AKShare 接口函数（带缓存）。"""
    if name not in _API_FUNCS:
        _API_FUNCS[name] = getattr(ak, name)
    return _API_FUNCS[name]


class MacroDataFetcher:
    """
    宏观因子数据获取器。

    使用方式
    --------
    >>> f = MacroDataFetcher()
    >>> f.update_all()              # 联网更新所有因子到本地
    >>> pmi = f.load_local("PMI")   # 从本地 CSV 读取
    >>> print(pmi.tail())
    """

    def __init__(self) -> None:
        self._data_dir: Path = config.MACRO_DATA_DIR
        self._data_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------
    # 本地读取（主入口，不需要联网）
    # ---------------------------------------------------------------

    def load_local(self, name: str) -> pd.Series:
        """
        从本地 CSV 读取因子数据。

        返回以日期为索引、数值为值的 Series，日期统一为 Timestamp。
        文件不存在时返回空 Series。
        """
        filepath = self._local_path(name)

        if not filepath.exists():
            return pd.Series(dtype=float, name=name)

        df = pd.read_csv(filepath, index_col=0, parse_dates=True)
        series = df.iloc[:, 0].dropna().astype(float)
        series.name = name
        series.index.name = "date"
        return series

    def load_all(self) -> dict[str, pd.Series]:
        """加载所有已缓存的因子数据。"""
        result = {}
        for factor in config.FACTOR_DEFINITIONS:
            series = self.load_local(factor["name"])
            if not series.empty:
                result[factor["name"]] = series
        return result

    # ---------------------------------------------------------------
    # 联网获取（仅更新时调用）
    # ---------------------------------------------------------------

    def fetch_from_api(self, name: str) -> pd.Series:
        """
        调用 AKShare 接口获取因子最新数据。

        返回以日期为索引、数值为值的 Series。
        """
        factor = config.get_factor_config(name)
        if factor is None:
            raise ValueError(f"未知因子：{name}")

        api_func_name = factor["api_func"]
        func = _get_api_func(api_func_name)
        raw = func()

        series = self._parse_api_response(name, factor, raw)

        if series.empty:
            raise RuntimeError(f"{name}：API 返回数据为空")

        return series

    def fetch_all(self) -> dict[str, pd.Series]:
        """一次性获取所有配置的因子数据。"""
        result = {}
        for factor in config.FACTOR_DEFINITIONS:
            try:
                result[factor["name"]] = self.fetch_from_api(factor["name"])
                print(f"  ✅ {factor['label']} ({factor['name']})")
            except Exception as e:
                print(f"  ❌ {factor['label']} ({factor['name']}): {e}")
        return result

    # ---------------------------------------------------------------
    # 更新与持久化
    # ---------------------------------------------------------------

    def update_data(self, name: str) -> pd.Series:
        """
        更新单个因子数据：联网获取最新数据，合并到本地文件。

        Returns
        -------
        pd.Series
            合并后的完整数据。
        """
        new_data = self.fetch_from_api(name)
        self._save_to_local(name, new_data)
        return new_data

    def update_all(self) -> dict[str, pd.Series]:
        """更新所有因子数据到本地。"""
        print("正在更新宏观因子数据...")
        result = {}
        for factor in config.FACTOR_DEFINITIONS:
            try:
                series = self.update_data(factor["name"])
                result[factor["name"]] = series
                print(f"  ✅ {factor['label']}: {len(series)} 行")
            except Exception as e:
                print(f"  ❌ {factor['label']}: {e}")
        return result

    # ---------------------------------------------------------------
    # 内部方法
    # ---------------------------------------------------------------

    def _local_path(self, name: str) -> Path:
        """本地 CSV 文件路径。"""
        return self._data_dir / f"{name}.csv"

    def _save_to_local(self, name: str, series: pd.Series) -> None:
        """保存 Series 到本地 CSV（日期为索引）。"""
        filepath = self._local_path(name)
        df = series.to_frame(name=name)
        df.to_csv(filepath, encoding="utf-8")

    def _parse_api_response(
        self, name: str, factor: dict, raw_df: pd.DataFrame
    ) -> pd.Series:
        """
        解析各因子的 API 返回格式，提取日期和数值列。

        每个 AKShare 接口返回格式不同，需要按因子特定逻辑解析。
        """
        if raw_df is None or raw_df.empty:
            return pd.Series(dtype=float, name=name)

        df = raw_df.copy()

        if name == "PMI":
            # 字段: 月份, 制造业-指数, ...
            date_col = "月份"
            val_col = "制造业-指数"
            date_fmt = "%Y年%m月份"

        elif name == "CPI":
            # 字段: 月份, 全国-当月, 全国-同比增长, ...
            date_col = "月份"
            val_col = "全国-当月"

        elif name == "PPI":
            date_col = "月份"
            val_col = "当月"

        elif name == "M1":
            # 字段: 月份, 货币(M1)-数量(亿元), 货币(M1)-同比增长, ...
            date_col = "月份"
            val_col = "货币(M1)-同比增长"

        elif name == "CN10Y":
            # bond_zh_us_rate 返回周频数据，列: 日期, 中国国债收益率10年, ...
            date_col = "日期"
            val_col = "中国国债收益率10年"

        elif name == "CN10Y2Y":
            date_col = "日期"
            val_col = "中国国债收益率10年-2年"

        elif name == "SHIBOR3M":
            # 字段: 日期, 3M-定价, ...
            date_col = "日期"
            val_col = "3M-定价"

        elif name == "COMMODITY":
            # macro_china_commodity_price_index: 日期, 最新值, ...
            date_col = "日期"
            val_col = "最新值"

        elif name == "GDP":
            # macro_china_gdp: 季度, 国内生产总值-绝对值, 国内生产总值-同比增长...
            # 只保留单季度数据（不含"1-4季度"等累计值）
            df = df[~df["季度"].str.contains("1-4季度|1-3季度|1-2季度", na=False)].copy()
            date_col = "季度"
            val_col = "国内生产总值-同比增长"

        elif name == "LPR":
            # 找日期列和利率列
            date_col = df.columns[0]
            for candidate in ["LPR1Y"]:
                if candidate in df.columns:
                    val_col = candidate
                    break
            else:
                val_col = df.columns[1]

        else:
            # 默认：第一列为日期，第二列为数值
            date_col = df.columns[0]
            val_col = df.columns[1]

        # 统一处理日期列
        if date_col not in df.columns:
            return pd.Series(dtype=float, name=name)

        # 解析日期
        dates = df[date_col].astype(str)
        if "年" in dates.iloc[0]:
            if "月" in dates.iloc[0]:
                dates = pd.to_datetime(dates, format="%Y年%m月份", errors="coerce")
            elif "季度" in dates.iloc[0] or "季" in dates.iloc[0]:
                # 季度数据如 "2024年第1季度" → 映射到该季末月份
                import re
                def _quarter_to_month(d):
                    m = re.match(r"(\d{4})年第?(\d+)季度", d)
                    if m:
                        year, q = int(m.group(1)), int(m.group(2))
                        return pd.Timestamp(year=year, month=q*3, day=1)
                    return pd.NaT
                dates = dates.apply(_quarter_to_month)
            else:
                dates = pd.to_datetime(dates, errors="coerce")
            dates = pd.to_datetime(dates, errors="coerce")

        # 提取数值
        if val_col not in df.columns:
            return pd.Series(dtype=float, name=name)

        values = pd.to_numeric(df[val_col], errors="coerce")

        # 构建 Series
        series = pd.Series(values.values, index=dates, name=name)
        series = series.dropna().sort_index()

        # 去重（保留最后一个值）
        series = series[~series.index.duplicated(keep="last")]

        # 统一对齐到月末频率
        if factor["frequency"] == "daily":
            series = self._align_month_end(series)

        return series

    @staticmethod
    def _align_month_end(daily_series: pd.Series) -> pd.Series:
        """
        将日频数据对齐到月末。

        取每月最后一个交易日的数据。
        """
        if daily_series.empty:
            return daily_series
        monthly = daily_series.resample("ME").last()
        return monthly

    # ---------------------------------------------------------------
    # 工具方法
    # ---------------------------------------------------------------

    @staticmethod
    def align_to_monthly(
        series_dict: dict[str, pd.Series],
    ) -> pd.DataFrame:
        """
        将多个因子 Series 对齐为月度 DataFrame。

        所有 Series 的日期索引必须可对齐。
        返回 DataFrame，列为因子名，行为月份。
        """
        aligned = pd.DataFrame(series_dict)
        aligned.index.name = "date"
        return aligned
