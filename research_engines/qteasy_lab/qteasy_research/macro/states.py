"""
因子状态量化 — 将宏观数值转换为三态信号。

支持两种状态计算方法：
1. Z-score（默认）：滚动计算标准分，按阈值分 up/neutral/down
2. MA方向（备选）：短期均线与长期均线比较
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qteasy_research.macro import config


class FactorStateClassifier:
    """
    因子状态分类器。

    Parameters
    ----------
    lookback : int
        Z-score 回溯窗口（月），默认从 config.PARAMS 读取。
    z_threshold : float
        状态划分阈值，默认从 config.PARAMS 读取。
    method : str
        "z_score" 或 "ma_cross"。

    Examples
    --------
    >>> clf = FactorStateClassifier(lookback=24, z_threshold=0.5)
    >>> states = clf.classify(pmi_series)
    >>> print(states.tail())
    """

    def __init__(
        self,
        lookback: int | None = None,
        z_threshold: float | None = None,
        method: str | None = None,
    ) -> None:
        self.lookback = lookback or config.PARAMS["lookback"]
        self.z_threshold = z_threshold or config.PARAMS["z_threshold"]
        self.method = method or config.PARAMS["state_method"]

    def rolling_zscore(self, series: pd.Series) -> pd.Series:
        """
        计算滚动 Z-score。

        Z = (当前值 - 窗口内均值) / 窗口内标准差
        """
        roll = series.rolling(window=self.lookback, min_periods=self.lookback)
        mean = roll.mean()
        std = roll.std(ddof=0)
        zscore = (series - mean) / std.replace(0, np.nan)
        zscore.name = series.name
        return zscore

    def ma_cross(self, series: pd.Series) -> pd.Series:
        """
        计算 MA 方向信号。

        返回 -1~1 之间的连续值：
          > 0  短期均线在长期上方（上行趋势）
          < 0  短期均线在长期下方（下行趋势）
        """
        short = series.rolling(window=3, min_periods=3).mean()
        long = series.rolling(window=12, min_periods=12).mean()
        signal = (short - long) / long.replace(0, np.nan)
        signal = signal.clip(-1, 1)
        signal.name = series.name
        return signal

    def classify(self, series: pd.Series) -> pd.Series:
        """
        对因子序列进行三态分类。

        Parameters
        ----------
        series : pd.Series
            以日期为索引的因子值序列（月末频率）。

        Returns
        -------
        pd.Series
            状态值：'up' / 'neutral' / 'down'。
        """
        if self.method == "ma_cross":
            scores = self.ma_cross(series)
            conditions = [
                scores > self.z_threshold,
                scores < -self.z_threshold,
            ]
        else:
            # 默认 Z-score
            scores = self.rolling_zscore(series)
            conditions = [
                scores > self.z_threshold,
                scores < -self.z_threshold,
            ]

        states = pd.Series(
            np.select(conditions, ["up", "down"], default="neutral"),
            index=scores.index,
            name=series.name,
        )
        return states

    def classify_all(
        self, factor_data: dict[str, pd.Series]
    ) -> dict[str, dict[str, str]]:
        """
        对所有因子分类，并返回当前最新状态字典。

        Returns
        -------
        dict
            {因子名: {因子名, label, 最新值, zscore, 状态}}
        """
        result = {}
        for name, series in factor_data.items():
            if series.empty:
                continue

            states = self.classify(series)
            latest_state = states.iloc[-1] if not states.empty else "neutral"

            # 最新的 Z-score 或 MA 值
            if self.method == "ma_cross":
                raw_score = self.ma_cross(series)
            else:
                raw_score = self.rolling_zscore(series)

            latest_score = raw_score.iloc[-1] if not raw_score.empty else 0.0
            latest_value = series.iloc[-1] if not series.empty else 0.0

            factor_cfg = config.get_factor_config(name)
            result[name] = {
                "name": name,
                "label": factor_cfg["label"] if factor_cfg else name,
                "latest_value": round(float(latest_value), 4),
                "score": round(float(latest_score), 4),
                "state": latest_state,
            }

        return result
