"""grid_theoretical_profit：网格理论收益补充参考（B2）单元测试。

会计式确定性部分、N 估算/回测实测独立字段与偏差、成本临界边界、缺数据降级、
组合降级（无组合 Leff）、build_grid_suggestion / build_grid_recommendation 集成。
纯 unittest + 合成 fixture，不碰真实 A 契约 / 共享目录。
运行：``cd research_engines/qteasy_lab && .venv/Scripts/python.exe -B -m unittest discover -s "D:/Project DPS/tests" -q``
"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from qteasy_research.reference import grid_suggestion as gs
from qteasy_research.reference import grid_recommendation as gr
from qteasy_research.reference import grid_theoretical_profit as gtp
from qteasy_research.reference.backtest_engine import ContractAsset, ContractStrategy


def _make_strategy(asset_ids, strategy_id: str = "grid_lh",
                   grid_spread: float | None = None) -> ContractStrategy:
    """grid_spread 提供 → 契约嵌套 grid_config（regular=3/edge=1，共 9 档）。
    grid_spread=None → 无 grid_config（实际口径降级路径）。"""
    assets = [
        ContractAsset(
            asset_id=a, role="grid", enabled=True,
            min_weight=0.0, target_weight=None, max_weight=0.2,
            target_weight_configured=False,
            anchor_price=100.0,
            regular_spread=grid_spread,
            edge_spread=grid_spread * 4.0 if grid_spread is not None else None,
            regular_levels_per_side=3 if grid_spread is not None else None,
            edge_levels_per_side=1 if grid_spread is not None else None,
        )
        for a in asset_ids
    ]
    return ContractStrategy(
        strategy_id=strategy_id, decision_rule="grid", enabled=True,
        use_target_ratio=False, rebalance_frequency="daily",
        rebalance_threshold_abs=0.03, asset_rebalance_threshold_abs=0.02,
        signal_filters=None, preferences={"macro_fit": -0.2}, assets=assets,
        capital_bucket="grid",
    )


def _zigzag_frame(levels=(0, 2, 0, -1, 0, 2), n_per: int = 15) -> pd.DataFrame:
    """阶梯价格序列：在 spread=0.05 网格上逐档移动，保证多次触发。"""
    dates = pd.date_range("2024-01-02", periods=len(levels) * n_per, freq="B").strftime("%Y-%m-%d")
    prices: list[float] = []
    p = 100.0
    for level in levels:
        target = 100.0 * (1.05 ** level)
        prices.extend(np.linspace(p, target, n_per))
        p = target
    return pd.DataFrame({"trade_date": dates, "close": prices, "vol": 1000.0})


def _osc_frame(n: int = 90, seed: int = 7) -> pd.DataFrame:
    """正弦 + 噪声（约 ±6% 摆幅），确保 0.05 网格多次触发。"""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    dates = pd.date_range("2024-01-02", periods=n, freq="B").strftime("%Y-%m-%d")
    close = 100.0 + 6.0 * np.sin(t * 2 * np.pi / 20) + rng.normal(0, 0.5, n)
    return pd.DataFrame({"trade_date": dates, "close": close, "vol": 1000.0})


def _market_data(n: int = 8) -> dict[str, pd.DataFrame]:
    return {f"A{i}.SZ": _osc_frame(90, seed=i) for i in range(n)}


def _asset_ids(n: int = 8) -> list[str]:
    return [f"A{i}.SZ" for i in range(n)]


def _grid_reference(asset_ids: list[str]) -> pd.DataFrame:
    return pd.DataFrame([
        {"asset_id": aid, "vol_rank_60d": 0.6, "suggested_reference_spread": 0.05,
         "cone_60_p95": 0.10, "cone_60_p50": 0.02}
        for aid in asset_ids
    ])


class AssetTheoreticalProfitTests(unittest.TestCase):
    """单标的理论收益：公式 / 边界 / 降级 / N 双口径。"""

    def setUp(self) -> None:
        self.frame = _zigzag_frame()
        self.amount = 6000.0
        self.fee = 0.0005

    def test_accounting_formula_and_basis(self) -> None:
        result = gtp.compute_asset_theoretical_profit(
            "A.SZ", 0.05, self.frame, self.amount, self.fee
        )
        self.assertFalse(result["degraded"])
        # 每格毛利 = 单份金额 × 间距
        self.assertAlmostEqual(result["round_profit"], self.amount * 0.05, places=4)
        # n_annual = 年化单边触发 / 2（往返口径，与 suitability.trigger_freq 同源）
        single = gs._annualized_single_side_triggers(self.frame, 0.05)
        self.assertAlmostEqual(result["n_annual"], single * 0.5, places=4)
        self.assertGreater(result["n_annual"], 0.0)
        # 会计式年化 = 毛利×往返 − 往返×双边费
        expected = round(
            result["round_profit"] * result["n_annual"]
            - result["n_annual"] * 2.0 * self.amount * self.fee,
            4,
        )
        self.assertAlmostEqual(result["annual_accounting_profit"], expected, places=4)
        # 理论最大 = 0.8 × w × σ × n × (单份金额 × levels_total)（levels_total 默认 1）
        sigma = gtp._sigma_annual(self.frame)
        self.assertIsNotNone(sigma)
        self.assertAlmostEqual(
            result["theory_max"],
            round(0.8 * sigma * result["n_annual"] * (self.amount * 1), 4),
            places=4,
        )
        # 理论上限应高于会计式年化收益（验证项：theory_max > annual_accounting_profit）
        self.assertGreater(result["theory_max"], result["annual_accounting_profit"])
        # 成本临界：0.05 > 2×0.0005 → True
        self.assertTrue(result["cost_breakeven"])
        self.assertEqual(result["basis"], "accounting")
        self.assertTrue(result["assumptions"])

    def test_theory_max_scales_with_levels_total(self) -> None:
        base = gtp.compute_asset_theoretical_profit(
            "A.SZ", 0.05, self.frame, self.amount, self.fee
        )
        full = gtp.compute_asset_theoretical_profit(
            "A.SZ", 0.05, self.frame, self.amount, self.fee, levels_total=9
        )
        self.assertIsNotNone(base["theory_max"])
        self.assertIsNotNone(full["theory_max"])
        # 档数 9 → 上限 ≈ 9×（与会计式金额同量纲；base 已四舍五入，用 delta 容差）
        self.assertAlmostEqual(full["theory_max"], base["theory_max"] * 9.0, delta=0.01)

    def test_cost_breakeven_threshold(self) -> None:
        # 低于双边费率 → 成本吞噬
        low = gtp.compute_asset_theoretical_profit(
            "A.SZ", 0.0008, self.frame, self.amount, self.fee
        )
        self.assertFalse(low["cost_breakeven"])
        # 高于双边费率 → 有利可图
        high = gtp.compute_asset_theoretical_profit(
            "A.SZ", 0.0012, self.frame, self.amount, self.fee
        )
        self.assertTrue(high["cost_breakeven"])

    def test_n_annual_and_backtest_independent_fields(self) -> None:
        result = gtp.compute_asset_theoretical_profit(
            "A.SZ", 0.05, self.frame, self.amount, self.fee, backtest_n_annual=12.0
        )
        self.assertIsNotNone(result["n_annual"])
        self.assertEqual(result["backtest_n_annual"], 12.0)
        self.assertAlmostEqual(result["n_deviation"], 12.0 - result["n_annual"], places=4)

    def test_backtest_none_when_not_run(self) -> None:
        result = gtp.compute_asset_theoretical_profit(
            "A.SZ", 0.05, self.frame, self.amount, self.fee
        )
        self.assertIsNone(result["backtest_n_annual"])
        self.assertIsNone(result["n_deviation"])

    def test_degraded_when_missing_spread(self) -> None:
        result = gtp.compute_asset_theoretical_profit(
            "A.SZ", None, self.frame, self.amount, self.fee
        )
        self.assertTrue(result["degraded"])
        self.assertIsNone(result["round_profit"])
        self.assertIsNone(result["annual_accounting_profit"])
        self.assertIsNone(result["theory_max"])

    def test_degraded_when_empty_frame(self) -> None:
        result = gtp.compute_asset_theoretical_profit(
            "A.SZ", 0.05, pd.DataFrame(), self.amount, self.fee
        )
        self.assertTrue(result["degraded"])


class AssetTheoreticalProfitActualTests(unittest.TestCase):
    """按 A 权威 grid_config 的 actual 口径：公式 / 档数映射 / 降级。"""

    def setUp(self) -> None:
        self.frame = _zigzag_frame()
        self.amount = 6000.0
        self.fee = 0.0005

    def test_actual_formula_uses_auth_spread(self) -> None:
        result = gtp.compute_asset_theoretical_profit_actual(
            "A.SZ", 0.015, 3, 1, self.frame, self.amount, self.fee
        )
        self.assertFalse(result["degraded"])
        # 每格毛利 = 单份金额 × A 权威间距（159985.SZ 场景：6000×0.015=90）
        self.assertAlmostEqual(result["round_profit"], self.amount * 0.015, places=4)
        self.assertEqual(result["basis"], "accounting (actual grid params)")
        self.assertEqual(result["actual_levels_total"], 9)  # (3+1)×2+1
        self.assertTrue(result["cost_breakeven"])  # 0.015 > 2×0.0005

    def test_actual_narrower_spread_more_triggers_than_suggest(self) -> None:
        suggest = gtp.compute_asset_theoretical_profit(
            "A.SZ", 0.05, self.frame, self.amount, self.fee
        )
        actual = gtp.compute_asset_theoretical_profit_actual(
            "A.SZ", 0.015, 3, 1, self.frame, self.amount, self.fee
        )
        self.assertGreater(actual["n_annual"], suggest["n_annual"])  # 窄间距触发更频繁
        self.assertLess(actual["round_profit"], suggest["round_profit"])
        # 软检查：actual 口径 theory_max > annual_accounting_profit
        self.assertGreater(actual["theory_max"], actual["annual_accounting_profit"])

    def test_actual_levels_total_mapping(self) -> None:
        # 每侧 2 常规 + 0 边缘 → 5 档；每侧 3+1 → 9 档
        a = gtp.compute_asset_theoretical_profit_actual(
            "A.SZ", 0.03, 2, 0, self.frame, self.amount, self.fee
        )
        self.assertEqual(a["actual_levels_total"], 5)
        b = gtp.compute_asset_theoretical_profit_actual(
            "A.SZ", 0.015, 3, 1, self.frame, self.amount, self.fee
        )
        self.assertEqual(b["actual_levels_total"], 9)

    def test_actual_degraded_when_gc_missing(self) -> None:
        # regular_spread=None（契约未导出）→ 降级不抛异常
        result = gtp.compute_asset_theoretical_profit_actual(
            "A.SZ", None, 3, 1, self.frame, self.amount, self.fee
        )
        self.assertTrue(result["degraded"])
        self.assertIsNone(result["round_profit"])

    def test_actual_degraded_when_levels_missing(self) -> None:
        result = gtp.compute_asset_theoretical_profit_actual(
            "A.SZ", 0.015, None, None, self.frame, self.amount, self.fee
        )
        self.assertTrue(result["degraded"])
        self.assertIn("levels", result["assumptions"][0])


class PortfolioTheoreticalProfitTests(unittest.TestCase):
    """组合理论收益（本轮降级：会计式汇总 + 各标的理论最大清单，无组合 Leff）。"""

    def test_degraded_and_accounting_total(self) -> None:
        per_asset = {
            "A.SZ": {"annual_accounting_profit": 100.0, "theory_max": 200.0},
            "B.SZ": {"annual_accounting_profit": 50.0, "theory_max": 120.0},
        }
        result = gtp.compute_portfolio_theoretical_profit(["A.SZ", "B.SZ"], per_asset)
        self.assertTrue(result["degraded"])  # 组合理论最大未上线
        self.assertEqual(result["annual_accounting_total"], 150.0)
        self.assertEqual(result["per_asset_max"], {"A.SZ": 200.0, "B.SZ": 120.0})

    def test_missing_asset_not_fabricated(self) -> None:
        per_asset = {"A.SZ": {"annual_accounting_profit": 100.0, "theory_max": 200.0}}
        result = gtp.compute_portfolio_theoretical_profit(["A.SZ", "C.SZ"], per_asset)
        self.assertEqual(result["annual_accounting_total"], 100.0)
        self.assertIsNone(result["per_asset_max"]["C.SZ"])


class BuildIntegrationTests(unittest.TestCase):
    """build_grid_suggestion / build_grid_recommendation 集成：理论收益字段就位。"""

    def test_suggestion_includes_theoretical_profit(self) -> None:
        strategy = _make_strategy(_asset_ids(2))
        suggestion = gs.build_grid_suggestion(
            [strategy], _market_data(2), _grid_reference(_asset_ids(2)), None, "2024-04-30"
        )
        self.assertIn("theoretical_profit_params", suggestion)
        self.assertEqual(suggestion["theoretical_profit_params"]["amount_per_grid"], 6000.0)
        for asset in suggestion["strategies"][0]["assets"]:
            tp = asset["theoretical_profit"]
            self.assertIsNotNone(tp)
            for field in ("round_profit", "n_annual", "backtest_n_annual",
                          "annual_accounting_profit", "theory_max", "cost_breakeven"):
                self.assertIn(field, tp)

    def test_suggestion_includes_theoretical_profit_actual(self) -> None:
        # 有 grid_config → 非降级 actual；无 grid_config → 降级（不抛异常）
        with_gc = _make_strategy(_asset_ids(2), grid_spread=0.015)
        suggestion = gs.build_grid_suggestion(
            [with_gc], _market_data(2), _grid_reference(_asset_ids(2)), None, "2024-04-30"
        )
        for asset in suggestion["strategies"][0]["assets"]:
            tp_actual = asset["theoretical_profit_actual"]
            self.assertFalse(tp_actual["degraded"])
            self.assertEqual(tp_actual["basis"], "accounting (actual grid params)")
            self.assertEqual(tp_actual["actual_levels_total"], 9)
            # 建议口径字段保留（旧输出零变化）
            self.assertIn("theoretical_profit", asset)
        without_gc = _make_strategy(_asset_ids(2))
        suggestion2 = gs.build_grid_suggestion(
            [without_gc], _market_data(2), _grid_reference(_asset_ids(2)), None, "2024-04-30"
        )
        for asset in suggestion2["strategies"][0]["assets"]:
            self.assertTrue(asset["theoretical_profit_actual"]["degraded"])

    def test_recommendation_includes_portfolio_theoretical_profit_actual(self) -> None:
        ids = _asset_ids(8)
        reco = gr.build_grid_recommendation(
            [_make_strategy(ids, grid_spread=0.015)], _market_data(8),
            _grid_reference(ids), None, None, "2024-04-30"
        )
        self.assertIn("theoretical_profit_params", reco["capital_params"])
        self.assertTrue(reco["capital_params"]["theoretical_profit_params"]["degraded"])
        self.assertTrue(reco["plans"])
        for plan in reco["plans"]:
            ptp = plan["portfolio_theoretical_profit"]
            self.assertTrue(ptp["degraded"])
            self.assertIn("annual_accounting_total", ptp)
            self.assertIn("per_asset_max", ptp)
            ptp_actual = plan["portfolio_theoretical_profit_actual"]
            self.assertTrue(ptp_actual["degraded"])
            self.assertIn("annual_accounting_total", ptp_actual)
            for asset in plan["assets"]:
                self.assertIn("theoretical_profit", asset)
                self.assertIn("theoretical_profit_actual", asset)


if __name__ == "__main__":
    unittest.main()
