"""
风险平价策略（Risk Parity）。

基于历史波动率计算等风险贡献权重。
风险越高的资产获得越低权重，风险越低的资产获得越高权重。

权重公式：w_i = (1 / σ_i) / Σ(1 / σ_j)
"""

from __future__ import annotations

import numpy as np
import qteasy as qt

from qteasy_research.strategies.base import BaseStrategy


class RiskParityStrategy(BaseStrategy):
    """
    风险平价策略。

    Parameters
    ----------
    window_length : int
        用于计算历史波动率的交易日数。默认 60（约3个月）。
    run_freq : str
        调仓频率。默认 "ME"（月末）。
    run_timing : str
        调仓时机。默认 "close"（收盘时）。

    Examples
    --------
    >>> strategy = RiskParityStrategy(window_length=60)
    >>> operator = qt.Operator(
    ...     strategies=[strategy.build()],
    ...     signal_type="PT",
    ...     run_freq="ME",
    ...     run_timing="close",
    ... )
    """

    name = "RISK_PARITY"
    description = "风险平价策略（等风险贡献）"

    def __init__(
        self,
        window_length: int = 60,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._window_length = window_length

    def build(self) -> qt.GeneralStg:

        window = self._window_length

        class _RiskParityStg(qt.GeneralStg):
            def __init__(inner_self):
                super().__init__(
                    name="RISK_PARITY",
                    description=(
                        f"风险平价策略，基于{window}日历史波动率"
                        f"计算等风险贡献权重"
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
                target_weights = np.zeros(n_assets, dtype=float)

                for i in range(n_assets):
                    col = daily_returns[:, i]
                    valid = np.isfinite(col) & (col != 0)

                    if valid.sum() < 5:
                        continue

                    # 年化波动率
                    volatility = (
                        np.std(col[valid], ddof=1)
                        * np.sqrt(252)
                    )

                    if volatility > 0:
                        target_weights[i] = 1.0 / volatility

                total = target_weights.sum()
                if total > 0:
                    target_weights /= total

                return target_weights

        return _RiskParityStg()
