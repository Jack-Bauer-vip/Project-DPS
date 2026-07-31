"""
绩效指标 — 计算策略回测的各种绩效指标。

提供夏普比率、索提诺比率、卡尔玛比率、最大回撤、
年化收益率、年化波动率等指标计算。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class MetricsResult:
    """
    绩效指标结果。

    Attributes
    ----------
    total_return : float
        总收益率。
    annual_return : float
        年化收益率。
    annual_volatility : float
        年化波动率。
    sharpe_ratio : float
        夏普比率（无风险利率假设为0）。
    sortino_ratio : float
        索提诺比率。
    calmar_ratio : float
        卡尔玛比率（年化收益 / 最大回撤）。
    max_drawdown : float
        最大回撤。
    max_drawdown_duration : str
        最大回撤持续期。
    win_rate : float
        胜率（日收益为正的比例）。
    benchmark_return : float
        基准收益率。
    benchmark_annual_return : float
        基准年化收益率。
    alpha : float
        Alpha 收益。
    beta : float
        Beta 系数。
    information_ratio : float
        信息比率。
    """

    total_return: float = 0.0
    annual_return: float = 0.0
    annual_volatility: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_duration: str = ""
    win_rate: float = 0.0
    benchmark_return: float = 0.0
    benchmark_annual_return: float = 0.0
    alpha: float = 0.0
    beta: float = 0.0
    information_ratio: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        result = {}
        for key, value in self.__dict__.items():
            if isinstance(value, float):
                if any(k in key for k in ("return", "volatility", "ratio",
                                          "drawdown", "rate", "alpha", "beta")):
                    result[key] = round(value, 4)
                else:
                    result[key] = round(value, 4)
            else:
                result[key] = value
        return result


class PerformanceMetrics:
    """
    绩效指标计算器。

    Examples
    --------
    >>> metrics = PerformanceMetrics()
    >>> result = metrics.calculate(
    ...     daily_returns=[...],
    ...     benchmark_returns=[...],
    ... )
    """

    RISK_FREE_RATE = 0.0
    TRADING_DAYS = 252

    @classmethod
    def calculate(
        cls,
        daily_returns: np.ndarray | pd.Series | list[float],
        benchmark_returns: np.ndarray | pd.Series | list[float] | None = None,
    ) -> MetricsResult:
        """
        计算全面的绩效指标。

        Parameters
        ----------
        daily_returns : array-like
            策略日收益率序列。
        benchmark_returns : array-like, optional
            基准日收益率序列。

        Returns
        -------
        MetricsResult
            所有计算出的绩效指标。
        """
        returns = np.asarray(daily_returns, dtype=float)
        returns = returns[np.isfinite(returns)]

        if len(returns) < 5:
            return MetricsResult()

        total_return = cls._total_return(returns)
        ann_return = cls._annual_return(returns)
        ann_vol = cls._annual_volatility(returns)
        sharpe = cls._sharpe_ratio(returns)
        sortino = cls._sortino_ratio(returns)
        max_dd, dd_duration = cls._max_drawdown(returns)
        win_rate = cls._win_rate(returns)
        calmar = ann_return / abs(max_dd) if max_dd != 0 else 0.0

        result = MetricsResult(
            total_return=total_return,
            annual_return=ann_return,
            annual_volatility=ann_vol,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            max_drawdown=max_dd,
            max_drawdown_duration=dd_duration,
            win_rate=win_rate,
        )

        if benchmark_returns is not None:
            bench = np.asarray(benchmark_returns, dtype=float)
            bench = bench[np.isfinite(bench)]

            if len(bench) > 0:
                result.benchmark_return = cls._total_return(bench)
                result.benchmark_annual_return = cls._annual_return(bench)
                alpha, beta = cls._alpha_beta(returns, bench)
                result.alpha = alpha
                result.beta = beta
                result.information_ratio = cls._information_ratio(returns, bench)

        return result

    @classmethod
    def _total_return(cls, returns: np.ndarray) -> float:
        """计算累计总收益率。"""
        return float(np.prod(1 + returns) - 1)

    @classmethod
    def _annual_return(cls, returns: np.ndarray) -> float:
        """计算年化收益率。"""
        n = len(returns)
        total_ret = cls._total_return(returns)
        years = n / cls.TRADING_DAYS
        if years <= 0:
            return 0.0
        return float((1 + total_ret) ** (1 / years) - 1)

    @classmethod
    def _annual_volatility(cls, returns: np.ndarray) -> float:
        """计算年化波动率。"""
        if len(returns) < 2:
            return 0.0
        return float(np.std(returns, ddof=1) * np.sqrt(cls.TRADING_DAYS))

    @classmethod
    def _sharpe_ratio(cls, returns: np.ndarray) -> float:
        """计算夏普比率。"""
        vol = cls._annual_volatility(returns)
        if vol <= 0:
            return 0.0
        ann_ret = cls._annual_return(returns)
        return float((ann_ret - cls.RISK_FREE_RATE) / vol)

    @classmethod
    def _sortino_ratio(cls, returns: np.ndarray) -> float:
        """计算索提诺比率（只考虑下行波动）。"""
        ann_ret = cls._annual_return(returns)
        downside = returns[returns < 0]
        if len(downside) < 2:
            return 0.0
        downside_vol = float(
            np.std(downside, ddof=1) * np.sqrt(cls.TRADING_DAYS)
        )
        if downside_vol <= 0:
            return 0.0
        return float((ann_ret - cls.RISK_FREE_RATE) / downside_vol)

    @classmethod
    def _max_drawdown(
        cls, returns: np.ndarray
    ) -> tuple[float, str]:
        """计算最大回撤及持续期。"""
        cum_returns = np.cumprod(1 + returns)
        peak = np.maximum.accumulate(cum_returns)
        drawdowns = (cum_returns - peak) / peak
        max_dd = float(np.min(drawdowns))

        # 计算最大回撤持续期
        trough_idx = np.argmin(drawdowns)
        peak_idx = np.argmax(peak[:trough_idx + 1]) if trough_idx > 0 else 0

        # 找出恢复日期
        recovery_idx = trough_idx
        for i in range(trough_idx, len(drawdowns)):
            if drawdowns[i] >= 0:
                recovery_idx = i
                break

        trough_to_recovery = recovery_idx - trough_idx
        if trough_to_recovery > 0:
            duration = f"{trough_to_recovery} 交易日"
        else:
            duration = "尚未恢复"

        return max_dd, duration

    @classmethod
    def _win_rate(cls, returns: np.ndarray) -> float:
        """计算胜率（正收益日占比）。"""
        if len(returns) == 0:
            return 0.0
        return float(np.mean(returns > 0))

    @classmethod
    def _alpha_beta(
        cls, returns: np.ndarray, benchmark: np.ndarray
    ) -> tuple[float, float]:
        """计算 Alpha 和 Beta。"""
        min_len = min(len(returns), len(benchmark))
        r = returns[:min_len]
        b = benchmark[:min_len]

        cov = np.cov(r, b)
        beta = cov[0, 1] / cov[1, 1] if cov[1, 1] > 0 else 0.0
        alpha = cls._annual_return(r) - beta * cls._annual_return(b)
        return alpha, beta

    @classmethod
    def _information_ratio(
        cls, returns: np.ndarray, benchmark: np.ndarray
    ) -> float:
        """计算信息比率。"""
        min_len = min(len(returns), len(benchmark))
        excess = returns[:min_len] - benchmark[:min_len]
        tracking_error = float(np.std(excess, ddof=1) * np.sqrt(cls.TRADING_DAYS))
        if tracking_error <= 0:
            return 0.0
        return float(cls._annual_return(excess) / tracking_error)
