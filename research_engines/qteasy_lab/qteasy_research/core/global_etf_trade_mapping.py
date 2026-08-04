"""研究资产↔交易资产映射的消费侧辅助。

映射本身是用户配置（存于 global_etf_trade_mapping 表），本模块提供：
- effective_trade_mapping：取某研究资产当前生效（ACTIVE + priority 最小）的交易资产映射；
- initialize_default_global_etf_trade_mappings：可选的 self_mapping 初始化器（仅显式调用）。

本模块不改变 GlobalEtfEngine 评分逻辑，也不自动生成交易指令。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from qteasy_research.pretrade.storage import ResearchStore


def effective_trade_mapping(
    store: ResearchStore,
    research_asset_code: str,
) -> dict[str, Any] | None:
    """返回某研究资产当前生效（status=ACTIVE、priority 最小）的交易资产映射。

    未配置映射时返回 None（派生状态：未配置 = 无法换算交易口径）。
    若 definition 已不存在，记录为悬空引用并返回 None。
    """
    definitions = {item["asset_code"] for item in store.list_global_etf_definitions()}
    if research_asset_code not in definitions:
        return None
    mappings = store.get_global_etf_trade_mappings(research_asset_code, status="ACTIVE")
    if not mappings:
        return None
    return min(mappings, key=lambda item: int(item.get("priority", 1)))


def initialize_default_global_etf_trade_mappings(
    store_root: str | Path,
    *,
    research_assets: tuple[str, ...] = ("SPY", "TLT", "GLD"),
    self_mapping: bool = False,
) -> list[dict[str, Any]]:
    """（可选）为无映射的研究资产注册 self_mapping（美股账户直接交易）。

    self_mapping=False（默认）时不写任何行，保持未配置派生状态。
    仅当用户显式开启 self_mapping=True 时，才为每个研究资产写入
    research_asset_code == trade_asset_code、currency='USD'、fx_rule='static'、
    exchange_rate=1.0 的真实映射。人民币 QDII/场外基金必须由用户自己配置。
    绝不自动执行。
    """
    if not self_mapping:
        return []
    store = ResearchStore(store_root)
    result = []
    for asset in research_assets:
        existing = store.get_global_etf_trade_mappings(asset)
        if existing:
            continue
        result.append(store.upsert_global_etf_trade_mapping({
            "research_asset_code": asset,
            "trade_asset_code": asset,
            "trade_asset_name": f"{asset} 美股直投",
            "trade_market": "US",
            "currency": "USD",
            "fx_pair": "USD/USD",
            "fx_rule": "static",
            "exchange_rate": 1.0,
            "priority": 1,
            "status": "ACTIVE",
        }))
    return result
