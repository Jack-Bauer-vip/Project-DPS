"""
概率映射与评分系统 — 计算因子状态 → 资产的上涨概率。

核心方法：
1. historical_freq（默认）：统计历史"状态→未来N月上涨"的条件频率
2. logistic（备选）：用逻辑回归拟合概率（需要 sklearn）
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qteasy_research.macro import config


class MacroScoringSystem:
    """
    宏观因子评分系统。

    使用历史条件概率法（或逻辑回归）将因子状态映射为资产上涨概率，
    再按权重加权合成综合评分。

    Parameters
    ----------
    weights : dict, optional
        各因子权重，默认从 config.PARAMS 读取。
    forward_period : int
        预测未来几个月，默认从 config.PARAMS 读取。
    decision_threshold : float
        超配/低配阈值，默认从 config.PARAMS 读取。
    prob_method : str
        "historical_freq" 或 "logistic"。

    Examples
    --------
    >>> scorer = MacroScoringSystem()
    >>> prob_tables = scorer.build_prob_tables(factor_data, asset_data)
    >>> scores = scorer.score(current_states, prob_tables)
    """

    def __init__(
        self,
        weights: dict | None = None,
        forward_period: int | None = None,
        decision_threshold: float | None = None,
        prob_method: str | None = None,
    ) -> None:
        p = config.PARAMS
        self.weights = weights or p["weights"]
        self.forward_period = forward_period or p["forward_period"]
        self.decision_threshold = decision_threshold or p["decision_threshold"]
        self.prob_method = prob_method or p["prob_method"]
        self.last_prob_table_metadata: dict = {}

    # ---------------------------------------------------------------
    # 概率表构建
    # ---------------------------------------------------------------

    def build_prob_tables(
        self,
        factor_data: dict[str, pd.Series],
        asset_monthly: pd.DataFrame,
        forward_period: int | None = None,
        *,
        include_metadata: bool = False,
    ) -> dict[str, dict[str, float]]:
        """
        构建每个因子的"状态 → 未来N月上涨概率"表。

        Parameters
        ----------
        factor_data : dict[str, pd.Series]
            各因子历史数据（月末频率）。
        asset_monthly : pd.DataFrame
            资产月收益率 DataFrame，列为资产名，行为月份。
        forward_period : int, optional
            预测未来月数。

        Returns
        -------
        dict
            {因子名: {'up': 概率, 'neutral': 概率, 'down': 概率}}
        """
        fp = forward_period or self.forward_period
        prob_tables = {}
        metadata = {}

        # 计算资产未来 N 月收益
        forward_returns = asset_monthly.shift(-fp).rolling(fp).apply(
            lambda x: (1 + x).prod() - 1 if x.notna().sum() == fp else np.nan
        )

        for factor_name, series in factor_data.items():
            if series.empty or factor_name not in self.weights:
                continue

            # 计算因子状态
            from qteasy_research.macro.states import FactorStateClassifier
            clf = FactorStateClassifier()
            states = clf.classify(series)

            # 对齐因子与资产的时间轴
            common_idx = states.index.intersection(forward_returns.index)
            if len(common_idx) < 10:
                metadata[factor_name] = {"sample_count": len(common_idx), "confidence": 0.0, "usable": False, "reason": "共同样本少于10期"}
                continue

            states_aligned = states.loc[common_idx]
            returns_aligned = forward_returns.loc[common_idx]

            # 按状态分组计算上涨概率
            prob_tables[factor_name] = {}
            metadata[factor_name] = {"sample_count": int(len(common_idx)), "states": {}, "confidence": 1.0, "usable": True}

            for state in ("up", "neutral", "down"):
                mask = states_aligned == state
                if mask.sum() < 3:
                    prob_tables[factor_name][state] = 0.5  # 样本不足时默认50%
                    metadata[factor_name]["states"][state] = {"sample_count": int(mask.sum()), "confidence": 0.0, "usable": False, "reason": "该状态样本少于3期"}
                    continue

                state_returns = returns_aligned[mask]

                # 多个资产时取平均
                if isinstance(state_returns, pd.DataFrame):
                    state_returns = state_returns.mean(axis=1)

                prob = float((state_returns > 0).mean())
                prob_tables[factor_name][state] = round(prob, 4)
                metadata[factor_name]["states"][state] = {"sample_count": int(mask.sum()), "confidence": 1.0, "usable": True}

        self.last_prob_table_metadata = metadata
        if include_metadata:
            prob_tables["__metadata__"] = metadata
        return prob_tables

    # ---------------------------------------------------------------
    # 评分计算
    # ---------------------------------------------------------------

    def score(
        self,
        factor_states: dict[str, dict],
        prob_tables: dict[str, dict[str, float]] | None = None,
    ) -> dict:
        """
        计算当前宏观因子状态下的资产综合评分。

        Parameters
        ----------
        factor_states : dict
            states.py 输出的因子状态字典。
        prob_tables : dict, optional
            概率表。不提供则使用默认等概率。

        Returns
        -------
        dict
            评分结果，包含各因子贡献和综合概率。
        """
        total_weight = 0.0
        weighted_prob = 0.0
        details = []
        metadata = (prob_tables or {}).get("__metadata__", {}) if isinstance(prob_tables, dict) else {}

        for factor_name, state_info in factor_states.items():
            weight = self.weights.get(factor_name, 0)
            if weight <= 0:
                continue

            state = state_info.get("state", "neutral")

            if factor_name in metadata and not metadata[factor_name].get("usable", True):
                details.append({
                    "factor": factor_name,
                    "label": state_info.get("label", factor_name),
                    "value": state_info.get("latest_value"),
                    "state": state,
                    "weight": weight,
                    "prob": None,
                    "confidence": 0.0,
                    "reason": metadata[factor_name].get("reason", "样本不足"),
                })
                continue
            state_meta = metadata.get(factor_name, {}).get("states", {}).get(state, {})
            if state_meta and not state_meta.get("usable", True):
                details.append({
                    "factor": factor_name,
                    "label": state_info.get("label", factor_name),
                    "value": state_info.get("latest_value"),
                    "state": state,
                    "weight": weight,
                    "prob": None,
                    "confidence": 0.0,
                    "reason": state_meta.get("reason", "该状态样本不足"),
                })
                continue
            if prob_tables and factor_name in prob_tables:
                prob = prob_tables[factor_name].get(state, 0.5)
            else:
                # 无概率表时，用简单的代替规则
                direction_factor = config.get_factor_config(factor_name)
                dir_sign = 1 if direction_factor and direction_factor.get("direction") == "positive" else -1
                state_map = {"up": 0.5 + 0.3 * dir_sign, "neutral": 0.5, "down": 0.5 - 0.3 * dir_sign}
                prob = state_map.get(state, 0.5)

            weighted_prob += weight * prob
            total_weight += weight

            details.append({
                "factor": factor_name,
                "label": state_info.get("label", factor_name),
                "value": state_info.get("latest_value"),
                "state": state,
                "weight": weight,
                "prob": round(prob, 4),
            })

        # 归一化
        composite_prob = round(weighted_prob / total_weight, 4) if total_weight > 0 else None

        # 判断建议
        if composite_prob is None:
            action = "数据不足"
        elif composite_prob >= self.decision_threshold:
            action = "超配"
        elif composite_prob <= (1 - self.decision_threshold):
            action = "低配"
        else:
            action = "中性"

        return {
            "composite_prob": composite_prob,
            "action": action,
            "details": details,
        }

    # ---------------------------------------------------------------
    # 批量评分（多个资产）
    # ---------------------------------------------------------------

    def score_all_assets(
        self,
        factor_states: dict[str, dict],
        prob_tables: dict[str, dict[str, float]] | None = None,
        asset_pool: dict | None = None,
    ) -> dict:
        """
        为资产池中的所有资产评分。

        Parameters
        ----------
        factor_states : dict
            当前因子状态。
        prob_tables : dict, optional
            各资产的概率表字典。
        asset_pool : dict, optional
            资产池定义，默认从 config.ASSET_POOL 读取。

        Returns
        -------
        dict
            {资产名: {prob, action, details}}
        """
        pool = asset_pool or config.ASSET_POOL
        result = {}

        # 如果 prob_tables 是以资产为键的，按资产评分
        if prob_tables and any(k in prob_tables for k in ("stock", "bond", "gold")):
            for asset_key, asset_info in pool.items():
                asset_prob_tables = prob_tables.get(asset_key, None)
                score_result = self.score(factor_states, asset_prob_tables)
                result[asset_info["name"]] = score_result
        else:
            # 统一概率表（所有资产用同一组概率）
            score_result = self.score(factor_states, prob_tables)
            for asset_key, asset_info in pool.items():
                result[asset_info["name"]] = {
                    "composite_prob": score_result["composite_prob"],
                    "action": self._adjust_action(
                        score_result["composite_prob"], asset_key
                    ),
                    "details": score_result["details"],
                }

        return result

    def _adjust_action(self, prob: float | None, asset_key: str) -> str:
        """按资产类型调整建议。"""
        th = self.decision_threshold
        if prob is None:
            return "数据不足"
        if prob >= th:
            return "超配"
        elif prob <= (1 - th):
            return "低配"
        return "中性"
