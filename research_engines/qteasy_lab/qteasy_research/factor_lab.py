"""因子实验室公共 API。

该 API 可以直接接收研究数据做离线实验，也可以把结果保存到 ResearchStore，
为桌面端后续增加“因子有效性/资产匹配”页面提供稳定入口。
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from qteasy_research.pretrade.factor_matching import match_asset_to_factors
from qteasy_research.pretrade.factor_scoring import calculate_factor_scores, monitor_factor_long_short
from qteasy_research.pretrade.factor_research import (
    FACTOR_RESEARCH_FORMULA_VERSION,
    diagnose_factor_collinearity,
    estimate_asset_factor_exposure,
    evaluate_factor_effectiveness,
    grade_data_quality,
    monitor_factor_decay,
)
from qteasy_research.pretrade.schemas import (
    AssetFactorExposure,
    DataQualityMetadata,
    FactorResearchResult,
    TransactionCostConfig,
)


def import_factor_research_result(
    factor_id: str,
    research_summary: dict[str, Any],
    *,
    store_root: str | Path,
    approve: bool = False,
) -> dict[str, Any]:
    """Import an offline factor study summary and optionally approve it.

    The production scorer continues to use the configured Parquet values. This
    function only updates the definition's auditable research summary and
    lifecycle status.
    """

    if not isinstance(research_summary, dict):
        raise TypeError("research_summary 必须是 JSON 对象")
    from qteasy_research.pretrade.storage import ResearchStore

    store = ResearchStore(store_root)
    definition = next(
        (item for item in store.list_factor_definitions() if item["factor_id"] == factor_id),
        None,
    )
    if definition is None:
        raise ValueError(f"未找到因子定义：{factor_id}")
    definition["research_summary"] = dict(research_summary)
    definition["status"] = "APPROVED" if approve else "CANDIDATE"
    return store.upsert_factor_definition(definition)


def research_factor(
    factor_id: str,
    *,
    factor: pd.Series | pd.DataFrame | None = None,
    forward_returns: pd.Series | pd.DataFrame | None = None,
    assets: list[str] | None = None,
    horizon: str = "medium",
    forward_period: int = 1,
    cost_config: TransactionCostConfig | dict[str, Any] | None = None,
    asset_type: str = "ETF",
    average_daily_value: float | None = None,
    data_quality: DataQualityMetadata | dict[str, Any] | None = None,
    formula_version: str = FACTOR_RESEARCH_FORMULA_VERSION,
    min_samples: int = 24,
    store_root: str | Path | None = None,
    as_of_date: str | None = None,
) -> FactorResearchResult:
    """运行单因子研究；未传入数据时返回明确的部分结果。"""

    if factor is None or forward_returns is None:
        result = FactorResearchResult(
            factor_id=factor_id, horizon=horizon, formula_version=formula_version,
            quality_level="D", status="insufficient_data",
            warnings=["未提供因子观测和未来收益，当前只建立研究任务，不输出有效性结论"],
        )
    else:
        result = evaluate_factor_effectiveness(
            factor, forward_returns, factor_id=factor_id, horizon=horizon,
            forward_period=forward_period, cost_config=cost_config,
            asset_type=asset_type, average_daily_value=average_daily_value,
            data_quality=data_quality, formula_version=formula_version,
            min_samples=min_samples,
        )
    if store_root is not None:
        from qteasy_research.pretrade.storage import ResearchStore

        ResearchStore(store_root).save_factor_research(
            factor_id=factor_id, horizon=horizon, as_of=as_of_date,
            formula_version=formula_version, payload=asdict(result),
        )
    return result


def analyze_factor_collinearity(factor_frame: pd.DataFrame, **kwargs: Any) -> dict[str, Any]:
    return diagnose_factor_collinearity(factor_frame, **kwargs)


def analyze_factor_decay(ic_history: pd.Series, **kwargs: Any) -> dict[str, Any]:
    return monitor_factor_decay(ic_history, **kwargs)


def estimate_exposure(
    asset_returns: pd.Series,
    factor_values: pd.Series,
    *,
    asset_code: str,
    factor_id: str,
    horizon: str = "medium",
    data_quality: DataQualityMetadata | dict[str, Any] | None = None,
    store_root: str | Path | None = None,
    as_of_date: str | None = None,
    min_samples: int = 24,
) -> AssetFactorExposure:
    result = estimate_asset_factor_exposure(
        asset_returns, factor_values, asset_code=asset_code,
        factor_id=factor_id, horizon=horizon, data_quality=data_quality,
        min_samples=min_samples,
    )
    if store_root is not None:
        from qteasy_research.pretrade.storage import ResearchStore

        ResearchStore(store_root).save_factor_exposure(
            asset_code=asset_code, factor_id=factor_id, horizon=horizon,
            as_of=as_of_date, formula_version=FACTOR_RESEARCH_FORMULA_VERSION,
            payload=asdict(result),
        )
    return result


def match_factors(
    asset_code: str,
    exposures: list[AssetFactorExposure | dict[str, Any]],
    current_states: dict[str, float],
    *,
    horizon: str = "medium",
    regime: str = "unknown",
    factor_weights: dict[str, float] | None = None,
    decay_warnings: list[str] | None = None,
    missing_items: list[str] | None = None,
    store_root: str | Path | None = None,
    as_of_date: str | None = None,
) -> Any:
    result = match_asset_to_factors(
        asset_code, exposures, current_states, horizon=horizon, regime=regime,
        factor_weights=factor_weights, decay_warnings=decay_warnings,
        missing_items=missing_items,
    )
    if store_root is not None:
        from qteasy_research.pretrade.storage import ResearchStore

        ResearchStore(store_root).save_factor_match(
            asset_code=asset_code, horizon=horizon, as_of=as_of_date,
            formula_version=FACTOR_RESEARCH_FORMULA_VERSION, payload=asdict(result),
        )
    return result


__all__ = [
    "research_factor", "estimate_exposure", "match_factors",
    "analyze_factor_collinearity", "analyze_factor_decay", "grade_data_quality",
    "calculate_factor_scores", "monitor_factor_long_short", "import_factor_research_result",
]
