"""grid_recommendation：网格推荐组合（B2/B3）单元测试。

覆盖：3 套方案标的数区间 + avg_corr<cap、间距参考 clamp 边界/历史不足 None、
theoretical_max_inventory 公式、确定性、缺 account_total_capital 降级、
manifest 独立指针（绝不顶日度 newest_run / newest_grid_suggestion_run）。

纯 unittest + 合成 fixture（14 标的 90d 日线），不碰真实 A 契约。
运行：``cd research_engines/qteasy_lab && .venv/Scripts/python.exe -B -m unittest discover -s "D:/Project DPS/tests" -q``
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from qteasy_research.reference import grid_suggestion as gs
from qteasy_research.reference.backtest_engine import ContractAsset, ContractStrategy
from qteasy_research.reference.config import (
    GRID_RECOMMENDATION_CADENCE,
    GRID_RECOMMENDATION_SCHEMA_VERSION,
    GRID_RECOMMENDATION_SUBDIR,
    GRID_SUGGESTION_SUBDIR,
)
from qteasy_research.reference.grid_recommendation import (
    build_grid_recommendation,
    capital_metrics,
    spacing_reference_triplet,
)
from qteasy_research.reference.shared_dir import IntegrationDir


# ---------------------------------------------------------------------------
# 合成 fixture
# ---------------------------------------------------------------------------

def _make_strategy(
    strategy_id: str,
    asset_ids: tuple[str, ...],
    weight: float = 0.0,
    risk_budget: float | None = None,
    enabled: bool = True,
    decision_rule: str = "grid",
) -> ContractStrategy:
    assets = [
        ContractAsset(
            asset_id=a, role="grid", enabled=True,
            min_weight=0.0, target_weight=None, max_weight=0.2,
            target_weight_configured=False,
        )
        for a in asset_ids
    ]
    return ContractStrategy(
        strategy_id=strategy_id, decision_rule=decision_rule, enabled=enabled,
        use_target_ratio=False, rebalance_frequency="daily",
        rebalance_threshold_abs=0.03, asset_rebalance_threshold_abs=0.02,
        signal_filters=None, preferences={"macro_fit": -0.2}, assets=assets,
        target_capital_weight=weight, risk_budget=risk_budget,
    )


def _market_data_14(n: int = 90, seed: int = 123) -> dict[str, pd.DataFrame]:
    """14 标的 90d 日线：4 簇强内聚（簇内 corr≈0.9）、簇间近 0。

    高/低 = close×(1±0.02)（ATR≈4%、间距参考 default 封顶 0.05）。
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    dates = pd.date_range("2024-01-02", periods=n, freq="B").strftime("%Y-%m-%d")
    common = 0.5 * np.sin(t * 2 * np.pi / 20)  # 全局共同因子（簇间微弱相关）
    clusters = [("A", 4), ("B", 4), ("C", 3), ("D", 3)]
    data: dict[str, pd.DataFrame] = {}
    for ci, (prefix, size) in enumerate(clusters):
        period = 22 + ci * 3
        phase = ci * 1.3
        cluster_factor = 6.0 * np.sin(t * 2 * np.pi / period + phase)
        for j in range(size):
            noise = rng.normal(0, 0.8, n)
            close = 100.0 + common + cluster_factor + noise
            asset_id = f"{prefix}{j}.SZ"
            data[asset_id] = pd.DataFrame({
                "trade_date": dates,
                "close": close,
                "high": close * 1.02,
                "low": close * 0.98,
                "vol": 1000.0,
            })
    return data


def _asset_ids_14() -> list[str]:
    return sorted(_market_data_14().keys())


def _grid_reference_14(asset_ids: list[str]) -> pd.DataFrame:
    return pd.DataFrame([
        {"asset_id": aid, "vol_rank_60d": 0.6, "suggested_reference_spread": 0.05,
         "cone_60_p95": 0.10, "cone_60_p50": 0.02}
        for aid in asset_ids
    ])


def _strategies_14() -> list[ContractStrategy]:
    ids = _asset_ids_14()
    # 两个网格策略（grid_lh 8 标的 / grid_scz 6 标的），并集=全池 14。
    lh_ids = tuple(ids[:8])
    scz_ids = tuple(ids[8:])
    return [
        _make_strategy("grid_lh", lh_ids, weight=0.1970, risk_budget=1.70),
        _make_strategy("grid_scz", scz_ids, weight=0.1212, risk_budget=None),
    ]


def _shared_grid(account_total_capital: float | None = 2_000_000.0) -> dict:
    grid = {"window_days": 60}
    if account_total_capital is not None:
        grid["account_total_capital"] = account_total_capital
    return grid


def _build(shared_grid: dict | None = None) -> dict:
    return build_grid_recommendation(
        _strategies_14(),
        _market_data_14(),
        _grid_reference_14(_asset_ids_14()),
        None,
        shared_grid,
        "2024-04-30",
    )


def _plan(reco: dict, plan_id: str) -> dict:
    for plan in reco["plans"]:
        if plan["plan_id"] == plan_id:
            return plan
    raise KeyError(plan_id)


# ---------------------------------------------------------------------------
# B1：间距参考三元组（clamp 边界 / 历史不足）
# ---------------------------------------------------------------------------

class SpacingReferenceTests(unittest.TestCase):
    @staticmethod
    def _frame(close_series, delta: float) -> pd.DataFrame:
        n = len(close_series)
        return pd.DataFrame({
            "trade_date": pd.date_range("2024-01-02", periods=n, freq="B").strftime("%Y-%m-%d"),
            "close": np.asarray(close_series, dtype=float),
            "high": np.asarray(close_series, dtype=float) * (1 + delta),
            "low": np.asarray(close_series, dtype=float) * (1 - delta),
        })

    def test_clamp_upper_bound(self) -> None:
        # V2：default=ATR 主公式，ATR≈4%（delta=0.02）→ clamp(6%, cost_floor, cap=5%)=5%。
        frame = self._frame([100.0] * 60, delta=0.02)
        triplet = spacing_reference_triplet(frame, {}, gs._grid_params(None))
        self.assertEqual(triplet["default"], 0.05)
        self.assertEqual(triplet["min"], 0.035)
        self.assertEqual(triplet["max"], 0.075)
        self.assertEqual(triplet["basis"], "atr20_regime")
        self.assertEqual(triplet["confidence"], "high")

    def test_clamp_lower_bound(self) -> None:
        # V2：ATR≈1%（delta=0.005）→ default=0.015（ATR 线性 ×1.5，无旧固定 2.5% 下限）。
        frame = self._frame([100.0] * 60, delta=0.005)
        triplet = spacing_reference_triplet(frame, {}, gs._grid_params(None))
        self.assertEqual(triplet["default"], 0.015)
        self.assertEqual(triplet["min"], 0.0105)
        self.assertEqual(triplet["max"], 0.0225)
        self.assertEqual(triplet["basis"], "atr20_regime")

    def test_clamp_mid_range_formula(self) -> None:
        # V2：ATR≈2%（delta=0.01）→ default=0.03、min=0.021、max=0.045。
        frame = self._frame([100.0] * 60, delta=0.01)
        triplet = spacing_reference_triplet(frame, {}, gs._grid_params(None))
        self.assertEqual(triplet["default"], 0.03)
        self.assertEqual(triplet["min"], 0.021)
        self.assertEqual(triplet["max"], 0.045)
        self.assertEqual(triplet["basis"], "atr20_regime")
        self.assertEqual(triplet["confidence"], "high")

    def test_insufficient_history_low_confidence(self) -> None:
        # 不足 20 日 → 三元组 None + confidence=low（不虚构）。
        frame = self._frame([100.0] * 10, delta=0.02)
        triplet = spacing_reference_triplet(frame, {}, gs._grid_params(None))
        self.assertIsNone(triplet["default"])
        self.assertIsNone(triplet["min"])
        self.assertIsNone(triplet["max"])
        self.assertIsNone(triplet["basis"])
        self.assertEqual(triplet["confidence"], "low")


# ---------------------------------------------------------------------------
# B2：组合生成端到端
# ---------------------------------------------------------------------------

class BuildGridRecommendationTests(unittest.TestCase):
    def test_schema_and_ascii(self) -> None:
        reco = _build()
        self.assertEqual(reco["schema_version"], "grid-recommendation-v1")
        self.assertEqual(reco["approval_policy"], "REFERENCE_ONLY")
        self.assertEqual(reco["cadence"], "weekly")
        self.assertEqual(reco["data_asof"], "2024-04-30")
        self.assertEqual(reco["universe"]["pool"], _asset_ids_14())
        self.assertEqual(len(reco["universe"]["enabled_grid_assets"]), 14)
        self.assertEqual(len(reco["strategies"]), 2)
        self.assertEqual(len(reco["plans"]), 3)
        self.assertEqual(len(reco["spacing_reference"]), 14)
        for plan in reco["plans"]:
            self.assertIn(plan["plan_id"], ("conservative", "balanced", "aggressive"))
            self.assertTrue(plan["plan_id"].isascii())
        for strategy in reco["strategies"]:
            self.assertTrue(strategy["strategy_id"].isascii())
        self.assertEqual(reco["risk_annotation"]["risk_budget"], {"grid_lh": 1.70})
        self.assertIn("risk_budget_usage 由 A 风控管线估算", reco["risk_annotation"]["note"])

    def test_three_plans_asset_ranges_and_corr_cap(self) -> None:
        reco = _build()
        expected_ranges = {
            "conservative": (4, 5),
            "balanced": (6, 7),
            "aggressive": (8, 10),
        }
        expected_cap = {
            "conservative": 0.3,
            "balanced": 0.4,
            "aggressive": 0.5,
        }
        for plan_id, (lo, hi) in expected_ranges.items():
            plan = _plan(reco, plan_id)
            n = plan["metrics"]["n_assets"]
            self.assertGreaterEqual(n, lo, f"{plan_id} 低于区间下界")
            self.assertLessEqual(n, hi, f"{plan_id} 高于区间上界")
            self.assertLess(
                plan["metrics"]["avg_corr"], expected_cap[plan_id],
                f"{plan_id} avg_corr 应 < corr_cap",
            )
            self.assertEqual(len(plan["assets"]), n)
            self.assertEqual(plan["assets"][0]["regular_levels_per_side"], 3)
            self.assertEqual(plan["assets"][0]["edge_levels_per_side"], 1)
            for asset in plan["assets"]:
                self.assertIn("spacing_reference", asset)
                self.assertIsNotNone(asset["spacing_reference"]["default"])

    def test_determinism(self) -> None:
        reco_a = _build()
        reco_b = _build()
        for plan_a, plan_b in zip(reco_a["plans"], reco_b["plans"]):
            ids_a = [a["asset_id"] for a in plan_a["assets"]]
            ids_b = [a["asset_id"] for a in plan_b["assets"]]
            self.assertEqual(ids_a, ids_b)

    def test_suggestion_reused_when_provided(self) -> None:
        suggestion = gs.build_grid_suggestion(
            _strategies_14(), _market_data_14(), _grid_reference_14(_asset_ids_14()),
            _shared_grid(), "2024-04-30",
        )
        reco = build_grid_recommendation(
            _strategies_14(), _market_data_14(), _grid_reference_14(_asset_ids_14()),
            suggestion, _shared_grid(), "2024-04-30",
        )
        self.assertEqual(len(reco["plans"]), 3)
        for plan in reco["plans"]:
            for asset in plan["assets"]:
                self.assertIn("is_currently_enabled", asset)

    def test_capital_formula_with_budget(self) -> None:
        reco = _build(_shared_grid(account_total_capital=2_000_000.0))
        plan = _plan(reco, "balanced")
        n = plan["metrics"]["n_assets"]
        amount = 6000.0
        levels = 9
        expected_inventory = round(amount * levels * n, 4)
        self.assertEqual(plan["capital"]["theoretical_max_inventory"], expected_inventory)
        self.assertEqual(plan["capital"]["inventory_formula"], "amount_per_grid * levels_total * n_assets")
        per = plan["capital"]["per_strategy"]["grid_lh"]
        grid_budget = round(2_000_000.0 * 0.1970, 4)
        self.assertEqual(per["grid_budget"], grid_budget)
        self.assertEqual(per["max_amount_per_grid_within_budget"],
                         round(grid_budget / (levels * n), 4))
        self.assertEqual(reco["capital_params"]["account_total_capital"], 2_000_000.0)

    def test_missing_account_total_capital_degrades(self) -> None:
        reco = _build(_shared_grid(account_total_capital=None))
        self.assertIsNone(reco["capital_params"]["account_total_capital"])
        self.assertTrue(any("account_total_capital" in w for w in reco["warnings"]))
        plan = _plan(reco, "balanced")
        per = plan["capital"]["per_strategy"]["grid_lh"]
        self.assertIsNone(per["grid_budget"])
        self.assertIsNone(per["utilization"])
        self.assertIsNone(per["max_amount_per_grid_within_budget"])
        # 理论最大库存仍需给出（不依赖账户资金）。
        self.assertEqual(plan["capital"]["theoretical_max_inventory"],
                         round(6000.0 * 9 * plan["metrics"]["n_assets"], 4))

    def test_greedy_stops_at_corr_cap(self) -> None:
        # 直接测 _select_plan：C 与 B 强相关（0.9），加入 C 后 avg=(0.1+0.1+0.9)/3≈0.37
        # 超 corr_cap=0.3 → 停于 [A, B]。
        corr = {
            "A": {"A": 1.0, "B": 0.1, "C": 0.1},
            "B": {"A": 0.1, "B": 1.0, "C": 0.9},
            "C": {"A": 0.1, "B": 0.9, "C": 1.0},
        }
        from qteasy_research.reference.grid_recommendation import _select_plan
        plan = _select_plan(["A", "B", "C"], corr, 2, 3, 0.3, rank=["A", "B", "C"])
        self.assertEqual(plan, ["A", "B"])

    def test_capital_metrics_pure(self) -> None:
        metrics = capital_metrics(["A.SZ", "B.SZ"], 1_000_000.0, {"grid_lh": 0.5}, {})
        self.assertEqual(metrics["theoretical_max_inventory"], round(6000.0 * 9 * 2, 4))
        self.assertEqual(metrics["per_strategy"]["grid_lh"]["grid_budget"], 500_000.0)
        self.assertEqual(metrics["per_strategy"]["grid_lh"]["max_amount_per_grid_within_budget"],
                         round(500_000.0 / (9 * 2), 4))


# ---------------------------------------------------------------------------
# B3：manifest 独立指针（绝不顶日度 newest_run / newest_grid_suggestion_run）
# ---------------------------------------------------------------------------

class ManifestIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.integration = IntegrationDir(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _daily(self, run_id: str) -> None:
        self.integration.write_run(
            run_id,
            {"decision_ref_package.json": {"x": 1}},
            data_asof=f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:]}",
            generated_date=f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:]}",
        )

    def _publish_reco(self, run_id: str) -> None:
        src = self.root / f"src_reco_{run_id}"
        src.mkdir(parents=True, exist_ok=True)
        (src / "grid_recommendations.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
        self.integration.publish_run(
            run_id, src, subdir=GRID_RECOMMENDATION_SUBDIR,
            data_asof=f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:]}",
            generated_date=f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:]}",
            schema_version=GRID_RECOMMENDATION_SCHEMA_VERSION,
            cadence=GRID_RECOMMENDATION_CADENCE,
            package_kind=GRID_RECOMMENDATION_SUBDIR,
        )

    def _publish_gs(self, run_id: str) -> None:
        src = self.root / f"src_gs_{run_id}"
        src.mkdir(parents=True, exist_ok=True)
        (src / "grid_suggestion.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
        self.integration.publish_run(
            run_id, src, subdir=GRID_SUGGESTION_SUBDIR,
            data_asof=f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:]}",
            generated_date=f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:]}",
            schema_version="grid-suggestion-v1", package_kind=GRID_SUGGESTION_SUBDIR,
        )

    def test_recommendation_pointer_isolated_from_daily(self) -> None:
        self._daily("20260813")
        self._publish_reco("20260814")
        manifest = self.integration.get_manifest()
        self.assertEqual(manifest["newest_run"], "20260813")
        self.assertEqual(manifest["newest_grid_recommendation_run"], "20260814")

    def test_grid_suggestion_and_recommendation_independent(self) -> None:
        self._daily("20260812")
        self._publish_gs("20260813")
        self._publish_reco("20260814")
        manifest = self.integration.get_manifest()
        self.assertEqual(manifest["newest_run"], "20260812")
        self.assertEqual(manifest["newest_grid_suggestion_run"], "20260813")
        self.assertEqual(manifest["newest_grid_recommendation_run"], "20260814")

    def test_prune_recomputes_recommendation_pointer(self) -> None:
        self._publish_reco("20260813")
        self._publish_reco("20260814")
        self.integration._prune_manifest(["20260813"])
        manifest = self.integration.get_manifest()
        self.assertEqual(manifest["newest_grid_recommendation_run"], "20260814")
        self.assertNotIn("20260813", manifest["runs"])


if __name__ == "__main__":
    unittest.main()
