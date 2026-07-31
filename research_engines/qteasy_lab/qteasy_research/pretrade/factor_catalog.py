"""首期因子目录和主题合成规则。

目录只定义候选研究对象，不代表因子已经有效。只有通过样本内、样本外、
成本后和衰退监控的因子，才允许进入资产匹配评分。
"""

from __future__ import annotations

from typing import Any

from qteasy_research.pretrade.schemas import FactorDefinition


MACRO_THEMES: dict[str, dict[str, Any]] = {
    "growth": {"name": "增长", "indicators": ["cn_gdp", "cn_pmi", "industrial_activity"]},
    "inflation": {"name": "通胀", "indicators": ["cn_cpi", "cn_ppi", "commodity_price"]},
    "rate_real_rate": {"name": "利率/实际利率", "indicators": ["policy_rate", "real_rate", "cn10y"]},
    "credit_liquidity": {"name": "信用/流动性", "indicators": ["m2", "social_financing", "credit_spread"]},
    "term_spread": {"name": "期限利差", "indicators": ["cn10y2y", "us10y2y"]},
    "fx": {"name": "汇率", "indicators": ["cny_usd", "dxy", "reer"]},
}


ASSET_FACTOR_CATALOG: tuple[FactorDefinition, ...] = (
    FactorDefinition("value", "价值", "asset", "估值相对便宜的资产长期风险溢价可能更高。", "PE/PB/股息率的横截面标准化", horizons=["medium", "long"], expected_direction="positive"),
    FactorDefinition("quality", "质量", "asset", "盈利质量、资本回报和资产负债表稳健性可能改善长期表现。", "ROE/毛利率/负债率等标准化合成", horizons=["medium", "long"], expected_direction="positive"),
    FactorDefinition("growth", "成长", "asset", "盈利和收入增长可能支持中长期超额收益，但估值过高会反转。", "营收/利润增长率及其变化", horizons=["medium", "long"], expected_direction="positive"),
    FactorDefinition("low_volatility", "低波动", "asset", "低波动资产可能提供更稳定的风险调整收益。", "滚动波动率倒数或横截面排名", horizons=["medium", "long"], expected_direction="positive"),
    FactorDefinition("dividend", "股息", "asset", "稳定现金分配可能提供防御性收益来源。", "股息率与股息稳定性", horizons=["medium", "long"], expected_direction="positive"),
    FactorDefinition("momentum", "动量", "asset", "近期相对强势可能在一定期限内延续。", "20/60/126/252日收益及横截面排名", horizons=["short", "medium"], expected_direction="positive"),
    FactorDefinition("trend", "趋势", "asset", "价格位于中长期均线之上时趋势状态通常更稳定。", "MA60/120/250关系", horizons=["short", "medium"], expected_direction="positive"),
    FactorDefinition("drawdown", "回撤", "asset", "回撤深度和修复速度反映风险承受与恢复能力。", "滚动峰值回撤与修复时间", horizons=["medium", "long"], expected_direction="negative"),
    FactorDefinition("liquidity", "流动性", "asset", "成交额、价差和冲击成本影响因子策略可执行性。", "ADV/成交额趋势/冲击成本", horizons=["short", "medium"], expected_direction="positive"),
    FactorDefinition("macd", "MACD辅助", "technical", "趋势动能的辅助确认，不单独作为买卖信号。", "DIF-DEA及其柱值", horizons=["short"], expected_direction="positive"),
    FactorDefinition("rsi", "RSI辅助", "technical", "相对强弱状态的辅助确认，不单独作为买卖信号。", "14日相对强弱指标", horizons=["short"], expected_direction="positive"),
    FactorDefinition("atr", "ATR辅助", "technical", "波动水平用于风险和成本调整。", "ATR14/价格", horizons=["short", "medium"], expected_direction="negative"),
    FactorDefinition("bollinger", "布林带辅助", "technical", "价格相对波动区间的位置用于状态确认。", "(价格-下轨)/(上轨-下轨)", horizons=["short"], expected_direction="positive"),
)


def list_factor_catalog(*, category: str | None = None, horizon: str | None = None) -> list[FactorDefinition]:
    result = []
    for definition in ASSET_FACTOR_CATALOG:
        if category and definition.category != category:
            continue
        if horizon and horizon not in definition.horizons:
            continue
        result.append(definition)
    return result


def build_theme_weights(
    factor_ids: list[str],
    factor_theme_map: dict[str, str],
    *,
    individual_cap: float = 0.25,
) -> dict[str, float]:
    """主题等权、主题内等权，并限制单指标权重上限。"""

    themes: dict[str, list[str]] = {}
    for factor_id in factor_ids:
        themes.setdefault(factor_theme_map.get(factor_id, factor_id), []).append(factor_id)
    if not themes:
        return {}
    theme_weight = 1.0 / len(themes)
    weights = {factor_id: theme_weight / len(items) for theme, items in themes.items() for factor_id in items}
    return {factor_id: min(weight, individual_cap) for factor_id, weight in weights.items()}


__all__ = ["MACRO_THEMES", "ASSET_FACTOR_CATALOG", "list_factor_catalog", "build_theme_weights"]
