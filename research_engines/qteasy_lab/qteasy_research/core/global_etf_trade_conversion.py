"""研究资产↔交易资产评分的换算（交易口径）。

研究资产 SPY/TLT/GLD 以 USD 计价；用户实际交易的是 A 股 ETF/股票（如 513500.SH、
518880.SH，以 CNY 计价）或美股直投（self_mapping）。本模块把研究口径的评分
换算到交易口径，供桌面端与报告独立展示，不改动研究口径的 final_score。

换算只对收益端做折减，不重算波动率（引擎未输出原始收益序列）：

- 管理费：相对费用差异。只扣交易资产相对研究资产多出的年化管理费，
  避免重复计算研究资产自身成本（SPY 0.09% / TLT 0.15% / GLD 0.40%）。
- 交易成本：一次性成本（trading_cost_bps / 10000），直接折减。
- 汇率：对 A 股 QDII，净值已含汇率、收益已是人民币口径，不额外调整收益；
  静态汇率无时间变动信息，不假设汇率变动（保守，不把缺失当作中性），
  仅生成 fx_note 展示汇率水平与敞口提示。
- 跟踪误差、折溢价：仅透传展示，不进入评分计算。

换算公式：
    trade_final_score = research_final_score × (1 − fee_adjustment − cost_adjustment)
    fee_adjustment    = max(0, trade_fee% − research_fee%) / 100 × sample_years
    cost_adjustment   = trading_cost_bps / 10000

本模块不自动生成交易指令、不自动修改组合权重。
"""

from __future__ import annotations

from typing import Any

# 研究资产年化管理费（%），公开基金事实（SPDR / iShares 官方费率）。
RESEARCH_ASSET_MANAGEMENT_FEE: dict[str, float] = {
    "SPY": 0.09,
    "TLT": 0.15,
    "GLD": 0.40,
}

# 费用+成本合计折减率的上限，防止异常配置过度下调评分。
MAX_ADJUSTMENT_RATE = 0.5


def _sample_years(sample_count: int, frequency_used: str) -> float:
    """把评分样本数折算为年数。

    base_score 用最近 12 个月（monthly）或 252 个交易日（daily），约 1 年。
    数据不足时按实际样本比例折算；越界值收敛到 [0, 1]。
    """
    if not sample_count or sample_count <= 0:
        return 0.0
    per_year = 252 if frequency_used == "daily" else 12
    return min(1.0, max(0.0, sample_count / per_year))


def conversion_adjustments(
    research_asset: str,
    mapping: dict[str, Any],
    *,
    sample_count: int,
    frequency_used: str,
) -> dict[str, Any]:
    """计算交易口径的调整项：费用差异、交易成本与汇率说明。

    参数：
        research_asset：研究资产代码（SPY/TLT/GLD），用于取基准管理费。
        mapping：生效的交易资产映射记录（含 management_fee、trading_cost_bps、
            currency、fx_rule、exchange_rate 等字段）。
        sample_count：研究评分样本数（决定费用折算的年数）。
        frequency_used：'daily' 或 'monthly'，决定每年样本数。

    返回：
        {fee_adjustment, cost_adjustment, fx_note, warnings}。
        fee_adjustment / cost_adjustment 为收益折减比率（如 0.0051 表示 0.51%）。
    """
    research_fee = float(RESEARCH_ASSET_MANAGEMENT_FEE.get(research_asset, 0.0))
    trade_fee = float(mapping.get("management_fee") or 0.0)
    years = _sample_years(int(sample_count or 0), frequency_used)
    # 只扣交易资产多出的费用；交易资产费用更低时不反向加分。
    fee_diff = max(0.0, trade_fee - research_fee)
    fee_adjustment = fee_diff / 100.0 * years
    cost_adjustment = float(mapping.get("trading_cost_bps") or 0.0) / 10000.0

    warnings: list[str] = []
    total = fee_adjustment + cost_adjustment
    if total > MAX_ADJUSTMENT_RATE:
        # 按比例压缩到上限，保持费用与成本的相对关系。
        scale = MAX_ADJUSTMENT_RATE / total
        fee_adjustment *= scale
        cost_adjustment *= scale
        warnings.append(
            f"费用与成本合计折减率超过 {MAX_ADJUSTMENT_RATE:.0%}，已按比例压缩到上限。"
        )

    currency = str(mapping.get("currency") or "CNY")
    fx_rule = str(mapping.get("fx_rule") or "static")
    exchange_rate = mapping.get("exchange_rate")
    if currency == "USD":
        fx_note = "以 USD 计价，无汇率差异。"
    else:
        rate_txt = f"{float(exchange_rate):.4f}" if exchange_rate else "未设置"
        if fx_rule == "static":
            fx_note = (
                f"以 CNY 计价，静态汇率 1 USD ≈ {rate_txt} CNY；"
                "QDII 净值已含汇率，收益不再额外调整。"
            )
        else:
            fx_note = (
                f"以 CNY 计价，汇率方式 {fx_rule}（未对冲），"
                "收益受汇率波动影响，未建模。"
            )

    return {
        "fee_adjustment": fee_adjustment,
        "cost_adjustment": cost_adjustment,
        "fx_note": fx_note,
        "warnings": warnings,
    }


def convert_research_score_to_trade(
    score_row: dict[str, Any],
    mapping: dict[str, Any] | None,
) -> dict[str, Any]:
    """把一行研究评分换算为交易口径的换算行。

    参数：
        score_row：GlobalEtfEngine.calculate_scores() 输出 scores 中的一行
            （含 asset、base_score、macro_modifier、final_score、sample_count、
            frequency_used 等字段）。
        mapping：effective_trade_mapping() 取到的生效交易资产映射；None 表示
            未配置映射（派生状态，返回 NO_MAPPING，不报错）。

    返回：
        换算行 dict，包含透传的映射字段、研究口径 final_score、分项调整与
        trade_final_score。status 取值：
        - NO_MAPPING：未配置生效映射；
        - NO_RESEARCH_SCORE：研究口径 final_score 不可用（如 PARTIAL 常态）；
        - CONVERTED：换算成功。
    """
    asset = str(score_row.get("asset") or score_row.get("research_asset") or "")
    research_final = score_row.get("final_score")

    if mapping is None:
        return {
            "asset": asset,
            "status": "NO_MAPPING",
            "research_final_score": research_final,
            "trade_final_score": None,
            "fee_adjustment": 0.0,
            "cost_adjustment": 0.0,
            "fx_note": "",
            "warnings": ["未配置生效交易资产映射，无法换算交易口径。"],
        }

    base: dict[str, Any] = {
        "asset": asset,
        # 透传映射字段
        "trade_asset_code": mapping.get("trade_asset_code", ""),
        "trade_asset_name": mapping.get("trade_asset_name", ""),
        "trade_market": mapping.get("trade_market", ""),
        "currency": mapping.get("currency", "CNY"),
        "fx_pair": mapping.get("fx_pair", ""),
        "fx_rule": mapping.get("fx_rule", "static"),
        "exchange_rate": mapping.get("exchange_rate"),
        "management_fee": mapping.get("management_fee", 0.0),
        "trading_cost_bps": mapping.get("trading_cost_bps", 0.0),
        "tracking_error": mapping.get("tracking_error"),
        "premium_discount": mapping.get("premium_discount"),
        # 研究口径
        "research_final_score": research_final,
    }

    if research_final is None:
        base.update({
            "status": "NO_RESEARCH_SCORE",
            "trade_final_score": None,
            "fee_adjustment": 0.0,
            "cost_adjustment": 0.0,
            "fx_note": "",
            "warnings": ["研究口径 final_score 不可用（如 PARTIAL），交易口径评分无法换算。"],
        })
        return base

    sample_count = int(score_row.get("sample_count") or 0)
    frequency_used = str(score_row.get("frequency_used") or ("daily" if asset == "TLT" else "monthly"))
    adjustment = conversion_adjustments(
        asset,
        mapping,
        sample_count=sample_count,
        frequency_used=frequency_used,
    )
    trade_score = float(research_final) * (
        1.0 - adjustment["fee_adjustment"] - adjustment["cost_adjustment"]
    )
    base.update({
        "fee_adjustment": adjustment["fee_adjustment"],
        "cost_adjustment": adjustment["cost_adjustment"],
        "fx_note": adjustment["fx_note"],
        "trade_final_score": trade_score,
        "status": "CONVERTED",
        "warnings": adjustment["warnings"],
    })
    return base
