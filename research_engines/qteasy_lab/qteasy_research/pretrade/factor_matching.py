"""宏观环境、资产状态和因子暴露的研究匹配。"""

from __future__ import annotations

from typing import Any

import numpy as np

from qteasy_research.pretrade.schemas import AssetFactorExposure, FactorMatchResult


def _number(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if np.isfinite(value) else default


def match_asset_to_factors(
    asset_code: str,
    exposures: list[AssetFactorExposure | dict[str, Any]],
    current_states: dict[str, float],
    *,
    horizon: str = "medium",
    regime: str = "unknown",
    factor_weights: dict[str, float] | None = None,
    decay_warnings: list[str] | None = None,
    missing_items: list[str] | None = None,
) -> FactorMatchResult:
    """按 0—100 匹配分筛选，不把分数转成交易指令。"""

    weights = factor_weights or {}
    contributions: list[dict[str, Any]] = []
    for raw in exposures:
        item = raw.__dict__ if isinstance(raw, AssetFactorExposure) else dict(raw)
        factor_id = str(item.get("factor_id") or "")
        state = current_states.get(factor_id)
        if state is None:
            continue
        direction = str(item.get("exposure_direction") or "unknown")
        sign = 1.0 if direction == "positive" else -1.0 if direction == "negative" else 0.0
        quality = _number(item.get("data_confidence"), 0.55)
        stability = _number(item.get("stability_score"), 0.5)
        strength = abs(_number(item.get("exposure_strength"), 0.0))
        weight = _number(weights.get(factor_id), 1.0)
        contribution = weight * _number(state) * sign * strength * stability * quality
        contributions.append({**item, "current_state": _number(state), "weight": weight, "contribution": contribution})
    total = sum(_number(item["contribution"]) for item in contributions)
    score = float(np.clip(50 + 50 * total, 0, 100)) if contributions else None
    supporting = [item for item in contributions if item["contribution"] > 0]
    conflicting = [item for item in contributions if item["contribution"] < 0]
    if score is None:
        state = "unavailable"
    elif score >= 65:
        state = "supportive"
    elif score <= 35:
        state = "conflicting"
    else:
        state = "mixed"
    return FactorMatchResult(
        asset_code=asset_code,
        horizon=horizon,
        score=score,
        state=state,
        regime=regime,
        supporting_factors=supporting,
        conflicting_factors=conflicting,
        missing_items=list(missing_items or []),
        decay_warnings=list(decay_warnings or []),
        details={"formula": "clip(50 + 50 × Σ(weight × state × direction × strength × stability × quality), 0, 100)", "contributions": contributions},
    )
