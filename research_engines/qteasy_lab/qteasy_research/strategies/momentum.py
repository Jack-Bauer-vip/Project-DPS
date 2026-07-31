"""
动量策略（Momentum）。

基于历史收益率进行排名，选择动量最强的 ETF 分配权重。
支持多种动量计算方式：简单收益率、夏普比率、风险调整后动量。
"""

from __future__ import annotations

import numpy as np
import qteasy as qt

from qteasy_research.strategies.base import BaseStrategy


class MomentumStrategy(BaseStrategy):
    """
    动量策略。

    Parameters
    ----------
    lookback : int
        动量计算回溯期（交易日数）。默认 126（约6个月）。
    top_n : int or None
        选择动量最强的几只ETF。None 表示所有正动量ETF都配置。默认 3。
    method : str
        动量计算方式：
        - "return" : 简单历史收益率
        - "sharp" : 夏普比率（收益/波动率）
        默认 "return"。
    run_freq : str
        调仓频率。默认 "ME"（月末）。
    run_timing : str
        调仓时机。默认 "close"（收盘时）。

    Examples
    --------
    >>> strategy = MomentumStrategy(lookback=126, top_n=3)
    """

    name = "MOMENTUM"
    description = "动量策略"

    def __init__(
        self,
        lookback: int = 126,
        top_n: int | None = 3,
        method: str = "return",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._lookback = lookback
        self._top_n = top_n
        self._method = method

    def build(self) -> qt.GeneralStg:

        lookback = self._lookback
        top_n = self._top_n
        method = self._method

        class _MomentumStg(qt.GeneralStg):
            def __init__(inner_self):
                super().__init__(
                    name="MOMENTUM",
                    description=(
                        f"动量策略，回溯{lookback}日，"
                        f"选前{top_n if top_n else '全部正动量'}名，"
                        f"方法={method}"
                    ),
                    data_types=[
                        qt.StgData(
                            "close",
                            freq="d",
                            asset_type="FD",
                            window_length=lookback,
                        )
                    ],
                )

            def realize(inner_self) -> np.ndarray:
                close_data = inner_self.get_data("close_FD_d")
                close_array = np.asarray(close_data, dtype=float)

                if close_array.shape[0] < 2:
                    return np.zeros(close_array.shape[1], dtype=float)

                n_assets = close_array.shape[1]
                scores = np.full(n_assets, np.nan)

                for i in range(n_assets):
                    prices = close_array[:, i]
                    valid = np.isfinite(prices) & (prices > 0)

                    if valid.sum() < 20:
                        continue

                    valid_prices = prices[valid]

                    if method == "return":
                        # 简单收益率
                        momentum = (
                            valid_prices[-1] / valid_prices[0]
                        ) - 1.0
                        scores[i] = momentum

                    elif method == "sharp":
                        # 夏普比率
                        returns = (
                            valid_prices[1:] / valid_prices[:-1]
                        ) - 1.0
                        mean_ret = np.mean(returns)
                        std_ret = np.std(returns, ddof=1)
                        if std_ret > 0:
                            scores[i] = mean_ret / std_ret * np.sqrt(252)
                        else:
                            scores[i] = 0.0

                target_weights = np.zeros(n_assets, dtype=float)

                valid_indices = np.where(np.isfinite(scores))[0]
                if len(valid_indices) == 0:
                    return target_weights

                if top_n is not None:
                    # 选择动量最强的 top_n 只
                    top_indices = valid_indices[
                        np.argsort(scores[valid_indices])[::-1][:top_n]
                    ]
                    target_weights[top_indices] = 1.0 / len(top_indices)
                else:
                    # 所有正动量等权配置
                    positive = (scores > 0) & np.isfinite(scores)
                    positive_count = int(positive.sum())
                    if positive_count > 0:
                        target_weights[positive] = 1.0 / positive_count
                    else:
                        # 没有正动量时等权
                        target_weights[valid_indices] = (
                            1.0 / len(valid_indices)
                        )

                return target_weights

        return _MomentumStg()
