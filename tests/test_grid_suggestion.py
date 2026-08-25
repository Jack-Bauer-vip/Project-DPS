"""grid_suggestion：网格建议引擎（P1-B）单元测试。

适合度四分量/分级、VWAP/SMA 中轴、两段步长（锥尾放大/封顶/缺省乘子）、
相关性对表、扁平 CSV、schema 与全 ASCII。
纯 unittest + 合成 fixture，不碰真实 A 契约。
运行：``cd research_engines/qteasy_lab && .venv/Scripts/python.exe -B -m unittest discover -s "D:/Project DPS/tests" -q``
"""

from __future__ import annotations

import math
import unittest

import numpy as np
import pandas as pd

from qteasy_research.reference import grid_suggestion as gs
from qteasy_research.reference.backtest_engine import ContractAsset, ContractStrategy


def _make_strategy(asset_ids=("A.SZ", "B.SZ"), strategy_id: str = "grid_lh",
                   capital_bucket: str | None = None) -> ContractStrategy:
    assets = [
        ContractAsset(
            asset_id=a, role="grid", enabled=True,
            min_weight=0.0, target_weight=None, max_weight=0.2,
            target_weight_configured=False,
        )
        for a in asset_ids
    ]
    return ContractStrategy(
        strategy_id=strategy_id, decision_rule="grid", enabled=True,
        use_target_ratio=False, rebalance_frequency="daily",
        rebalance_threshold_abs=0.03, asset_rebalance_threshold_abs=0.02,
        signal_filters=None, preferences={"macro_fit": -0.2}, assets=assets,
        capital_bucket=capital_bucket,
    )


def _frame(close, high=None, low=None, vol=None) -> pd.DataFrame:
    dates = pd.date_range("2024-01-02", periods=len(close), freq="B")
    data = {"trade_date": dates.strftime("%Y-%m-%d"), "close": np.asarray(close, dtype=float)}
    if high is not None:
        data["high"] = np.asarray(high, dtype=float)
        data["low"] = np.asarray(low, dtype=float)
    if vol is not None:
        data["vol"] = np.asarray(vol, dtype=float)
    return pd.DataFrame(data)


def _suitability_frame(n: int = 80, amplitude: float = 3.0, period: int = 20) -> pd.DataFrame:
    """围绕 100 的周期摆动（drift≈0、日均波幅≈4%），供分级测试。"""
    t = np.arange(n)
    close = 100.0 + amplitude * np.sin(t * 2 * np.pi / period)
    return _frame(close, high=close + 2.0, low=close - 2.0, vol=[1000.0] * n)


def _trend_frame(n: int = 80, slope: float = 0.8) -> pd.DataFrame:
    """强趋势 + 摆动：drift 高（drift_score→0），供低分级（marginal/not_suitable）测试。

    V2 下 amplitude 分量受 ATR 主公式约束，旧小摆动 fixture 会让 amplitude 饱和
    而无法落入低分级；用强趋势压低 drift_score 触发 marginal/not_suitable。
    """
    t = np.arange(n)
    close = 100.0 + t * slope + 5.0 * np.sin(t * 2 * np.pi / 20)
    return _frame(close, high=close + 3.0, low=close - 3.0, vol=[1000.0] * n)


def _grid_row(**overrides) -> dict:
    row = {
        "vol_rank_60d": 0.6,
        "suggested_reference_spread": 0.05,
        "cone_60_p95": 0.10,
        "cone_60_p50": 0.02,
    }
    row.update(overrides)
    return row


class SuitabilityTests(unittest.TestCase):
    """suitability 四分量 + 加权总分 + 分级。"""

    def test_breakdown_keys_and_range(self) -> None:
        frame = _suitability_frame()
        result = gs._compute_suitability(frame, _grid_row(), gs._grid_params(None))
        self.assertEqual(
            set(result["breakdown"]),
            {"vol_rank_score", "amplitude_score", "drift_score", "trigger_freq_score"},
        )
        for value in result["breakdown"].values():
            self.assertIsNotNone(value)
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 100.0)
        self.assertIn(result["grade"], ("suitable", "marginal", "not_suitable"))

    def test_grading_suitable(self) -> None:
        frame = _suitability_frame()
        result = gs._compute_suitability(
            frame, _grid_row(vol_rank_60d=0.6, suggested_reference_spread=0.05),
            gs._grid_params(None),
        )
        self.assertEqual(result["breakdown"]["vol_rank_score"], 100.0)
        self.assertGreater(result["breakdown"]["amplitude_score"], 95.0)
        self.assertGreater(result["breakdown"]["drift_score"], 85.0)
        self.assertGreater(result["breakdown"]["trigger_freq_score"], 90.0)
        self.assertGreaterEqual(result["score"], 70.0)
        self.assertEqual(result["grade"], "suitable")

    def test_grading_marginal(self) -> None:
        frame = _trend_frame()
        result = gs._compute_suitability(
            frame, _grid_row(vol_rank_60d=0.3), gs._grid_params(None),
        )
        # 强趋势 → drift_score=0；vol_rank=0.3 → vol_rank_score=40；
        # score≈52 → marginal（[45,70) 带）。
        self.assertEqual(result["breakdown"]["vol_rank_score"], 40.0)
        self.assertEqual(result["breakdown"]["drift_score"], 0.0)
        self.assertGreaterEqual(result["score"], 45.0)
        self.assertLess(result["score"], 70.0)
        self.assertEqual(result["grade"], "marginal")

    def test_grading_not_suitable(self) -> None:
        frame = _trend_frame()
        result = gs._compute_suitability(
            frame, _grid_row(vol_rank_60d=0.0), gs._grid_params(None),
        )
        # 强趋势 + vol_rank=0 → score≈40 → not_suitable（<45）。
        self.assertEqual(result["breakdown"]["vol_rank_score"], 0.0)
        self.assertLess(result["score"], 45.0)
        self.assertEqual(result["grade"], "not_suitable")

    def test_vol_rank_score_formula(self) -> None:
        frame = _suitability_frame()
        params = gs._grid_params(None)
        for rank, expected in [(0.6, 100.0), (0.35, 50.0), (0.1, 0.0), (1.0, 20.0)]:
            result = gs._compute_suitability(frame, _grid_row(vol_rank_60d=rank), params)
            self.assertAlmostEqual(
                result["breakdown"]["vol_rank_score"], expected, places=2,
                msg=f"vol_rank={rank}",
            )

    def test_trigger_freq_score_boundaries(self) -> None:
        params = gs._grid_params(None)
        self.assertEqual(gs._trigger_freq_score(30.0, params), 100.0)   # 目标带内
        self.assertEqual(gs._trigger_freq_score(0.0, params), 0.0)      # 0 触发
        self.assertEqual(gs._trigger_freq_score(150.0, params), 0.0)    # 超 max
        self.assertAlmostEqual(gs._trigger_freq_score(4.0, params), 50.0, places=2)  # 半程线性


class AnchorTests(unittest.TestCase):
    """中轴建议（V2）：多窗口(20/60/90/120)几何均值主锚 + 各窗口来源。"""

    def test_anchor_vwap_60(self) -> None:
        n = 130
        close = 100.0 + np.arange(n) * 0.1
        vol = np.arange(n) * 100.0 + 1000.0
        result = gs._anchor_suggestion(_frame(close, vol=vol), gs._grid_params(None))
        # 130 行 → 4 窗口全有效；全窗口有量 → VWAP 源。
        self.assertEqual(result["basis"], "geomean_of_4_sources")
        self.assertIn("vwap_60", result["sources"])
        expected = math.exp(
            sum(math.log(v) for v in result["sources"].values()) / len(result["sources"])
        )
        self.assertAlmostEqual(result["anchor"], round(expected, 3), places=3)

    def test_anchor_sma_60_when_no_volume(self) -> None:
        n = 130
        close = 100.0 + np.arange(n) * 0.1
        result = gs._anchor_suggestion(_frame(close), gs._grid_params(None))
        # 无量 → SMA 源；多窗口几何均值。
        self.assertEqual(result["basis"], "geomean_of_4_sources")
        self.assertIn("sma_60", result["sources"])


class SpreadsTests(unittest.TestCase):
    """两段步长：edge = regular × max(默认乘子, cone_p95/p50)，上限 4×regular。"""

    def test_edge_spread_cone_amplification(self) -> None:
        frame = _suitability_frame()
        row = _grid_row(suggested_reference_spread=0.02, cone_60_p95=0.06, cone_60_p50=0.02)
        regular, edge, basis, _info = gs._spreads(frame, row, gs._grid_params(None))
        self.assertIsNotNone(regular)
        self.assertAlmostEqual(edge, round(regular * 3.0, 4), places=4)  # cone 比例 3 > 默认 2 → ×3
        self.assertEqual(basis, "cone_60_p95")

    def test_edge_spread_capped_at_max_multiple(self) -> None:
        frame = _suitability_frame()
        row = _grid_row(suggested_reference_spread=0.02, cone_60_p95=0.10, cone_60_p50=0.02)
        regular, edge, basis, _info = gs._spreads(frame, row, gs._grid_params(None))
        self.assertIsNotNone(regular)
        self.assertAlmostEqual(edge, round(regular * 4.0, 4), places=4)  # 比例 5 封顶 4×
        self.assertEqual(basis, "cone_60_p95")

    def test_edge_spread_cone_ratio_below_default_uses_default(self) -> None:
        frame = _suitability_frame()
        row = _grid_row(suggested_reference_spread=0.02, cone_60_p95=0.03, cone_60_p50=0.02)
        regular, edge, basis, _info = gs._spreads(frame, row, gs._grid_params(None))
        self.assertIsNotNone(regular)
        self.assertAlmostEqual(edge, round(regular * 2.0, 4), places=4)  # 比例 1.5 < 2 → 默认乘子
        self.assertEqual(basis, "cone_60_p95")

    def test_edge_spread_default_multiplier_when_no_cone(self) -> None:
        frame = _suitability_frame()
        row = _grid_row(suggested_reference_spread=0.02, cone_60_p95=None, cone_60_p50=None)
        regular, edge, basis, _info = gs._spreads(frame, row, gs._grid_params(None))
        self.assertIsNotNone(regular)
        self.assertAlmostEqual(edge, round(regular * 2.0, 4), places=4)
        self.assertEqual(basis, "default_multiplier")


class LevelsTests(unittest.TestCase):
    """档数：默认 3+1；vol_rank 超阈时边缘档升 2。"""

    def test_levels_default(self) -> None:
        regular, edge = gs._levels(_grid_row(), gs._grid_params(None))
        self.assertEqual(regular, 3)
        self.assertEqual(edge, 1)

    def test_levels_high_vol_boosts_edge(self) -> None:
        regular, edge = gs._levels(_grid_row(vol_rank_60d=0.9), gs._grid_params(None))
        self.assertEqual(regular, 3)
        self.assertEqual(edge, 2)


class CorrelationTests(unittest.TestCase):
    """相关性：同源行情两标的应进 high_corr_pairs、redundancy_note=high。"""

    @staticmethod
    def _two_asset_data(n: int = 90) -> dict[str, pd.DataFrame]:
        t = np.arange(n)
        common = 6.0 * np.sin(t * 2 * np.pi / 20)
        rng = np.random.default_rng(123)
        noise = rng.normal(0, 0.3, n)
        dates = pd.date_range("2024-01-02", periods=n, freq="B").strftime("%Y-%m-%d")
        return {
            "A.SZ": pd.DataFrame({"trade_date": dates, "close": 100.0 + common + noise, "vol": 1000.0}),
            "B.SZ": pd.DataFrame({"trade_date": dates, "close": 100.0 + 2.0 * common + 0.5 * noise, "vol": 1000.0}),
        }

    def test_high_corr_pairs_detected(self) -> None:
        strategy = _make_strategy(("A.SZ", "B.SZ"))
        result = gs._correlation(strategy, self._two_asset_data(), gs._grid_params(None), "2024-05-31")
        self.assertEqual(len(result["high_corr_pairs"]), 1)
        pair = result["high_corr_pairs"][0]
        self.assertIn(pair["asset_a"], ("A.SZ", "B.SZ"))
        self.assertIn(pair["asset_b"], ("A.SZ", "B.SZ"))
        self.assertGreater(abs(pair["corr"]), 0.6)
        self.assertGreaterEqual(result["avg_corr"], 0.6)
        self.assertEqual(result["redundancy_note"], "high")
        self.assertIn("A.SZ", result["matrix"])
        self.assertIn("B.SZ", result["matrix"])

    def test_single_asset_low_redundancy(self) -> None:
        strategy = _make_strategy(("A.SZ",))
        result = gs._correlation(strategy, self._two_asset_data(), gs._grid_params(None), "2024-05-31")
        self.assertEqual(result["high_corr_pairs"], [])
        self.assertEqual(result["redundancy_note"], "low")
        self.assertIsNone(result["avg_corr"])


class BuildGridSuggestionTests(unittest.TestCase):
    """build_grid_suggestion 端到端：schema / 字段 / 全 ASCII / 扁平表。"""

    @staticmethod
    def _market_data() -> dict[str, pd.DataFrame]:
        n = 90
        t = np.arange(n)
        common = 6.0 * np.sin(t * 2 * np.pi / 20)
        rng = np.random.default_rng(123)
        noise = rng.normal(0, 0.3, n)
        dates = pd.date_range("2024-01-02", periods=n, freq="B").strftime("%Y-%m-%d")
        return {
            "A.SZ": pd.DataFrame({"trade_date": dates, "close": 100.0 + common + noise, "vol": 1000.0}),
            "B.SZ": pd.DataFrame({"trade_date": dates, "close": 100.0 + 2.0 * common + 0.5 * noise, "vol": 1000.0}),
        }

    @staticmethod
    def _grid_reference() -> pd.DataFrame:
        return pd.DataFrame([
            {"asset_id": "A.SZ", "vol_rank_60d": 0.6, "suggested_reference_spread": 0.05,
             "cone_60_p95": 0.10, "cone_60_p50": 0.02},
            {"asset_id": "B.SZ", "vol_rank_60d": 0.6, "suggested_reference_spread": 0.05,
             "cone_60_p95": 0.10, "cone_60_p50": 0.02},
        ])

    def test_schema_fields_and_ascii(self) -> None:
        strategy = _make_strategy(("A.SZ", "B.SZ"))
        suggestion = gs.build_grid_suggestion(
            [strategy], self._market_data(), self._grid_reference(), None, "2024-05-31"
        )
        self.assertEqual(suggestion["schema_version"], "grid-suggestion-v2")
        self.assertEqual(suggestion["approval_policy"], "REFERENCE_ONLY")
        self.assertEqual(suggestion["data_asof"], "2024-05-31")
        self.assertEqual(len(suggestion["strategies"]), 1)
        entry = suggestion["strategies"][0]
        self.assertEqual(entry["strategy_id"], "grid_lh")
        self.assertEqual(len(entry["assets"]), 2)
        for asset in entry["assets"]:
            for field in ("asset_id", "suitability", "suitability_breakdown", "anchor_suggestion",
                          "anchor_basis", "anchor_sources", "anchor_stability_score",
                          "anchor_stability_grade", "anchor_references",
                          "regular_spread", "edge_spread", "edge_spread_basis",
                          "spread_basis", "spread_regime", "spread_alternatives",
                          "cost_constraint_applied", "cost_constraint_note",
                          "regular_levels_per_side", "edge_levels_per_side",
                          "spacing_reference", "confidence"):
                self.assertIn(field, asset)
            # 90 行数据 → 20/60/90/120 窗口全有效（V2 多周期几何均值主锚）
            self.assertEqual(asset["anchor_basis"], "geomean_of_4_sources")
            self.assertEqual(asset["regular_levels_per_side"], 3)
            self.assertEqual(asset["edge_levels_per_side"], 1)
            for value in asset.values():
                if isinstance(value, str):
                    self.assertTrue(value.isascii(), f"非 ASCII: {value}")
        self.assertIn("correlation", entry)

    def test_disabled_strategy_skipped(self) -> None:
        strategy = _make_strategy(("A.SZ",))
        disabled = ContractStrategy(
            strategy_id="disabled_strategy", decision_rule="grid", enabled=False,
            use_target_ratio=False, rebalance_frequency="daily",
            rebalance_threshold_abs=0.03, asset_rebalance_threshold_abs=0.02,
            signal_filters=None, preferences={"macro_fit": -0.2}, assets=strategy.assets,
        )
        suggestion = gs.build_grid_suggestion(
            [disabled], self._market_data(), self._grid_reference(), None, "2024-05-31"
        )
        self.assertEqual(len(suggestion["strategies"]), 0)

    def test_incomplete_history_low_confidence(self) -> None:
        strategy = _make_strategy(("A.SZ",))
        data = {"A.SZ": pd.DataFrame({
            "trade_date": pd.date_range("2024-01-02", periods=10, freq="B").strftime("%Y-%m-%d"),
            "close": [100.0 + i for i in range(10)],
            "vol": [1000.0] * 10,
        })}
        suggestion = gs.build_grid_suggestion([strategy], data, self._grid_reference(), None, "2024-01-15")
        asset = suggestion["strategies"][0]["assets"][0]
        self.assertEqual(asset["confidence"], "low")
        self.assertIsNone(asset["anchor_suggestion"])
        self.assertIsNone(asset["regular_spread"])
        self.assertIsNone(asset["suitability_score"])
        self.assertEqual(asset["suitability"], "not_suitable")

    def test_shared_grid_overrides_defaults(self) -> None:
        strategy = _make_strategy(("A.SZ",))
        shared_grid = {"regular_levels_per_side": 5, "edge_levels_per_side": 2}
        suggestion = gs.build_grid_suggestion(
            [strategy], self._market_data(), self._grid_reference(), shared_grid, "2024-05-31"
        )
        asset = suggestion["strategies"][0]["assets"][0]
        self.assertEqual(asset["regular_levels_per_side"], 5)
        self.assertEqual(asset["edge_levels_per_side"], 2)

    def test_flat_table(self) -> None:
        strategy = _make_strategy(("A.SZ", "B.SZ"))
        suggestion = gs.build_grid_suggestion(
            [strategy], self._market_data(), self._grid_reference(), None, "2024-05-31"
        )
        table = gs.build_grid_suggestion_table(suggestion)
        self.assertEqual(len(table), 2)
        expected_cols = {
            "strategy_id", "asset_id", "suitability_score", "suitability",
            "vol_rank_score", "amplitude_score", "drift_score", "trigger_freq_score",
            "anchor_suggestion", "anchor_basis", "anchor_sources", "anchor_stability_score",
            "anchor_stability_grade", "anchor_references", "regular_spread", "edge_spread",
            "edge_spread_basis", "spread_basis", "spread_regime", "spread_alternatives",
            "cost_constraint_applied", "cost_constraint_note",
            "regular_levels_per_side", "edge_levels_per_side",
            "spacing_reference_default", "spacing_reference_min", "spacing_reference_max",
            "confidence",
            # 现有网格口径（theoretical_profit_actual）加性列
            "theory_actual_degraded", "theory_actual_round_profit", "theory_actual_n_annual",
            "theory_actual_annual_accounting_profit", "theory_actual_theory_max",
            "theory_actual_cost_breakeven", "theory_actual_levels_total",
        }
        self.assertEqual(set(table.columns), expected_cols)
        self.assertEqual(table.loc[0, "strategy_id"], "grid_lh")
        self.assertEqual(table.loc[0, "anchor_basis"], "geomean_of_4_sources")
        self.assertTrue(table["edge_spread_basis"].notna().all())

    @staticmethod
    def _non_grid_strategy(sid: str, rule: str, bucket: str) -> ContractStrategy:
        """非网格策略（无启用标的），供 6 策略混合契约过滤测试。"""
        return ContractStrategy(
            strategy_id=sid, decision_rule=rule, enabled=True,
            use_target_ratio=True, rebalance_frequency="weekly",
            rebalance_threshold_abs=0.03, asset_rebalance_threshold_abs=0.05,
            signal_filters=None, preferences={}, assets=[],
            capital_bucket=bucket,
        )

    def test_filters_non_grid_via_capital_bucket(self) -> None:
        """契约含 6 策略（含非 grid），只产出 capital_bucket==grid 的 grid_lh/grid_scz。"""
        strategies = [
            self._non_grid_strategy("barbell_strategy", "barbell", "core"),
            self._non_grid_strategy("global_allocation", "mid_line", "core"),
            self._non_grid_strategy("three_musketeers", "mid_line", "core"),
            self._non_grid_strategy("short_stock_placeholder", "short_term", "tactical"),
            _make_strategy(("A.SZ", "B.SZ"), strategy_id="grid_lh", capital_bucket="grid"),
            _make_strategy(("A.SZ", "B.SZ"), strategy_id="grid_scz", capital_bucket="grid"),
        ]
        suggestion = gs.build_grid_suggestion(
            strategies, self._market_data(), self._grid_reference(), None, "2024-05-31"
        )
        emitted = [s["strategy_id"] for s in suggestion["strategies"]]
        self.assertEqual(emitted, ["grid_lh", "grid_scz"])
        # 每个网格策略的标的建议正常产出（2 标尺 × 2 策略）。
        for entry in suggestion["strategies"]:
            self.assertEqual(len(entry["assets"]), 2)

    def test_filters_non_grid_fallback_decision_rule(self) -> None:
        """契约无 capital_bucket 字段（None）时回退 decision_rule==grid，非网格仍被剔除。"""
        non_grid = ContractStrategy(
            strategy_id="barbell_strategy", decision_rule="barbell", enabled=True,
            use_target_ratio=True, rebalance_frequency="weekly",
            rebalance_threshold_abs=0.03, asset_rebalance_threshold_abs=0.05,
            signal_filters=None, preferences={}, assets=[],
            # capital_bucket 不传 → None（旧契约/测试 fixture 场景）。
        )
        suggestion = gs.build_grid_suggestion(
            [non_grid, _make_strategy(("A.SZ",), strategy_id="grid_lh")],
            self._market_data(), self._grid_reference(), None, "2024-05-31",
        )
        emitted = [s["strategy_id"] for s in suggestion["strategies"]]
        self.assertEqual(emitted, ["grid_lh"])

    def test_grid_strategies_without_capital_bucket_still_emitted(self) -> None:
        """既有网格策略 fixture（无 capital_bucket）通过回退仍正常产出（不回归）。"""
        strategy = _make_strategy(("A.SZ", "B.SZ"))
        suggestion = gs.build_grid_suggestion(
            [strategy], self._market_data(), self._grid_reference(), None, "2024-05-31"
        )
        self.assertEqual([s["strategy_id"] for s in suggestion["strategies"]], ["grid_lh"])
        self.assertEqual(len(suggestion["strategies"][0]["assets"]), 2)


if __name__ == "__main__":
    unittest.main()
