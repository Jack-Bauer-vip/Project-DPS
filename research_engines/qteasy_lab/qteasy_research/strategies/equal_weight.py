"""
等权目标仓位策略。

每次调仓时，对所有有有效价格的 ETF 平均分配权重。
适用于作为多策略对比的基准策略。
"""

from __future__ import annotations

import numpy as np
import qteasy as qt

from qteasy_research.strategies.base import BaseStrategy


class EqualWeightStrategy(BaseStrategy):
    """
    等权目标仓位策略。

    Parameters
    ----------
    window_length : int
        每次策略运行读取的历史交易日数。默认 2。
    run_freq : str
        调仓频率。默认 "ME"（月末）。
    run_timing : str
        调仓时机。默认 "close"（收盘时）。

    Examples
    --------
    >>> strategy = EqualWeightStrategy()
    >>> operator = qt.Operator(
    ...     strategies=[strategy.build()],
    ...     signal_type="PT",
    ...     run_freq="ME",
    ...     run_timing="close",
    ... )
    """

    name = "EQUAL_WEIGHT"
    description = "等权目标仓位策略"

    def __init__(
        self,
        window_length: int = 2,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._window_length = window_length

    def build(self) -> qt.GeneralStg:
        window = self._window_length

        class _EqualWeightStg(qt.GeneralStg):
            def __init__(inner_self):
                super().__init__(
                    name="EQUAL_WEIGHT",
                    description=(
                        f"等权目标仓位策略，"
                        f"对所有有有效价格的ETF平均分配权重"
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
                latest_close = close_array[-1]

                valid_mask = (
                    np.isfinite(latest_close) & (latest_close > 0)
                )
                target_weights = np.zeros(
                    latest_close.shape[0], dtype=float,
                )
                valid_count = int(valid_mask.sum())
                if valid_count > 0:
                    target_weights[valid_mask] = 1.0 / valid_count
                return target_weights

        return _EqualWeightStg()
