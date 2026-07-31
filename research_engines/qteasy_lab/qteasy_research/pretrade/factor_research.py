"""可审计的因子研究内核。

本模块只做确定性统计，不生成交易指令。所有不足样本或无法计算的结果
使用 ``None`` 和 ``warnings`` 表达，不用 0 伪装成中性结论。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import numpy as np
import pandas as pd

from qteasy_research.pretrade.schemas import (
    AssetFactorExposure,
    DataQualityMetadata,
    FactorResearchResult,
    TransactionCostConfig,
)


FACTOR_RESEARCH_FORMULA_VERSION = "factor-research-v1"


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _quality_dict(quality: DataQualityMetadata | dict[str, Any] | None) -> dict[str, Any]:
    if quality is None:
        return {}
    return asdict(quality) if isinstance(quality, DataQualityMetadata) else dict(quality)


def grade_data_quality(
    *,
    source: str = "",
    completeness: float | None = None,
    release_date: str | None = None,
    available_at: str | None = None,
    revision_id: str | None = None,
    vintage_id: str | None = None,
    quality_level: str | None = None,
    sample_count: int | None = None,
) -> dict[str, Any]:
    """给字段/因子数据分级。

    A/B/C/D 是研究可用性等级，不是供应商评价；缺少发布时间或历史版本
    时即使数值完整，也不能当作 A 级 point-in-time 数据。
    """

    warnings: list[str] = []
    completeness_value = _finite(completeness)
    if completeness_value is not None and completeness_value < 0.95:
        warnings.append("字段存在缺失")
    if not release_date and not available_at:
        warnings.append("缺少发布时间/可获得时间，无法严格回放")
    if not vintage_id:
        warnings.append("缺少历史版本标识，可能存在修订偏差")
    if sample_count is not None and sample_count < 60:
        warnings.append("样本少于60，长期有效性结论不可靠")
    if quality_level in {"A", "B", "C", "D"}:
        level = quality_level
    elif release_date or available_at:
        level = "A" if completeness_value is not None and completeness_value >= 0.99 and vintage_id else "B"
        if completeness_value is not None and completeness_value < 0.95:
            level = "C"
    else:
        level = "C" if source else "D"
    if warnings and level == "A":
        level = "B" if "缺少历史版本标识" not in warnings else "C"
    score_map = {"A": 1.0, "B": 0.8, "C": 0.55, "D": 0.25}
    return {
        "source": source,
        "quality_level": level,
        "quality_score": score_map[level],
        "completeness": completeness_value,
        "release_date": release_date,
        "available_at": available_at or release_date,
        "revision_id": revision_id,
        "vintage_id": vintage_id,
        "sample_count": sample_count,
        "warnings": warnings,
    }


def estimate_transaction_cost(
    order_value: float,
    average_daily_value: float | None,
    *,
    asset_type: str = "ETF",
    config: TransactionCostConfig | dict[str, Any] | None = None,
) -> dict[str, float | None]:
    """估算单次换手成本率和金额。成本模型为研究假设，需在报告中披露。"""

    cfg = config if isinstance(config, TransactionCostConfig) else TransactionCostConfig(**(config or {}))
    value = max(float(order_value or 0), 0.0)
    if value <= 0:
        return {"cost_rate": 0.0, "cost_amount": 0.0, "impact_rate": 0.0, "liquidity_confidence": 0.0}
    commission = cfg.commission_rate
    stamp = cfg.stamp_tax_rate if asset_type.upper() in {"STOCK", "A股", "EQUITY"} or cfg.etf_stamp_tax else 0.0
    spread = max(cfg.half_spread_rate, 0.0)
    adv = _finite(average_daily_value)
    if adv is None or adv <= 0:
        impact = None
        liquidity_confidence = 0.2
    else:
        impact = max(cfg.impact_coefficient, 0.0) * np.sqrt(value / adv)
        liquidity_confidence = float(np.clip(1 - value / (adv * 5), 0.0, 1.0))
    base = commission + stamp + spread
    # 没有 ADV 时仍保留可确定的佣金/税费/价差，只有冲击成本缺失；
    # 这样成本后收益不会被错误地整体标记为不可用。
    rate = float(base + impact) if impact is not None else float(base)
    return {
        "cost_rate": rate,
        "cost_amount": None if rate is None else float(value * rate),
        "commission_rate": float(commission),
        "stamp_tax_rate": float(stamp),
        "half_spread_rate": float(spread),
        "impact_rate": None if impact is None else float(impact),
        "liquidity_confidence": liquidity_confidence,
    }


def _max_drawdown(returns: pd.Series) -> float | None:
    if returns.empty:
        return None
    wealth = (1 + returns).cumprod()
    return _finite((wealth / wealth.cummax() - 1).min())


def _prepare_pair(factor: pd.Series, forward_return: pd.Series) -> pd.DataFrame:
    left = pd.to_numeric(factor, errors="coerce").rename("factor")
    right = pd.to_numeric(forward_return, errors="coerce").rename("forward_return")
    frame = pd.concat([left, right], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    return frame


def _series_effectiveness(frame: pd.DataFrame, quantiles: int) -> tuple[float | None, float | None, dict[str, Any]]:
    if len(frame) < 3:
        return None, None, {"reason": "有效配对样本不足"}
    ic = _finite(frame["factor"].corr(frame["forward_return"]))
    rank_ic = _finite(frame["factor"].rank().corr(frame["forward_return"].rank()))
    try:
        groups = pd.qcut(frame["factor"], q=min(quantiles, frame["factor"].nunique()), labels=False, duplicates="drop")
        grouped = frame.assign(group=groups).groupby("group", observed=True)["forward_return"].mean()
        spread = _finite(grouped.iloc[-1] - grouped.iloc[0]) if len(grouped) >= 2 else None
    except (ValueError, IndexError):
        spread = None
    details = {"factor_mean": _finite(frame["factor"].mean()), "return_mean": _finite(frame["forward_return"].mean())}
    return ic, rank_ic, {"group_spread": spread, **details}


def evaluate_factor_effectiveness(
    factor: pd.Series | pd.DataFrame,
    forward_returns: pd.Series | pd.DataFrame,
    *,
    factor_id: str = "factor",
    horizon: str = "medium",
    forward_period: int = 1,
    quantiles: int = 5,
    cost_config: TransactionCostConfig | dict[str, Any] | None = None,
    asset_type: str = "ETF",
    average_daily_value: float | None = None,
    data_quality: DataQualityMetadata | dict[str, Any] | None = None,
    formula_version: str = FACTOR_RESEARCH_FORMULA_VERSION,
    min_samples: int = 24,
) -> FactorResearchResult:
    """计算单因子 IC/Rank IC/分组差/成本后收益。

    支持两种输入：
    - Series：时间序列因子与同一资产未来收益；
    - DataFrame：日期×资产的横截面因子与未来收益，每个日期计算横截面 IC。
    """

    warnings: list[str] = []
    if isinstance(factor, pd.DataFrame) or isinstance(forward_returns, pd.DataFrame):
        f = factor if isinstance(factor, pd.DataFrame) else pd.DataFrame({"asset": factor})
        r = forward_returns if isinstance(forward_returns, pd.DataFrame) else pd.DataFrame({"asset": forward_returns})
        f, r = f.align(r, join="inner", axis=0)
        if forward_period > 0:
            r = r.shift(-forward_period)
        ic_values: list[float] = []
        rank_values: list[float] = []
        spreads: list[float] = []
        observations: list[float] = []
        for date in f.index:
            pair = _prepare_pair(f.loc[date], r.loc[date])
            if len(pair) < 3:
                continue
            ic, rank_ic, details = _series_effectiveness(pair, quantiles)
            if ic is not None:
                ic_values.append(ic)
            if rank_ic is not None:
                rank_values.append(rank_ic)
            if details.get("group_spread") is not None:
                spreads.append(float(details["group_spread"]))
            observations.append(float(pair["forward_return"].mean()))
        sample_count = len(observations)
        ic = _finite(np.mean(ic_values)) if ic_values else None
        rank_ic = _finite(np.mean(rank_values)) if rank_values else None
        group_spread = _finite(np.mean(spreads)) if spreads else None
        icir = _finite(np.mean(ic_values) / np.std(ic_values, ddof=1)) if len(ic_values) > 1 and np.std(ic_values, ddof=1) else None
        gross = _finite(np.mean(observations)) if observations else None
        details = {"cross_sectional_ic_count": len(ic_values), "rank_ic_count": len(rank_values)}
    else:
        factor_series = pd.Series(factor).sort_index()
        returns_series = pd.Series(forward_returns).sort_index()
        if forward_period > 0:
            returns_series = returns_series.shift(-forward_period)
        frame = _prepare_pair(factor_series, returns_series)
        sample_count = len(frame)
        ic, rank_ic, details = _series_effectiveness(frame, quantiles)
        group_spread = _finite(details.get("group_spread"))
        icir = ic
        gross = _finite(frame["forward_return"].mean()) if not frame.empty else None
    if sample_count < min_samples:
        warnings.append(f"有效样本 {sample_count} 少于建议阈值 {min_samples}")
    quality = _quality_dict(data_quality)
    quality_level = quality.get("quality_level") or grade_data_quality(sample_count=sample_count).get("quality_level", "C")
    if quality.get("warnings"):
        warnings.extend(str(item) for item in quality["warnings"])
    cost = estimate_transaction_cost(1.0, average_daily_value, asset_type=asset_type, config=cost_config)
    cost_rate = _finite(cost.get("cost_rate"))
    net = None if gross is None or cost_rate is None else float(gross - cost_rate)
    status = "ok" if sample_count >= min_samples and ic is not None else "insufficient_data"
    if quality_level == "D":
        status = "low_quality"
    return FactorResearchResult(
        factor_id=factor_id,
        horizon=horizon,
        formula_version=formula_version,
        sample_count=sample_count,
        ic=ic,
        rank_ic=rank_ic,
        icir=icir,
        group_spread=group_spread,
        gross_return=gross,
        net_return=net,
        turnover=None,
        max_drawdown=_max_drawdown(pd.Series([])) if gross is None else None,
        quality_level=quality_level,
        status=status,
        warnings=warnings,
        details={"forward_period": forward_period, "quantiles": quantiles, **details, "cost_model": cost},
    )


def diagnose_factor_collinearity(
    factor_frame: pd.DataFrame,
    *,
    correlation_threshold: float = 0.80,
    vif_threshold: float = 10.0,
) -> dict[str, Any]:
    """相关矩阵、简单聚类和 VIF 诊断，不依赖 sklearn。"""

    frame = factor_frame.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna(axis=1, how="all")
    corr = frame.corr()
    columns = list(frame.columns)
    parent = {column: column for column in columns}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    high_corr_pairs: list[dict[str, Any]] = []
    for index, left in enumerate(columns):
        for right in columns[index + 1:]:
            value = _finite(corr.loc[left, right])
            if value is not None and abs(value) >= correlation_threshold:
                high_corr_pairs.append({"left": left, "right": right, "correlation": value})
                union(left, right)
    clusters: dict[str, list[str]] = {}
    for column in columns:
        clusters.setdefault(find(column), []).append(column)
    vifs: dict[str, float | None] = {}
    for column in columns:
        others = [item for item in columns if item != column]
        if not others:
            vifs[column] = 1.0
            continue
        data = frame[[column, *others]].dropna()
        if len(data) <= len(others) + 2:
            vifs[column] = None
            continue
        y = data[column].to_numpy(dtype=float)
        x = np.column_stack([np.ones(len(data)), data[others].to_numpy(dtype=float)])
        fitted = x @ np.linalg.pinv(x) @ y
        ss_total = float(np.sum((y - y.mean()) ** 2))
        r2 = 0.0 if ss_total == 0 else float(np.clip(1 - np.sum((y - fitted) ** 2) / ss_total, 0, 0.999999))
        vifs[column] = float(1 / (1 - r2))
    return {
        "correlation_matrix": corr.round(6).to_dict(),
        "high_correlation_pairs": high_corr_pairs,
        "clusters": list(clusters.values()),
        "vif": vifs,
        "requires_action": [column for column, value in vifs.items() if value is not None and value > vif_threshold],
        "rules": {"correlation_threshold": correlation_threshold, "vif_threshold": vif_threshold},
    }


def monitor_factor_decay(
    ic_history: pd.Series,
    *,
    rolling_window: int = 12,
    negative_streak: int = 6,
    oos_sharpe: float | None = None,
    in_sample_sharpe: float | None = None,
    data_quality_level: str = "A",
) -> dict[str, Any]:
    """监控滚动 IC、连续负 IC、样本外衰退和数据质量。"""

    values = pd.to_numeric(pd.Series(ic_history), errors="coerce").dropna()
    warnings: list[str] = []
    if len(values) >= rolling_window:
        rolling = values.iloc[-rolling_window:]
        mean = _finite(values.mean())
        std = _finite(values.std(ddof=1)) or 0.0
        if mean is not None and std > 0 and _finite(rolling.mean()) is not None and rolling.mean() < mean - 1.5 * std:
            warnings.append("滚动12期 IC 低于历史均值 1.5 个标准差")
    streak = 0
    for value in values.iloc[::-1]:
        if value < 0:
            streak += 1
        else:
            break
    if streak >= negative_streak:
        warnings.append(f"IC 已连续 {streak} 期为负")
    if oos_sharpe is not None and in_sample_sharpe not in (None, 0):
        if oos_sharpe < in_sample_sharpe * 0.5:
            warnings.append("样本外 Sharpe 低于样本内的 50%")
    if data_quality_level in {"C", "D"}:
        warnings.append(f"数据质量为 {data_quality_level}，降低因子置信度")
    status = "stable" if not warnings else "warning"
    if len(warnings) >= 2 or data_quality_level == "D":
        status = "degraded"
    return {"status": status, "warnings": warnings, "negative_streak": streak, "observations": len(values), "rolling_window": rolling_window}


def _aligned_exposure(factor: pd.Series, returns: pd.Series) -> pd.DataFrame:
    frame = pd.concat([pd.to_numeric(factor, errors="coerce").rename("factor"), pd.to_numeric(returns, errors="coerce").rename("return")], axis=1)
    return frame.replace([np.inf, -np.inf], np.nan).dropna()


def estimate_asset_factor_exposure(
    asset_returns: pd.Series,
    factor_values: pd.Series,
    *,
    asset_code: str,
    factor_id: str,
    horizon: str = "medium",
    data_quality: DataQualityMetadata | dict[str, Any] | None = None,
    min_samples: int = 24,
) -> AssetFactorExposure:
    """用 beta、状态条件收益差和分位数组差交叉估计资产暴露。"""

    frame = _aligned_exposure(factor_values, asset_returns)
    warnings: list[str] = []
    if len(frame) < min_samples:
        warnings.append(f"有效样本 {len(frame)} 少于建议阈值 {min_samples}")
    variance = float(frame["factor"].var(ddof=1)) if len(frame) > 1 else 0.0
    beta = _finite(frame["factor"].cov(frame["return"]) / variance) if variance > 0 else None
    median = frame["factor"].median() if not frame.empty else None
    conditional = None
    if median is not None:
        high = frame.loc[frame["factor"] >= median, "return"]
        low = frame.loc[frame["factor"] < median, "return"]
        if len(high) and len(low):
            conditional = _finite(high.mean() - low.mean())
    spread = None
    try:
        groups = pd.qcut(frame["factor"], q=min(5, frame["factor"].nunique()), labels=False, duplicates="drop")
        grouped = frame.assign(group=groups).groupby("group", observed=True)["return"].mean()
        if len(grouped) >= 2:
            spread = _finite(grouped.iloc[-1] - grouped.iloc[0])
    except (ValueError, IndexError):
        pass
    signs = [np.sign(value) for value in (beta, conditional, spread) if value is not None and value != 0]
    direction = "positive" if signs and sum(item > 0 for item in signs) >= sum(item < 0 for item in signs) else "negative" if signs else "unknown"
    strength_values = [abs(value) for value in (beta, conditional, spread) if value is not None]
    strength = _finite(np.mean(strength_values)) if strength_values else None
    quality = _quality_dict(data_quality)
    confidence = _finite(quality.get("quality_score")) or ({"A": 1.0, "B": 0.8, "C": 0.55, "D": 0.25}.get(quality.get("quality_level", "C"), 0.55))
    stability = _finite(1 - np.std(signs)) if len(signs) > 1 else (0.5 if signs else None)
    status = "ok" if len(frame) >= min_samples and strength is not None else "insufficient_data"
    return AssetFactorExposure(
        asset_code=asset_code, factor_id=factor_id, horizon=horizon,
        exposure_direction=direction, exposure_strength=strength,
        rolling_beta=beta, conditional_return_difference=conditional,
        rank_group_spread=spread, stability_score=stability,
        data_confidence=confidence, status=status,
        details={"sample_count": len(frame), "warnings": warnings},
    )
