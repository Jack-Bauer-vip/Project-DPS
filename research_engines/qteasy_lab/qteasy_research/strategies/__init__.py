"""策略库 — 预定义的 ETF 投资策略。"""

from qteasy_research.strategies.equal_weight import EqualWeightStrategy
from qteasy_research.strategies.risk_parity import RiskParityStrategy
from qteasy_research.strategies.inverse_vol import InverseVolatilityStrategy
from qteasy_research.strategies.momentum import MomentumStrategy

__all__ = [
    "EqualWeightStrategy",
    "RiskParityStrategy",
    "InverseVolatilityStrategy",
    "MomentumStrategy",
]
