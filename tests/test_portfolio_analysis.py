"""L3 组合分析（reference/portfolio_analysis）测试。

纯 unittest + tempfile + 合成数据；three_musketeers 真实冒烟用 ``skipUnless``
（真实数据缺失自动跳过）。运行方式（从 qteasy_lab 目录）：

  .\\.venv\\Scripts\\python.exe -B -m unittest discover -s "D:\\Project DPS\\tests" -q
"""

from __future__ import annotations

import json
import math
import re
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from qteasy_research.reference import portfolio_analysis as pa
from qteasy_research.reference.backtest_engine import (
    ContractAsset,
    ContractStrategy,
    grid_target_weight,
)

_ASCII = re.compile(r"^[\x00-\x7F]*$")

# qteasy_lab 根目录（tests/ 的上级 D:\\Project DPS\\research_engines\\qteasy_lab）。
_QTEASY_LAB = Path(__file__).resolve().parents[1] / "research_engines" / "qteasy_lab"
_REAL_DATA_OK = (
    (_QTEASY_LAB / "data" / "fund_daily.csv").exists()
    and (_QTEASY_LAB / "research_store" / "factor_values" / "momentum_60d.parquet").exists()
    and Path(r"D:\FF Project\data\integration\strategy_contracts\strategy_contract.json").exists()
)


def _synthetic_data(n_assets: int = 12, n_days: int = 400, seed: int = 7, n_factors: int = 2):
    """合成行情 + 因子面板（全资产×全因子），供离线测试。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    universe = [f"ASSET{i:02d}" for i in range(n_assets)]
    close = pd.DataFrame(
        100.0 + np.cumsum(rng.normal(0.0, 1.0, (n_days, n_assets)), axis=0),
        index=dates,
        columns=universe,
    )
    close = close.abs() + 10.0
    factor_panels: dict[str, pd.DataFrame] = {}
    for k in range(n_factors):
        factor_panels[f"factor_{k}"] = pd.DataFrame(
            rng.normal(0.0, 1.0, (n_days, n_assets)), index=dates, columns=universe
        )
    return close, factor_panels, universe


_DEFAULT_WEIGHTS = {"ASSET00": 0.4, "ASSET01": 0.3, "ASSET02": 0.3}


class ExposureMatrixTests(unittest.TestCase):
    def test_x_beta_reproduces_ols(self) -> None:
        """X 单格 = 对齐后最近 window 的 OLS rolling beta（cov/var）。"""
        close, panels, universe = _synthetic_data()
        factor_id = "factor_0"
        X, report = pa.assemble_exposure_matrix(
            close, {factor_id: panels[factor_id]}, window=252, min_window=120
        )
        self.assertEqual(X.shape[0], len(universe))
        self.assertEqual(X.shape[1], 1)
        asset = "ASSET00"
        daily_ret = close.pct_change()
        frame = pd.concat(
            [panels[factor_id][asset].rename("factor"), daily_ret[asset].rename("return")],
            axis=1,
        ).replace([np.inf, -np.inf], np.nan).dropna()
        frame = frame.iloc[-252:]
        expected = float(
            frame["factor"].cov(frame["return"]) / frame["factor"].var(ddof=1)
        )
        self.assertTrue(np.isfinite(float(X.loc[asset, factor_id])))
        self.assertAlmostEqual(float(X.loc[asset, factor_id]), expected, places=6)
        self.assertEqual(report["dropped_factors"], [])
        self.assertEqual(report["dropped_assets"], [])

    def test_x_insufficient_samples_nan_warning(self) -> None:
        """不足样本（< min_window）→ NaN + warning，不虚构。"""
        close, panels, universe = _synthetic_data()
        short = panels["factor_1"].copy()
        short.loc[close.index[60:], "ASSET01"] = np.nan  # 仅前 60 天有因子 → 不足 120
        panels2 = {"factor_0": panels["factor_0"], "factor_1": short}
        X, report = pa.assemble_exposure_matrix(
            close, panels2, window=252, min_window=120
        )
        self.assertTrue(math.isnan(X.loc["ASSET01", "factor_1"]))
        self.assertTrue(
            any("valid samples" in warning and "min" in warning for warning in report["warnings"])
        )
        # ASSET01 仍有 factor_0 有效 → 整行保留
        self.assertIn("ASSET01", X.index)

    def test_x_row_dropped_quality_d(self) -> None:
        """全因子 NaN 的资产整行剔除 → quality_level=D。"""
        close, panels, universe = _synthetic_data()
        panels2 = {k: v.copy() for k, v in panels.items()}
        for panel in panels2.values():
            panel.loc[:, "ASSET02"] = np.nan
        X, report = pa.assemble_exposure_matrix(
            close, panels2, window=252, min_window=120
        )
        self.assertNotIn("ASSET02", X.index)
        self.assertIn("ASSET02", report["dropped_assets"])
        self.assertEqual(report["quality_levels"]["ASSET02"], "D")

    def test_x_factor_column_dropped(self) -> None:
        """有效资产 < 3 的因子列整列剔除（不虚构）。"""
        close, panels, universe = _synthetic_data()
        bad = pd.DataFrame(np.nan, index=close.index, columns=universe)
        for asset in universe[:2]:
            bad[asset] = panels["factor_0"][asset]
        panels2 = {
            "factor_0": panels["factor_0"],
            "bad_factor": bad,
            "factor_1": panels["factor_1"],
        }
        X, report = pa.assemble_exposure_matrix(
            close, panels2, window=252, min_window=120
        )
        self.assertNotIn("bad_factor", X.columns)
        self.assertIn("bad_factor", report["dropped_factors"])


class SigmaTests(unittest.TestCase):
    def test_sigma_f_ledoit_wolf_psd(self) -> None:
        """Σf 主方案 = Ledoit-Wolf：PSD、shrinkage ∈ [0,1]、与 sample 同形状。"""
        close, panels, universe = _synthetic_data()
        factor_returns = pa.build_price_factor_returns(close, panels)
        result = pa.factor_covariance(factor_returns)
        lw = result["ledoit_wolf"]
        eigvals = np.linalg.eigvalsh(lw.to_numpy())
        self.assertGreaterEqual(eigvals.min(), -1e-10)
        self.assertGreaterEqual(result["shrinkage"], 0.0)
        self.assertLessEqual(result["shrinkage"], 1.0)
        self.assertEqual(lw.shape, result["sample"].shape)
        self.assertEqual(lw.shape, result["ewma"].shape)
        self.assertEqual(result["method"], "ledoit_wolf")

    def test_idio_var_floor(self) -> None:
        """Σε 特异方差带 floor：低于 floor 的残差方差被抬升到 floor，不虚构为 0。"""
        close, panels, universe = _synthetic_data()
        factor_returns = pa.build_price_factor_returns(close, panels)
        X, _ = pa.assemble_exposure_matrix(close, panels)
        # floor 高于真实残差方差 → 全部被抬升到 floor。
        idio_high = pa.idiosyncratic_variance(close, X, factor_returns, floor=5.0)
        self.assertTrue((idio_high["idio_var"] >= 5.0 - 1e-12).all())
        # 默认 floor（1e-8）远低于真实残差方差 → 原样保留（> floor）。
        idio_default = pa.idiosyncratic_variance(close, X, factor_returns)
        self.assertTrue((idio_default["idio_var"] > 1e-6).any())

    def test_sigma_formula_reproduction(self) -> None:
        """Σ = B Σf B' + diag(σ²ε)，与手算 numpy 一致。"""
        close, panels, universe = _synthetic_data()
        factor_returns = pa.build_price_factor_returns(close, panels)
        fc = pa.factor_covariance(factor_returns)["ledoit_wolf"]
        X, _ = pa.assemble_exposure_matrix(close, panels)
        idio = pa.idiosyncratic_variance(close, X, factor_returns)
        cov = pa.factor_model_cov(X, fc, idio)
        B = X.reindex(columns=fc.columns).fillna(0.0).to_numpy(dtype=float)
        expected = B @ fc.to_numpy() @ B.T + np.diag(
            idio.reindex(X.index)["idio_var"].fillna(0.0).to_numpy()
        )
        np.testing.assert_allclose(cov.to_numpy(), expected, rtol=1e-10, atol=1e-14)


class PortfolioMetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.close, cls.panels, cls.universe = _synthetic_data()
        cls.result = pa.analyze_portfolio(
            dict(_DEFAULT_WEIGHTS), cls.close, cls.panels
        )

    def test_asset_rc_sums_to_1(self) -> None:
        self.assertAlmostEqual(
            float(self.result["asset_risk_contributions"]["rc"].sum()), 1.0, places=8
        )

    def test_factor_rc_sums_to_1(self) -> None:
        self.assertAlmostEqual(
            float(self.result["factor_risk_contributions"]["rc"].sum()), 1.0, places=8
        )

    def test_l4_exposure_reproduction(self) -> None:
        """g = w'X：组合因子暴露 = 各资产权重×暴露之和。"""
        X, _ = pa.assemble_exposure_matrix(self.close, self.panels)
        exposure = pa.portfolio_factor_exposure(_DEFAULT_WEIGHTS, X)
        expected = np.zeros(len(X.columns), dtype=float)
        for asset, weight in _DEFAULT_WEIGHTS.items():
            expected += weight * X.loc[asset].fillna(0.0).to_numpy()
        np.testing.assert_allclose(
            exposure["exposure"].to_numpy(), expected, rtol=1e-10, atol=1e-14
        )

    def test_weights_normalized(self) -> None:
        self.assertAlmostEqual(sum(self.result["weights"].values()), 1.0, places=10)

    def test_vol_three_estimates_present(self) -> None:
        portfolio = self.result["portfolio"]
        self.assertTrue(np.isfinite(portfolio["annual_vol_factor_model"]))
        self.assertTrue(np.isfinite(portfolio["annual_vol_sample"]))
        self.assertTrue(np.isfinite(portfolio["annual_vol_ewma"]))


class ProportionTests(unittest.TestCase):
    def test_risk_parity_2_asset_equal_vol(self) -> None:
        """对角等方差 → 风险平价 0.5/0.5。"""
        cov = pd.DataFrame(np.diag([1.0, 1.0]), index=["A", "B"], columns=["A", "B"])
        rp = pa.risk_parity_weights(cov, method="fixed_point")
        self.assertEqual(rp["status"], "ok")
        self.assertEqual(rp["method"], "fixed_point")
        self.assertAlmostEqual(rp["weights"]["A"], 0.5, places=6)
        self.assertAlmostEqual(rp["weights"]["B"], 0.5, places=6)

    def test_risk_parity_bounds_effective(self) -> None:
        """高波动资产 B（vol=2）→ 低权重；A 权重被 max_weight 上限夹住。"""
        cov = pd.DataFrame(np.diag([1.0, 4.0]), index=["A", "B"], columns=["A", "B"])
        rp = pa.risk_parity_weights(
            cov, method="fixed_point", bounds={"A": (0.0, 0.25), "B": (0.0, 1.0)}
        )
        self.assertLessEqual(rp["weights"]["A"], 0.25 + 1e-9)
        # 风险平价解（无界）A≈0.667 → 被 0.25 夹住后 B≈0.75
        self.assertAlmostEqual(rp["weights"]["A"] + rp["weights"]["B"], 1.0, places=8)

    def test_inverse_vol_equal_vol_50_50(self) -> None:
        cov = pd.DataFrame(np.diag([2.0, 2.0]), index=["A", "B"], columns=["A", "B"])
        iv = pa.inverse_vol_weights(cov)
        self.assertAlmostEqual(iv["weights"]["A"], 0.5, places=6)

    def test_no_scipy_degradation(self) -> None:
        """无 scipy → 风险平价走固定点、有效前沿诚实省略（degraded, 0 点）。"""
        close, panels, universe = _synthetic_data()
        old = pa.SCIPY_AVAILABLE
        pa.SCIPY_AVAILABLE = False
        try:
            result = pa.analyze_portfolio(
                dict(_DEFAULT_WEIGHTS), close, panels
            )
            rp = result["proportions"]["risk_parity"]
            self.assertEqual(rp["method"], "fixed_point")
            self.assertEqual(rp["status"], "ok")
            frontier = result["proportions"]["efficient_frontier"]
            self.assertEqual(frontier["status"], "degraded")
            self.assertEqual(frontier["points"], [])
            self.assertTrue(
                any("scipy unavailable" in warning for warning in result["warnings"])
            )
        finally:
            pa.SCIPY_AVAILABLE = old

    def test_efficient_frontier_degraded_without_scipy(self) -> None:
        cov = pd.DataFrame(np.diag([1.0, 1.0]), index=["A", "B"], columns=["A", "B"])
        mu = {"A": 0.1, "B": 0.2}
        old = pa.SCIPY_AVAILABLE
        pa.SCIPY_AVAILABLE = False
        try:
            frontier = pa.efficient_frontier(mu, cov)
            self.assertEqual(frontier["status"], "degraded")
            self.assertEqual(frontier["points"], [])
            self.assertTrue(frontier["warning"])
        finally:
            pa.SCIPY_AVAILABLE = old


class StrategyInputTests(unittest.TestCase):
    def test_strategy_analysis_inputs_grid(self) -> None:
        """grid：target_weight_configured=false → 用 grid_target_weight = max×0.5。"""
        grid = ContractStrategy(
            strategy_id="grid_lh",
            decision_rule="grid",
            enabled=True,
            use_target_ratio=False,
            rebalance_frequency="weekly",
            rebalance_threshold_abs=0.0,
            asset_rebalance_threshold_abs=0.0,
            signal_filters=None,
            preferences={},
            assets=[
                ContractAsset("512890.SH", "defensive", True, 0.1, None, 0.4, False),
                ContractAsset("518880.SH", "gold", True, 0.1, None, 0.5, False),
            ],
        )
        inputs = pa.strategy_analysis_inputs(grid)
        self.assertEqual(inputs["source"], "grid_target")
        self.assertAlmostEqual(inputs["weights"]["512890.SH"], grid_target_weight(grid.assets[0]))
        self.assertAlmostEqual(inputs["weights"]["518880.SH"], grid_target_weight(grid.assets[1]))
        self.assertEqual(inputs["bounds"]["512890.SH"], (0.1, 0.4))
        self.assertIsNone(inputs["error"])

    def test_strategy_analysis_inputs_missing_target_excluded(self) -> None:
        """mid_line：target_weight_configured=false 的资产不入权重（不虚构）。"""
        mid = ContractStrategy(
            strategy_id="mid_line",
            decision_rule="mid_line",
            enabled=True,
            use_target_ratio=False,
            rebalance_frequency="weekly",
            rebalance_threshold_abs=0.0,
            asset_rebalance_threshold_abs=0.0,
            signal_filters=None,
            preferences={},
            assets=[
                ContractAsset("A", "risk", True, 0.0, None, 1.0, False),
                ContractAsset("B", "risk", True, 0.1, 0.6, 0.8, True),
            ],
        )
        inputs = pa.strategy_analysis_inputs(mid)
        self.assertEqual(inputs["source"], "contract_target")
        self.assertNotIn("A", inputs["weights"])
        self.assertAlmostEqual(inputs["weights"]["B"], 0.6)
        self.assertEqual(inputs["bounds"]["B"], (0.1, 0.8))


class OutputTests(unittest.TestCase):
    def test_summary_md_ascii(self) -> None:
        close, panels, universe = _synthetic_data()
        result = pa.analyze_portfolio(dict(_DEFAULT_WEIGHTS), close, panels)
        md = pa.render_summary_md(result)
        self.assertTrue(_ASCII.match(md), "summary.md 必须全 ASCII")
        self.assertIn("portfolio-analysis-v1", md)
        self.assertIn("REFERENCE_ONLY", md)

    def test_write_outputs_all_ascii(self) -> None:
        close, panels, universe = _synthetic_data()
        result = pa.analyze_portfolio(
            dict(_DEFAULT_WEIGHTS), close, panels, run_id="test_run"
        )
        with tempfile.TemporaryDirectory() as tmp:
            paths = pa.write_outputs(result, Path(tmp))
            self.assertIn("summary.md", paths)
            self.assertIn("portfolio_analysis.json", paths)
            self.assertIn("exposure_matrix.csv", paths)
            self.assertIn("factor_cov.csv", paths)
            self.assertIn("correlation_matrix.csv", paths)
            self.assertIn("risk_contributions.csv", paths)
            self.assertIn("risk_parity_weights.csv", paths)
            for path in paths.values():
                path.read_bytes().decode("ascii")  # 任何非 ASCII 字节都会抛错

    def test_json_schema_reference_only(self) -> None:
        close, panels, universe = _synthetic_data()
        result = pa.analyze_portfolio(dict(_DEFAULT_WEIGHTS), close, panels)
        payload = pa.result_to_dict(result)
        self.assertEqual(payload["schema"], "portfolio-analysis-v1")
        self.assertEqual(payload["schema"], pa.PORTFOLIO_ANALYSIS_SCHEMA)
        self.assertEqual(payload["approval_policy"], "REFERENCE_ONLY")
        json.dumps(payload)  # 必须可序列化
        for value in payload["weights"].values():
            self.assertTrue(value is None or math.isfinite(value))


@unittest.skipUnless(
    _REAL_DATA_OK,
    "three_musketeers 真实冒烟：需要 fund_daily.csv + factor_values/*.parquet",
)
class ThreeMusketeersSmokeTest(unittest.TestCase):
    """three_musketeers 真实数据冒烟（共享目录契约 + B 本地数据）。"""

    @classmethod
    def setUpClass(cls) -> None:
        from qteasy_research.reference.backtest_engine import (
            load_price_frames,
            parse_contract,
        )
        from qteasy_research.reference.config import STRATEGY_CONTRACT_PATH
        from qteasy_research.reference.factor_tear import (
            FACTOR_IDS,
            load_factor_panel_from_parquet,
        )

        contract = parse_contract(STRATEGY_CONTRACT_PATH)
        cls.strategy = next(
            (s for s in contract.strategies if s.strategy_id == "three_musketeers"), None
        )
        if cls.strategy is None:
            raise unittest.SkipTest("契约中无 three_musketeers")
        inputs = pa.strategy_analysis_inputs(cls.strategy)
        if inputs["error"]:
            raise unittest.SkipTest(f"three_musketeers 无可分析权重: {inputs['error']}")

        factor_panels = load_factor_panel_from_parquet(
            _QTEASY_LAB / "research_store" / "factor_values", FACTOR_IDS
        )
        universe = sorted(set(inputs["weights"].keys()) | set().union(
            *(set(p.columns) for p in factor_panels.values())
        ))
        frames = load_price_frames(universe, data_dir=_QTEASY_LAB / "data", online_ok=False)
        cls.close_panel = pa.align_close_panel(frames)
        cls.result = pa.analyze_portfolio(
            inputs["weights"],
            cls.close_panel,
            factor_panels,
            bounds=inputs["bounds"],
            run_id="smoke_three_musketeers",
        )

    def test_three_musketeers_assets(self) -> None:
        self.assertIn("512890.SH", self.result["assets"])
        self.assertIn("513650.SH", self.result["assets"])
        self.assertIn("518880.SH", self.result["assets"])

    def test_three_musketeers_rc_sums(self) -> None:
        self.assertAlmostEqual(
            float(self.result["asset_risk_contributions"]["rc"].sum()), 1.0, places=6
        )
        self.assertAlmostEqual(
            float(self.result["factor_risk_contributions"]["rc"].sum()), 1.0, places=6
        )

    def test_three_musketeers_summary_ascii(self) -> None:
        md = pa.render_summary_md(self.result)
        self.assertTrue(_ASCII.match(md))


if __name__ == "__main__":
    unittest.main()
