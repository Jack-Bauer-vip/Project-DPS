"""
波动率倒数加权策略（Inverse Volatility）。

基于历史波动率的倒数分配权重。
是风险平价的简化版本，但不保证真正的等风险贡献。

权重公式：w_i = (1 / σ_i) / Σ(1 / σ_j)
"""

from __future__ import annotations

import numpy as np
import qteasy as qt

from qteasy_research.strategies.base import BaseStrategy


class InverseVolatilityStrategy(BaseStrategy):
    """
    波动率倒数加权策略。

    Parameters
    ----------
    window_length : int
        用于计算历史波动率的交易日数。默认 60（约3个月）。
    min_vol : float
        最小波动率阈值，防止权重过于集中。默认 0.05（5%）。
    run_freq : str
        调仓频率。默认 "ME"（月末）。
    run_timing : str
        调仓时机。默认 "close"（收盘时）。

    Examples
    --------
    >>> strategy = InverseVolatilityStrategy(window_length=60)
    """

    name = "INVERSE_VOL"
    description = "波动率倒数加权策略"

    def __init__(
        self,
        window_length: int = 60,
        min_vol: float = 0.05,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._window_length = window_length
        self._min_vol = min_vol

    def build(self) -> qt.GeneralStg:

        window = self._window_length
        min_vol = self._min_vol

        class _InverseVolStg(qt.GeneralStg):
            def __init__(inner_self):
                super().__init__(
                    name="INVERSE_VOL",
                    description=(
                        f"波动率倒数加权，{window}日窗口，"
                        f"最小波动率{min_vol:.0%}"
                    ),
                    data_types=[
                        qt.StgData(
                            "close",
                            freq="d",
                            asset_type="FD",
                            window_length=window,
                        )
                    ],
                )

            def realize(inner_self) -> np.ndarray:
                close_data = inner_self.get_data("close_FD_d")
                close_array = np.asarray(close_data, dtype=float)

                if close_array.shape[0] < 2:
                    return np.zeros(close_array.shape[1], dtype=float)

                # 计算日收益率
                daily_returns = (
                    close_array[1:] / close_array[:-1]
                ) - 1.0

                n_assets = daily_returns.shape[1]
                inv_vols = np.zeros(n_assets, dtype=float)

                for i in range(n_assets):
                    col = daily_returns[:, i]
                    valid = np.isfinite(col) & (col != 0)

                    if valid.sum() < 5:
                        continue

                    vol = np.std(col[valid], ddof=1) * np.sqrt(252)
                    vol = max(vol, min_vol)
                    inv_vols[i] = 1.0 / vol

                total = inv_vols.sum()
                if total > 0:
                    return inv_vols / total
                return np.zeros(n_assets, dtype=float)

        return _InverseVolStg()
