"""
月度信号生成 — 自动完成数据更新、状态计算、评分输出。

generate_signal() 为外部唯一入口，返回完整的评分报告字典。
"""

from __future__ import annotations

from qteasy_research.macro import config
from qteasy_research.macro.factors import MacroDataFetcher
from qteasy_research.macro.scoring import MacroScoringSystem
from qteasy_research.macro.states import FactorStateClassifier


def generate_signal(
    weights: dict | None = None,
    decision_threshold: float | None = None,
    verbose: bool = True,
) -> dict:
    """
    生成当前宏观因子评分信号。

    Parameters
    ----------
    weights : dict, optional
        因子权重覆盖。
    decision_threshold : float, optional
        决策阈值覆盖。
    verbose : bool
        是否打印过程信息。

    Returns
    -------
    dict
        完整的评分报告，包含：
        - factor_states: 各因子当前状态
        - composite_prob: 综合上涨概率
        - action: 配置建议
        - details: 各因子贡献明细
        - asset_scores: 各资产评分
    """
    # 1. 加载本地数据
    fetcher = MacroDataFetcher()
    factor_data = fetcher.load_all()

    if verbose:
        print(f"已加载 {len(factor_data)} 个因子的本地数据")

    if not factor_data:
        raise RuntimeError(
            "没有找到本地宏观数据。请先运行：\n"
            "  python run_macro.py --init"
        )

    # 2. 状态分类
    clf = FactorStateClassifier()
    factor_states = clf.classify_all(factor_data)

    if verbose:
        print("因子状态计算完成")

    # 3. 评分
    scorer = MacroScoringSystem(
        weights=weights,
        decision_threshold=decision_threshold,
    )
    score_result = scorer.score(factor_states)

    if verbose:
        print(
            f"综合评分：{score_result['composite_prob']:.1%} → {score_result['action']}"
        )

    # 4. 资产评分
    asset_scores = scorer.score_all_assets(factor_states)

    # 5. 组装报告
    report = {
        "factor_states": factor_states,
        "composite_prob": score_result["composite_prob"],
        "action": score_result["action"],
        "details": score_result["details"],
        "asset_scores": asset_scores,
    }

    return report


def print_report(report: dict) -> None:
    """打印评分报告到控制台。"""
    print()
    print("=" * 70)
    print("  宏观因子评分报告")
    print("=" * 70)

    # 因子状态表
    print()
    print(f"  {'因子':<12} {'最新值':<10} {'Z-score':<10} {'状态':<10}")
    print(f"  {'-'*42}")
    for name, info in report["factor_states"].items():
        state_symbol = {"up": "↑", "down": "↓", "neutral": "→"}
        s = state_symbol.get(info["state"], "→")
        print(
            f"  {info['label']:<12} {info['latest_value']:<10} "
            f"{info['score']:<10} {s} {info['state']}"
        )

    print()
    print(f"  综合上涨概率：{report['composite_prob']:.1%}")
    print(f"  配置建议：{report['action']}")

    # 各因子贡献
    if report.get("details"):
        print()
        print(f"  各因子贡献：")
        for d in report["details"]:
            print(
                f"    {d['label']}: {d['prob']:.0%} "
                f"(权重 {d['weight']:.0%}, 状态 {d['state']})"
            )

    # 资产评分
    if report.get("asset_scores"):
        print()
        print(f"  资产评分：")
        for asset_name, score in report["asset_scores"].items():
            print(
                f"    {asset_name}: {score['composite_prob']:.0%} → {score['action']}"
            )

    print()
    print("=" * 70)
