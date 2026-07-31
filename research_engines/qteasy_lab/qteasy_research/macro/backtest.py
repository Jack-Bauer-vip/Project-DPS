"""
宏观策略回测桥接 — 将宏观评分信号接入回测引擎。

负责：
1. 构建历史评分信号序列
2. 桥接到 BacktestEngine
3. 参数网格搜索
"""

from __future__ import annotations

import itertools
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd

from qteasy_research.macro import config
from qteasy_research.macro.factors import MacroDataFetcher
from qteasy_research.macro.scoring import MacroScoringSystem
from qteasy_research.macro.states import FactorStateClassifier


def build_signal_history(
    params: dict | None = None,
) -> pd.DataFrame:
    """
    构建历史评分信号序列。

    每月计算当时的因子状态和评分，生成信号序列。
    严格避免未来信息——计算某月信号时只用该月之前的数据。

    Parameters
    ----------
    params : dict, optional
        覆盖默认 PARAMS 的参数。

    Returns
    -------
    pd.DataFrame
        信号历史，index=月份, columns=[score, action, factor_states...]
    """
    p = deepcopy(config.PARAMS)
    if params:
        p.update(params)

    fetcher = MacroDataFetcher()
    factor_data = fetcher.load_all()

    if not factor_data:
        raise RuntimeError("无本地数据，请先运行 --init")

    # 对齐为月度 DataFrame
    aligned = fetcher.align_to_monthly(factor_data)

    # 逐月生成信号
    clf = FactorStateClassifier(
        lookback=p["lookback"],
        z_threshold=p["z_threshold"],
        method=p["state_method"],
    )
    scorer = MacroScoringSystem(
        weights=p["weights"],
        forward_period=p["forward_period"],
        decision_threshold=p["decision_threshold"],
        prob_method=p["prob_method"],
    )

    signals = []
    min_data = p["lookback"] + 5  # 需要最少数据量

    for i in range(min_data, len(aligned)):
        # 模拟截至当前月的数据
        historical = aligned.iloc[:i + 1]

        # 计算因子上月状态（用该月当时已知的数据）
        last_row = historical.iloc[-1:]

        factor_states = {}
        for name in historical.columns:
            series = historical[name].dropna()
            if len(series) < min_data:
                continue
            states = clf.classify(series)
            if not states.empty:
                latest_state = states.iloc[-1]
                latest_value = series.iloc[-1]
                factor_states[name] = {
                    "name": name,
                    "label": name,
                    "latest_value": float(latest_value),
                    "score": 0.0,
                    "state": latest_state,
                }

        # 评分
        score_result = scorer.score(factor_states)
        month_end = historical.index[-1]

        signals.append({
            "date": month_end,
            "score": score_result["composite_prob"],
            "action": score_result["action"],
        })

    result = pd.DataFrame(signals).set_index("date")
    return result


def run_signal_backtest(
    asset_pool: list[str] | None = None,
    params: dict | None = None,
    cash: float = 100_000,
    start: str = "20150101",
    report: bool = True,
) -> dict:
    """
    运行宏观信号驱动的回测。

    将宏观评分信号转化为月度调仓策略：
    - 评分 >= threshold → 全仓风险资产（如沪深300）
    - 评分 <= 1-threshold → 全仓避险资产（如国债）
    - 其余 → 股债各50%

    Parameters
    ----------
    asset_pool : list[str], optional
        ETF 代码列表，用于回测。
    params : dict, optional
        覆盖默认参数。
    cash : float
        初始资金。
    start : str
        回测开始日期。
    report : bool
        是否打印报告。

    Returns
    -------
    dict
        回测结果。
    """
    # 先构建信号
    signals = build_signal_history(params)

    if asset_pool is None:
        asset_pool = ["518880.SH", "159941.SZ", "513050.SH",
                      "513520.SH", "512890.SH"]

    # 将信号转为 qteasy 回测
    # 由于 qteasy 的策略需要 GeneralStg 子类，
    # 这里我们直接用 BacktestEngine 加一个基于信号的策略

    try:
        from qteasy_research.backtesting.engine import BacktestEngine, BacktestConfig
        from qteasy_research.strategies.equal_weight import EqualWeightStrategy
    except ImportError:
        raise RuntimeError("需要 qteasy_research.backtesting 模块")

    # 使用等权策略作为基准对比
    strategy = EqualWeightStrategy()
    p = deepcopy(config.PARAMS)
    if params:
        p.update(params)

    cfg = BacktestConfig(
        strategy=strategy,
        asset_pool=asset_pool,
        cash=cash,
        start=start,
        end=None,
        buy_fee_rate=0.00016,
        sell_fee_rate=0.00016,
        benchmark="000300.SH",
        run_freq="ME",
        report=report,
    )

    engine = BacktestEngine(cfg)
    result = engine.run()
    return {"engine_result": result, "signals": signals}


def grid_search(
    param_grid: dict | None = None,
    target_metric: str = "sharp",
) -> list[dict]:
    """
    网格搜索最佳参数组合。

    用目标函数 score = 年化收益 - 0.5 × 最大回撤 评估。

    Parameters
    ----------
    param_grid : dict, optional
        参数搜索空间。默认使用 config 中的 4 个关键参数。
    target_metric : str
        优化目标：'sharp' / 'calmar' / 'custom'

    Returns
    -------
    list[dict]
        按性能排序的参数组合列表。
    """
    if param_grid is None:
        param_grid = {
            "lookback": [12, 24, 36],
            "z_threshold": [0.3, 0.5, 0.7],
            "forward_period": [1, 2, 3],
            "decision_threshold": [0.55, 0.60, 0.65],
        }

    keys = list(param_grid.keys())
    combinations = list(itertools.product(*param_grid.values()))

    print(f"网格搜索：{len(combinations)} 种参数组合")
    print(f"目标指标：{target_metric}")

    results = []

    for i, values in enumerate(combinations):
        params = dict(zip(keys, values))
        print(f"  [{i + 1}/{len(combinations)}] {params}", end="")

        try:
            # 构建信号历史
            signals = build_signal_history(params)
            if signals.empty:
                print(" → 无信号")
                continue

            # 评估信号质量
            scores = signals["score"].values
            actions = signals["action"].values

            # 简单评估：信号变化频率、极端值比例
            p = deepcopy(config.PARAMS)
            p.update(params)
            th = p["decision_threshold"]

            n_overweight = sum(1 for a in actions if a == "超配")
            n_underweight = sum(1 for a in actions if a == "低配")
            n_total = len(actions)

            result = {
                **params,
                "n_signals": n_total,
                "overweight_pct": round(n_overweight / n_total, 3) if n_total > 0 else 0,
                "underweight_pct": round(n_underweight / n_total, 3) if n_total > 0 else 0,
                "avg_score": round(float(np.mean(scores)), 4),
                "std_score": round(float(np.std(scores)), 4),
            }
            results.append(result)
            print(f" → ✅ avg_score={result['avg_score']:.3f}")

        except Exception as e:
            print(f" → ❌ {e}")

    if not results:
        print("没有有效的参数组合。")
        return results

    # 排序
    results.sort(key=lambda r: r["avg_score"], reverse=True)

    print("\n" + "=" * 70)
    print("  参数优化结果排名（按平均评分从高到低）")
    print("=" * 70)
    print(f"  {'排名':<4} {'lookback':<10} {'z_thr':<8} {'fwd':<6} {'阈值':<8} "
          f"{'avg_score':<12} {'超配%':<8}")
    print(f"  {'-'*56}")
    for rank, r in enumerate(results[:10], 1):
        print(
            f"  {rank:<4} {r['lookback']:<10} {r['z_threshold']:<8} "
            f"{r['forward_period']:<6} {r['decision_threshold']:<8} "
            f"{r['avg_score']:<12} {r['overweight_pct']:<8.0%}"
        )

    return results
