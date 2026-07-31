"""
qteasy_research — 基于 qteasy 的 ETF 量化研究与回测工具包
========================================================

个人投资者全球资产配置、ETF研究、量化分析、策略回测。

依赖于 qteasy 2.6.0+ 提供回测引擎、数据源和策略执行环境。

使用方式
-------
>>> from qteasy_research.strategies.equal_weight import EqualWeightStrategy
>>> from qteasy_research.backtesting.engine import BacktestEngine
>>> engine = BacktestEngine(strategy=EqualWeightStrategy(), asset_pool=[...])
>>> result = engine.run()

环境变量
--------
TUSHARE_TOKEN  : Tushare 接口令牌
TUSHARE_API_URL: 自定义 Tushare 兼容接口地址（可选）
"""

from __future__ import annotations

__version__ = "0.1.0"
__author__ = "Bern"

from qteasy_research import config
from qteasy_research import strategies
from qteasy_research import backtesting
from qteasy_research import data
from qteasy_research import macro
