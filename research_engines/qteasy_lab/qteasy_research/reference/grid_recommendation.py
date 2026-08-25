"""网格推荐组合（B2）：从 14 ETF 池选 4-10 标的组成 3 套方案 + 间距参考三元组。

补现有逐标尺建议（``grid_suggestion``）缺的组合级视角：
固定 9 格框架下按相关性 cap 生成 conservative / balanced / aggressive 三套
等权参考组合，并输出每标的间距参考三元组（default/min/max，``basis="atr20"``，
随日更口径复用 B1 ``grid_suggestion._spacing_reference``）。

安全边界：``approval_policy="REFERENCE_ONLY"``，永不自动 APPROVED、不改资产池 /
组合权重 / 回测配置；缺 ``account_total_capital`` 时 ``grid_budget``/``utilization``
置 None + warning（不虚构）。机器产出全 ASCII，零中文策略名
（``conservative/balanced/aggressive``）。

计算口径唯一源 = ``contract.shared_config.grid``（经 ``shared_grid`` 透传）；
缺省用 ``_DEFAULT_RECO_PARAMS``。复用：``grid_suggestion._close_panel`` /
``_grid_params`` / ``_compute_suitability`` / ``_suggested_spread`` /
``_spacing_reference``；``portfolio_analysis.correlation_matrix / portfolio_vol /
max_drawdown``；``backtest_engine.parse_contract``（上游）。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from qteasy_research.reference.config import (
    GRID_RECOMMENDATION_CADENCE,
    GRID_RECOMMENDATION_SCHEMA_VERSION,
)
from qteasy_research.reference.metadata import today_iso
from qteasy_research.reference import grid_suggestion as _gs
from qteasy_research.reference.grid_theoretical_profit import (
    compute_asset_theoretical_profit,
    compute_asset_theoretical_profit_actual,
    compute_portfolio_theoretical_profit,
)
from qteasy_research.reference.portfolio_analysis import (
    correlation_matrix,
    max_drawdown,
    portfolio_vol,
)

# 缺省网格推荐参数（contract.shared_config.grid 覆盖；缺失用代码默认值）。
_DEFAULT_RECO_PARAMS: dict[str, Any] = {
    "pool_scope": "asset_pool_active_etf",
    "levels_total": 9,
    # 3 套方案：标的数区间 + 池内 avg pair corr 上限。
    "plan_specs": [
        {"plan_id": "conservative", "target_min": 4, "target_max": 5, "corr_cap": 0.3},
        {"plan_id": "balanced", "target_min": 6, "target_max": 7, "corr_cap": 0.4},
        {"plan_id": "aggressive", "target_min": 8, "target_max": 10, "corr_cap": 0.5},
    ],
    # 间距参考三元组乘子/floor/cap（与 grid_suggestion._DEFAULT_GRID_PARAMS 一致，
    # shared_grid.grid 可覆盖）。
    "spacing_multiplier": 1.5,
    "spacing_floor": 0.025,
    "spacing_cap": 0.05,
    # 池级相关性窗口。
    "corr_window_days": 90,
    "corr_min_overlap": 60,
    # 适合度/中轴窗口（复用 grid_suggestion）。
    "window_days": 60,
    # 单份金额（决策点 8，可配置，默认 6000）。
    "default_amount_per_grid": 6000.0,
    # 理论收益（REFERENCE_ONLY 补充参考；amount_per_grid 见上，fee_rate 单边）。
    "fee_rate": 0.0005,
    "theory_max_w": 1.0,
}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _reco_params(shared_grid: dict | None) -> dict[str, Any]:
    """合并 ``contract.shared_config.grid`` 与代码默认值（文件值为准）。"""
    params = {k: (dict(v) if isinstance(v, dict) else v) for k, v in _DEFAULT_RECO_PARAMS.items()}
    if isinstance(shared_grid, dict):
        if isinstance(shared_grid.get("plan_specs"), list):
            params["plan_specs"] = shared_grid["plan_specs"]
        for key, value in shared_grid.items():
            if key == "plan_specs":
                continue
            if value is not None:
                params[key] = value
    return params


def _equal_weights(asset_ids: list[str]) -> dict[str, float]:
    if not asset_ids:
        return {}
    weight = 1.0 / len(asset_ids)
    return {asset_id: weight for asset_id in asset_ids}


def _pair_avg_corr(asset_ids: list[str], corr_matrix: dict[str, dict[str, Any]]) -> float | None:
    """plan 内两两相关均值（缺失对跳过，不虚构）。"""
    values: list[float] = []
    for i in range(len(asset_ids)):
        for j in range(i + 1, len(asset_ids)):
            asset_a, asset_b = asset_ids[i], asset_ids[j]
            try:
                value = corr_matrix[asset_a][asset_b]
            except (KeyError, TypeError):
                continue
            if value is None:
                continue
            if isinstance(value, float) and not np.isfinite(value):
                continue
            values.append(float(value))
    return float(np.mean(values)) if values else None


def _rank_assets(
    pool: list[str],
    suggestion_index: dict[str, dict[str, Any]],
) -> list[str]:
    """固定排序（确定性可回放）：suitability_score 降序，None 最后，同分按 asset_id 升序。"""
    def key(asset_id: str) -> tuple[Any, ...]:
        entry = suggestion_index.get(asset_id, {})
        score = entry.get("suitability_score")
        if score is None:
            return (1, 0.0, asset_id)
        return (0, -float(score), asset_id)

    return sorted(pool, key=key)


# ---------------------------------------------------------------------------
# 纯函数：池 / 相关性 / 方案选择 / 指标 / 资金 / 风险
# ---------------------------------------------------------------------------

def _pool_assets(
    strategy_assets: list[Any],
    params: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """池 = 全部启用策略的启用标的并集；enabled_grid = 启用网格策略的启用标的清单。

    ``pool_scope`` 标注来源（asset_pool_active_etf），标的清单以契约为准（B 只读）。
    """
    pool: list[str] = []
    enabled_grid: list[str] = []
    for strategy in strategy_assets:
        if not getattr(strategy, "enabled", True):
            continue
        enabled = [a.asset_id for a in strategy.assets if a.enabled]
        for asset_id in enabled:
            if asset_id not in pool:
                pool.append(asset_id)
        if strategy.decision_rule == "grid":
            for asset_id in enabled:
                if asset_id not in enabled_grid:
                    enabled_grid.append(asset_id)
    return sorted(pool), sorted(enabled_grid)


def _portfolio_correlation(
    asset_ids: list[str],
    market_data: dict[str, pd.DataFrame],
    params: dict[str, Any],
    data_asof: str | None = None,
) -> dict[str, Any]:
    """池级相关矩阵 + avg/max pair（90d 窗口，min_overlap=60）。"""
    window_days = int(params.get("corr_window_days", 90))
    min_overlap = int(params.get("corr_min_overlap", 60))
    panel = _gs._close_panel(market_data, asset_ids, window_days, data_asof)
    if panel.shape[1] < 2 or panel.shape[0] < 2:
        return {"matrix": {}, "pairs": [], "avg_corr": None, "max_pair_corr": None}
    corr = correlation_matrix(panel, min_overlap=min_overlap)
    assets = list(corr.columns)
    pairs: list[dict[str, Any]] = []
    for i in range(len(assets)):
        for j in range(i + 1, len(assets)):
            value = corr.iloc[i, j]
            if pd.isna(value):
                continue
            pairs.append({
                "asset_a": assets[i],
                "asset_b": assets[j],
                "corr": round(float(value), 4),
            })
    avg = float(np.mean([p["corr"] for p in pairs])) if pairs else None
    max_pair = float(np.max([abs(p["corr"]) for p in pairs])) if pairs else None
    matrix: dict[str, dict[str, Any]] = {}
    for asset_a in assets:
        matrix[asset_a] = {}
        for asset_b in assets:
            value = corr.loc[asset_a, asset_b]
            matrix[asset_a][asset_b] = round(float(value), 4) if not pd.isna(value) else None
    return {
        "matrix": matrix,
        "pairs": pairs,
        "avg_corr": round(avg, 4) if avg is not None else None,
        "max_pair_corr": round(max_pair, 4) if max_pair is not None else None,
    }


def _select_plan(
    asset_ids: list[str],
    corr_matrix: dict[str, dict[str, Any]],
    target_min: int,
    target_max: int,
    corr_cap: float,
    rank: list[str] | None = None,
) -> list[str]:
    """确定性贪心：按固定 rank 序逐个尝试，加入后 plan 内 avg pair corr < cap 才保留。

    到 ``target_max`` 或无可加停止（不保证达到 ``target_min``，相关过高时少选）。
    """
    candidate_set = set(asset_ids)
    candidates = [a for a in (rank if rank is not None else sorted(asset_ids)) if a in candidate_set]
    plan: list[str] = []
    for asset_id in candidates:
        if len(plan) >= target_max:
            break
        if asset_id in plan:
            continue
        trial = plan + [asset_id]
        avg = _pair_avg_corr(trial, corr_matrix)
        if avg is None or avg < corr_cap:
            plan = trial
    return plan


def _plan_corr_stats(
    asset_ids: list[str],
    corr_matrix: dict[str, dict[str, Any]],
    corr_cap: float,
) -> tuple[float | None, float | None, int]:
    """plan 内 avg / max_pair / 超过 corr_cap 的对数。"""
    values: list[float] = []
    for i in range(len(asset_ids)):
        for j in range(i + 1, len(asset_ids)):
            asset_a, asset_b = asset_ids[i], asset_ids[j]
            try:
                value = corr_matrix[asset_a][asset_b]
            except (KeyError, TypeError):
                continue
            if value is None:
                continue
            if isinstance(value, float) and not np.isfinite(value):
                continue
            values.append(float(value))
    if not values:
        return None, None, 0
    avg = float(np.mean(values))
    max_pair = float(np.max(np.abs(values)))
    high = int(sum(1 for v in values if abs(v) > corr_cap))
    return avg, max_pair, high


def spacing_reference_triplet(
    frame: pd.DataFrame,
    grid_row: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    """间距参考三元组（复用 B1 ``grid_suggestion._spacing_reference``）。"""
    return _gs._spacing_reference(frame, grid_row, params)


def portfolio_metrics(
    plan_asset_ids: list[str],
    market_data: dict[str, pd.DataFrame],
    corr_matrix: dict[str, dict[str, Any]],
    suitability_map: dict[str, float | None],
    corr_cap: float,
    params: dict[str, Any],
    data_asof: str | None = None,
) -> dict[str, Any]:
    """组合级指标（basis="equal_weight_reference"）。"""
    n = len(plan_asset_ids)
    avg_corr, max_pair_corr, high_corr_pairs = _plan_corr_stats(plan_asset_ids, corr_matrix, corr_cap)
    scores = [float(score) for score in suitability_map.values() if score is not None]
    avg_suit = float(np.mean(scores)) if scores else None
    weights = _equal_weights(plan_asset_ids)
    panel = _gs._close_panel(
        market_data, plan_asset_ids, int(params.get("corr_window_days", 90)), data_asof
    )
    annual_vol_est: float | None = None
    expected_max_drawdown: float | None = None
    if not panel.empty and n > 0:
        cov_daily = panel.pct_change().cov()
        if not cov_daily.empty:
            try:
                annual_vol_est = float(portfolio_vol(weights, cov_daily))
            except Exception:
                annual_vol_est = None
        try:
            mdd = max_drawdown(weights, panel)
            expected_max_drawdown = float(mdd) if mdd is not None else None
        except Exception:
            expected_max_drawdown = None
    return {
        "n_assets": n,
        "avg_corr": round(avg_corr, 4) if avg_corr is not None else None,
        "max_pair_corr": round(max_pair_corr, 4) if max_pair_corr is not None else None,
        "high_corr_pairs": int(high_corr_pairs),
        "avg_suitability": round(avg_suit, 2) if avg_suit is not None else None,
        "annual_vol_est": round(annual_vol_est, 4) if annual_vol_est is not None else None,
        "expected_max_drawdown": round(expected_max_drawdown, 4)
        if expected_max_drawdown is not None else None,
        "basis": "equal_weight_reference",
    }


def capital_metrics(
    plan_asset_ids: list[str],
    account_total_capital: float | None,
    strategy_weights: dict[str, float],
    params: dict[str, Any],
) -> dict[str, Any]:
    """资金指标：theoretical_max_inventory + per_strategy 预算占用。

    ``theoretical_max_inventory = amount_per_grid × levels_total × n_assets``
    （决策点 3：9 档全占，全区间可双向交易）。缺 account_total_capital →
    ``grid_budget``/``utilization`` 置 None（不虚构）。
    """
    n = len(plan_asset_ids)
    amount_per_grid = float(params.get("default_amount_per_grid", 6000.0))
    levels_total = int(params.get("levels_total", 9))
    theoretical_max_inventory = round(amount_per_grid * levels_total * n, 4)
    per_strategy: dict[str, Any] = {}
    for strategy_id, weight in strategy_weights.items():
        entry = {
            "target_capital_weight": round(float(weight), 6),
            "grid_budget": None,
            "utilization": None,
            "max_amount_per_grid_within_budget": None,
        }
        if account_total_capital is not None and account_total_capital > 0 and n > 0:
            grid_budget = round(account_total_capital * float(weight), 4)
            utilization = (
                round(theoretical_max_inventory / grid_budget, 4) if grid_budget > 0 else None
            )
            max_amount = round(grid_budget / (levels_total * n), 4) if n > 0 else None
            entry = {
                "target_capital_weight": round(float(weight), 6),
                "grid_budget": grid_budget,
                "utilization": utilization,
                "max_amount_per_grid_within_budget": max_amount,
            }
        per_strategy[strategy_id] = entry
    return {
        "theoretical_max_inventory": theoretical_max_inventory,
        "amount_per_grid": amount_per_grid,
        "levels_total": levels_total,
        "inventory_formula": "amount_per_grid * levels_total * n_assets",
        "per_strategy": per_strategy,
    }


def risk_annotation(strategy_assets: list[Any]) -> dict[str, Any]:
    """risk_budget 汇总 + note：B 不造 risk_budget_usage，标注由 A 风控管线估算。"""
    budgets: dict[str, float] = {}
    for strategy in strategy_assets:
        if not getattr(strategy, "enabled", True):
            continue
        risk_budget = getattr(strategy, "risk_budget", None)
        if risk_budget is not None:
            budgets[strategy.strategy_id] = risk_budget
    return {
        "risk_budget": budgets,
        "note": "risk_budget_usage 由 A 风控管线估算；B 仅提供波动/回撤参考（REFERENCE_ONLY）",
    }


# ---------------------------------------------------------------------------
# 资产信息组装
# ---------------------------------------------------------------------------

def _suggestion_asset_index(
    suggestion: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """从 grid_suggestion 包构建 {asset_id: 资产行 dict}（首个出现的策略为准）。"""
    index: dict[str, dict[str, Any]] = {}
    if suggestion is None:
        return index
    for strategy in suggestion.get("strategies", []):
        for asset in strategy.get("assets", []):
            asset_id = asset.get("asset_id")
            if asset_id and asset_id not in index:
                index[asset_id] = asset
    return index


def _asset_info_from_suggestion(
    asset_id: str,
    entry: dict[str, Any],
    enabled_grid_set: set[str],
) -> dict[str, Any]:
    spacing = entry.get("spacing_reference", {}) or {}
    return {
        "asset_id": asset_id,
        "anchor": entry.get("anchor_suggestion"),
        "anchor_basis": entry.get("anchor_basis"),
        "regular_spread": entry.get("regular_spread"),
        "edge_spread": entry.get("edge_spread"),
        "edge_spread_basis": entry.get("edge_spread_basis"),
        "regular_levels_per_side": entry.get("regular_levels_per_side"),
        "edge_levels_per_side": entry.get("edge_levels_per_side"),
        "suitability_score": entry.get("suitability_score"),
        "suitability": entry.get("suitability"),
        "spacing_reference": {
            "default": spacing.get("default"),
            "min": spacing.get("min"),
            "max": spacing.get("max"),
            "basis": spacing.get("basis"),
            "confidence": spacing.get("confidence"),
        },
        "is_currently_enabled": asset_id in enabled_grid_set,
        "theoretical_profit": entry.get("theoretical_profit"),
        "theoretical_profit_actual": entry.get("theoretical_profit_actual"),
    }


def _asset_info_recompute(
    asset_id: str,
    market_data: dict[str, pd.DataFrame],
    ref_by_asset: dict[str, dict[str, Any]],
    gs_params: dict[str, Any],
    data_asof: str,
    enabled_grid_set: set[str],
    grid_config_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """suggestion 缺失时的兜底：从行情重算（不虚构，缺失置 None）。"""
    frame = market_data.get(asset_id, pd.DataFrame())
    window = _gs._asset_window(frame, int(gs_params["window_days"]), data_asof)
    grid_row = ref_by_asset.get(asset_id, {})
    suit = _gs._compute_suitability(window, grid_row, gs_params)
    anchor_info = _gs._anchor_suggestion(window, gs_params)
    anchor = anchor_info["anchor"]
    anchor_basis = anchor_info["basis"]
    stability = anchor_info["stability"] or {}
    regular, edge, edge_basis, spread_info = _gs._spreads(window, grid_row, gs_params)
    regular_levels, edge_levels = _gs._levels(grid_row, gs_params)
    triplet = _gs._spacing_reference(window, grid_row, gs_params)
    amount_per_grid = float(gs_params.get("amount_per_grid", 6000.0))
    fee_rate = float(gs_params.get("fee_rate", 0.0005))
    theory_max_w = float(gs_params.get("theory_max_w", 1.0))
    tp = compute_asset_theoretical_profit(
        asset_id,
        regular,
        window,
        amount_per_grid,
        fee_rate,
        theory_max_w,
        levels_total=int(gs_params.get("levels_total", 9)),
    )
    gc = (grid_config_map or {}).get(asset_id)
    tp_actual = compute_asset_theoretical_profit_actual(
        asset_id,
        gc.regular_spread if gc is not None else None,
        gc.regular_levels_per_side if gc is not None else None,
        gc.edge_levels_per_side if gc is not None else None,
        window,
        amount_per_grid,
        fee_rate,
        theory_max_w,
    )
    return {
        "asset_id": asset_id,
        "anchor": anchor,
        "anchor_basis": anchor_basis,
        "anchor_sources": anchor_info["sources"],
        "anchor_stability_score": stability.get("score"),
        "anchor_stability_grade": stability.get("grade"),
        "anchor_references": anchor_info["references"],
        "regular_spread": regular,
        "edge_spread": edge,
        "edge_spread_basis": edge_basis,
        "spread_basis": spread_info.get("basis"),
        "spread_regime": {
            "regime": spread_info.get("regime"),
            "adx": spread_info.get("adx"),
            "atr_pct": spread_info.get("atr_pct"),
        },
        "spread_alternatives": spread_info.get("alternatives"),
        "cost_constraint_applied": spread_info.get("cost_constraint_applied"),
        "cost_constraint_note": spread_info.get("cost_constraint_note"),
        "regular_levels_per_side": regular_levels,
        "edge_levels_per_side": edge_levels,
        "suitability_score": suit["score"],
        "suitability": suit["grade"],
        "spacing_reference": {
            "default": triplet.get("default"),
            "min": triplet.get("min"),
            "max": triplet.get("max"),
            "basis": triplet.get("basis"),
            "confidence": triplet.get("confidence"),
        },
        "is_currently_enabled": asset_id in enabled_grid_set,
        "theoretical_profit": tp,
        "theoretical_profit_actual": tp_actual,
    }


def _grid_config_index(strategy_assets: list[Any]) -> dict[str, Any]:
    """asset_id → A 权威 ``ContractAsset.grid_config``（优先 enabled grid 策略）。

    与 ``grid_suggestion.build_grid_suggestion`` 的策略过滤同口径（capital_bucket==
    "grid" 优先，缺失回退 decision_rule=="grid"），首个出现为准（契约内 asset 唯一）。
    """
    index: dict[str, Any] = {}
    for strategy in strategy_assets:
        if not getattr(strategy, "enabled", True):
            continue
        bucket = getattr(strategy, "capital_bucket", None)
        if bucket is not None:
            if bucket != "grid":
                continue
        elif getattr(strategy, "decision_rule", None) != "grid":
            continue
        for asset in getattr(strategy, "assets", []) or []:
            gc = getattr(asset, "grid_config", None)
            if gc is not None and asset.asset_id not in index:
                index[asset.asset_id] = gc
    return index


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def build_grid_recommendation(
    strategy_assets: list[Any],
    market_data: dict[str, pd.DataFrame],
    grid_reference: pd.DataFrame | dict[str, Any] | None,
    suggestion: dict[str, Any] | None,
    shared_grid: dict[str, Any] | None,
    data_asof: str,
) -> dict[str, Any]:
    """构建网格推荐组合包（grid_recommendations.json）。

    参数：
        strategy_assets: ``ContractStrategy`` 列表（来自 A 侧策略契约）。
        market_data: ``{asset_id: DataFrame(trade_date, close, [high, low])}``。
        grid_reference: grid_reference_table DataFrame 或 ``{asset_id: 行 dict}``；
            None 时从行情自行计算。
        suggestion: ``build_grid_suggestion`` 的返回包；None 时对方案标的兜底重算。
        shared_grid: ``contract.shared_config.grid``（A 侧 "grid" 段；缺省用代码常量）。
        data_asof: 数据截止日（``YYYY-MM-DD``）。
    """
    params = _reco_params(shared_grid)
    gs_params = _gs._grid_params(shared_grid)
    pool, enabled_grid = _pool_assets(strategy_assets, params)
    enabled_grid_set = set(enabled_grid)
    ref_by_asset = _gs._grid_reference_index(grid_reference)
    suggestion_index = _suggestion_asset_index(suggestion)
    grid_config_map = _grid_config_index(strategy_assets)

    warnings: list[str] = []
    account_total_capital: float | None = None
    if isinstance(shared_grid, dict):
        account_total_capital = _gs._to_float(shared_grid.get("account_total_capital"))
    if account_total_capital is None:
        warnings.append(
            "shared_config.grid.account_total_capital 缺失，"
            "grid_budget/utilization 置 None（不虚构）"
        )

    # 策略级资金字段（契约透传；B 不越权读 A config/）。
    strategies_out: list[dict[str, Any]] = []
    strategy_weights: dict[str, float] = {}
    for strategy in strategy_assets:
        if not getattr(strategy, "enabled", True):
            continue
        weight = float(getattr(strategy, "target_capital_weight", 0.0) or 0.0)
        risk_budget = getattr(strategy, "risk_budget", None)
        strategies_out.append({
            "strategy_id": strategy.strategy_id,
            "decision_rule": strategy.decision_rule,
            "target_capital_weight": round(weight, 6),
            "risk_budget": risk_budget,
        })
        if strategy.decision_rule == "grid" and weight > 0:
            strategy_weights[strategy.strategy_id] = weight

    # 池级相关性（90d 窗口，min_overlap=60）。
    corr_info = _portfolio_correlation(pool, market_data, params, data_asof)

    # 间距参考三元组（全池，随日更口径）。
    spacing_reference: list[dict[str, Any]] = []
    for asset_id in pool:
        frame = market_data.get(asset_id, pd.DataFrame())
        window = _gs._asset_window(frame, int(gs_params["window_days"]), data_asof)
        grid_row = ref_by_asset.get(asset_id, {})
        triplet = _gs._spacing_reference(window, grid_row, gs_params)
        spacing_reference.append({
            "asset_id": asset_id,
            "default": triplet.get("default"),
            "min": triplet.get("min"),
            "max": triplet.get("max"),
            "basis": triplet.get("basis"),
            "confidence": triplet.get("confidence"),
            "is_currently_enabled": asset_id in enabled_grid_set,
        })

    # 3 套方案（确定性贪心）。
    plans: list[dict[str, Any]] = []
    for spec in params["plan_specs"]:
        plan_id = str(spec["plan_id"])
        target_min = int(spec.get("target_min", 0))
        target_max = int(spec.get("target_max", 0))
        corr_cap = float(spec.get("corr_cap", 1.0))
        rank = _rank_assets(pool, suggestion_index)
        plan_asset_ids = _select_plan(
            pool, corr_info["matrix"], target_min, target_max, corr_cap, rank
        )
        suitability_map: dict[str, float | None] = {}
        assets_out: list[dict[str, Any]] = []
        for asset_id in plan_asset_ids:
            entry = suggestion_index.get(asset_id)
            if entry is not None:
                info = _asset_info_from_suggestion(asset_id, entry, enabled_grid_set)
            else:
                info = _asset_info_recompute(
                    asset_id, market_data, ref_by_asset, gs_params, data_asof,
                    enabled_grid_set, grid_config_map,
                )
            suitability_map[asset_id] = info.get("suitability_score")
            assets_out.append(info)
        metrics = portfolio_metrics(
            plan_asset_ids, market_data, corr_info["matrix"],
            suitability_map, corr_cap, params, data_asof,
        )
        capital = capital_metrics(plan_asset_ids, account_total_capital, strategy_weights, params)
        per_asset_tp = {info["asset_id"]: info.get("theoretical_profit") for info in assets_out}
        portfolio_tp = compute_portfolio_theoretical_profit(plan_asset_ids, per_asset_tp)
        per_asset_tp_actual = {
            info["asset_id"]: info.get("theoretical_profit_actual") for info in assets_out
        }
        portfolio_tp_actual = compute_portfolio_theoretical_profit(plan_asset_ids, per_asset_tp_actual)
        plans.append({
            "plan_id": plan_id,
            "target_min": target_min,
            "target_max": target_max,
            "corr_cap": corr_cap,
            "assets": assets_out,
            "metrics": metrics,
            "capital": capital,
            "portfolio_theoretical_profit": portfolio_tp,
            "portfolio_theoretical_profit_actual": portfolio_tp_actual,
        })

    return {
        "schema_version": GRID_RECOMMENDATION_SCHEMA_VERSION,
        "approval_policy": "REFERENCE_ONLY",
        "cadence": GRID_RECOMMENDATION_CADENCE,
        "generated_date": today_iso(),
        "data_asof": data_asof,
        "universe": {"pool": pool, "enabled_grid_assets": enabled_grid},
        "capital_params": {
            "account_total_capital": account_total_capital,
            "default_amount_per_grid": float(params.get("default_amount_per_grid", 6000.0)),
            "levels_total": int(params.get("levels_total", 9)),
            "inventory_formula": "amount_per_grid * levels_total * n_assets",
            "note": "9 档全占（全区间可双向交易，中轴只是参考非分界线）；"
                    "risk_budget_usage 由 A 风控管线估算",
            "theoretical_profit_params": {
                "fee_rate": float(params.get("fee_rate", 0.0005)),
                "amount_per_grid": float(params.get("default_amount_per_grid", 6000.0)),
                "theory_max_w": float(params.get("theory_max_w", 1.0)),
                "levels_total": int(params.get("levels_total", 9)),
                "degraded": True,
                "note": "portfolio theoretical max (k*C*Leff%) deferred; "
                        "accounting total + per-asset max only",
                "actual_basis_note": "portfolio_theoretical_profit_actual = "
                        "A actual grid params (spread/levels from contract assets[].grid_config)",
            },
        },
        "strategies": strategies_out,
        "risk_annotation": risk_annotation(strategy_assets),
        "plans": plans,
        "spacing_reference": spacing_reference,
        "warnings": warnings,
    }


__all__ = [
    "build_grid_recommendation",
    "capital_metrics",
    "portfolio_metrics",
    "risk_annotation",
    "spacing_reference_triplet",
]
