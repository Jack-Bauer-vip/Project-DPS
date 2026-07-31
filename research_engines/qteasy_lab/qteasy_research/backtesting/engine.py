"""
回测引擎 — 策略回测的运行器。

封装 qteasy 的 qt.run() 接口，提供统一的配置方式、
结果返回和错误处理。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import pandas as pd
import qteasy as qt

from qteasy_research.strategies.base import BaseStrategy
from qteasy_research import config as cfg


@dataclass
class BacktestConfig:
    """
    回测配置。

    Attributes
    ----------
    strategy : BaseStrategy
        策略实例。
    asset_pool : list[str]
        ETF 代码列表。
    cash : float
        初始资金。默认 100,000。
    start : str
        回测开始日期，YYYYMMDD 格式。默认 "20190801"。
    end : str or None
        回测结束日期。None 表示自动使用数据的最后共同日期。
    buy_fee_rate : float
        买入费率。默认 0.00016（万1.6）。
    sell_fee_rate : float
        卖出费率。默认 0.00016（万1.6）。
    trade_batch_size : int
        最小交易单位（份数）。默认 100。
    benchmark : str
        业绩比较基准代码。默认 "000300.SH"（沪深300）。
    asset_type : str
        资产类型。默认 "FD"（基金）。
    signal_type : str
        信号类型。默认 "PT"（目标持仓比例）。
    run_freq : str
        调仓频率。默认 "ME"（月末）。
    run_timing : str
        调仓时机。默认 "close"（收盘时）。
    output_dir : str or Path
        输出目录。
    report : bool
        是否输出文字回测报告。默认 True。
    visual : bool
        是否输出图表。默认 False。
    trade_log : bool
        是否保存交易日志。默认 True。
    """

    strategy: BaseStrategy
    asset_pool: list[str]
    cash: float = 100_000.0
    start: str = "20190801"
    end: str | None = None
    buy_fee_rate: float = 0.00016
    sell_fee_rate: float = 0.00016
    trade_batch_size: int = 100
    benchmark: str = "000300.SH"
    asset_type: str = "FD"
    signal_type: str = "PT"
    run_freq: str = "ME"
    run_timing: str = "close"
    output_dir: str | Path = ""
    report: bool = True
    visual: bool = False
    trade_log: bool = True


@dataclass
class BacktestResult:
    """
    回测结果。

    Attributes
    ----------
    raw_result : dict
        qteasy qt.run() 返回的原始结果字典。
    config : BacktestConfig
        回测配置。
    strategy_name : str
        策略名称。
    success : bool
        是否成功。
    error : str or None
        错误信息（如有）。
    """
    raw_result: dict | None = None
    config: BacktestConfig | None = None
    strategy_name: str = ""
    success: bool = False
    error: str | None = None


class BacktestEngine:
    """
    回测引擎 — 运行策略回测并返回结构化结果。

    Parameters
    ----------
    config : BacktestConfig
        回测配置。

    Examples
    --------
    >>> from qteasy_research.strategies.equal_weight import EqualWeightStrategy
    >>> from qteasy_research.backtesting.engine import BacktestEngine, BacktestConfig
    >>> strategy = EqualWeightStrategy()
    >>> config = BacktestConfig(
    ...     strategy=strategy,
    ...     asset_pool=["518880.SH", "159941.SZ"],
    ...     cash=100_000,
    ... )
    >>> engine = BacktestEngine(config)
    >>> result = engine.run()
    >>> if result.success:
    ...     print(f"年化收益: {result.raw_result.get('yearly_return'):.2%}")
    """

    def __init__(self, config: BacktestConfig) -> None:
        self.config = config

    def run(self) -> BacktestResult:
        """
        执行回测。

        Returns
        -------
        BacktestResult
            结构化的回测结果。
        """
        strategy = self.config.strategy
        strategy_name = strategy.name
        cfg_ = self.config

        # 自动确定回测结束日期
        if cfg_.end is None:
            end_date = self._determine_end_date(cfg_.asset_pool)
        else:
            end_date = cfg_.end

        print("=" * 70)
        print(f"回测策略：{strategy_name}")
        print(f"资产池：{cfg_.asset_pool}")
        print(f"回测区间：{cfg_.start} 至 {end_date}")
        print(f"初始资金：{cfg_.cash:,.2f} 元")
        print(f"买入费率：{cfg_.buy_fee_rate:.5%}")
        print(f"卖出费率：{cfg_.sell_fee_rate:.5%}")
        print(f"业绩基准：{cfg_.benchmark}")
        print("=" * 70)

        try:
            # 构建 Operator
            operator = qt.Operator(
                strategies=[strategy.build()],
                signal_type=cfg_.signal_type,
                run_freq=cfg_.run_freq,
                run_timing=cfg_.run_timing,
            )

            print("\n运行回测……")

            raw_result = qt.run(
                operator,
                mode=1,  # 历史回测
                asset_pool=cfg_.asset_pool,
                asset_type=cfg_.asset_type,
                benchmark_asset=cfg_.benchmark,
                invest_cash_amounts=[cfg_.cash],
                invest_start=cfg_.start,
                invest_end=end_date,
                cost_rate_buy=cfg_.buy_fee_rate,
                cost_rate_sell=cfg_.sell_fee_rate,
                trade_batch_size=cfg_.trade_batch_size,
                sell_batch_size=cfg_.trade_batch_size,
                report=cfg_.report,
                visual=cfg_.visual,
                trade_log=cfg_.trade_log,
            )

            print("\n回测执行完成。")
            return BacktestResult(
                raw_result=raw_result,
                config=cfg_,
                strategy_name=strategy_name,
                success=True,
            )

        except Exception as exc:
            print(f"\n回测失败：{type(exc).__name__}: {exc}")
            return BacktestResult(
                config=cfg_,
                strategy_name=strategy_name,
                success=False,
                error=str(exc),
            )

    @staticmethod
    def _determine_end_date(asset_pool: list[str]) -> str:
        """
        自动确定所有ETF共同拥有数据的最后日期。

        Parameters
        ----------
        asset_pool : list[str]
            ETF 代码列表。

        Returns
        -------
        str
            YYYYMMDD 格式的结束日期。
        """
        fund_data = qt.QT_DATA_SOURCE.read_table_data("fund_daily")
        fund_data = fund_data.reset_index()
        fund_data["trade_date"] = pd.to_datetime(fund_data["trade_date"])

        end_dates = []
        for symbol in asset_pool:
            symbol_data = fund_data[fund_data["ts_code"] == symbol]
            if not symbol_data.empty:
                end_dates.append(symbol_data["trade_date"].max())

        if not end_dates:
            raise RuntimeError("无法确定回测结束日期：资产池中无数据。")

        common_end = min(end_dates)
        return common_end.strftime("%Y%m%d")

    @staticmethod
    def compare(engines: list[BacktestEngine]) -> list[BacktestResult]:
        """
        运行多个引擎并返回所有结果，便于对比。

        Parameters
        ----------
        engines : list[BacktestEngine]
            配置好的回测引擎列表。

        Returns
        -------
        list[BacktestResult]
            所有回测结果。
        """
        results = []
        for engine in engines:
            print(f"\n{'#'*70}")
            print(f"# 运行策略：{engine.config.strategy.name}")
            print(f"{'#'*70}")
            result = engine.run()
            results.append(result)
        return results
