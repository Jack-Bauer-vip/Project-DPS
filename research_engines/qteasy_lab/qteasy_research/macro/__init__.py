"""
macro — 宏观因子驱动资产配置研究模块。

基于改进美林时钟 × 多因子打分卡方法论：
  最新因子值 → 状态转化 → 各因子为资产打分 → 加权合成总概率 → 配置建议

本地 CSV 数据库为主存储，AKShare 联网仅用于数据更新。

使用方式
--------
>>> from qteasy_research.macro.factors import MacroDataFetcher
>>> from qteasy_research.macro.states import FactorStateClassifier
>>> from qteasy_research.macro.scoring import MacroScoringSystem
>>> from qteasy_research.macro.signal import generate_signal

>>> # 快速输出评分
>>> report = generate_signal()
"""

from __future__ import annotations

from qteasy_research.macro import config
from qteasy_research.macro.factors import MacroDataFetcher
from qteasy_research.macro.states import FactorStateClassifier
from qteasy_research.macro.scoring import MacroScoringSystem
from qteasy_research.macro.signal import generate_signal, print_report
from qteasy_research.macro.dashboard import MacroDashboard

__all__ = [
    "MacroDataFetcher",
    "FactorStateClassifier",
    "MacroScoringSystem",
    "MacroDashboard",
    "generate_signal",
    "print_report",
]
