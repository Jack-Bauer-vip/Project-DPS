"""reference/macro_monitoring：宏观监控框架（M1/M2/M3）测试（离线合成 fixture）。

纯 unittest + tempfile，不用真实契约/A 数据（项目纪律）。
运行：``cd research_engines/qteasy_lab && .venv/Scripts/python.exe -B -m unittest discover -s "D:\Project DPS/tests" -q``
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from qteasy_research.reference.macro_monitoring import (
    CORRELATION_WINDOW_DAYS,
    build_correlation,
    build_stress_scenarios,
    compute_macro_fitness,
    current_macro_state,
    render_correlation_md,
    render_macro_fitness_md,
    render_stress_md,
    run_macro_monitoring,
    write_correlation,
    write_macro_fitness,
    write_stress,
)

_ASCII = re.compile(r"^[\x00-\x7F]*$")


def _scenario_table(months: list[str] | None = None,
                    states_spec: list[list[str]] | None = None,
                    unavailable_last: bool = False) -> pd.DataFrame:
    """合成宏观场景表（含 9 布尔列 + macro_unavailable）。"""
    months = months or ["2026-06-30", "2026-07-31"]
    states_spec = states_spec or [["rate_up"], ["rate_up"]]
    rows = []
    all_states = (
        "rate_up", "rate_down", "rate_stable",
        "curve_inverted", "curve_normal",
        "real_yield_up", "real_yield_down", "real_yield_stable",
    )
    for i, month in enumerate(months):
        states = states_spec[i]
        unavailable = bool(unavailable_last) and i == len(months) - 1
        row = {
            "month": month,
            "states": states if not unavailable else [],
            "rate_proxy": "DGS30",
            "macro_unavailable": unavailable,
        }
        for state in all_states:
            row[state] = (not unavailable) and (state in states)
        rows.append(row)
    return pd.DataFrame(rows)


def _price_frame(dates: pd.DatetimeIndex, base: float = 100.0,
                 drift: float = 0.001, noise: float = 0.002) -> pd.DataFrame:
    """合成日行情（trade_date/close，小幅趋势+噪声）。"""
    rng = np.random.default_rng(42)
    noise_seq = rng.normal(0.0, noise, size=len(dates))
    close = [base * (1.0 + drift * i + noise_seq[i]) for i in range(len(dates))]
    return pd.DataFrame({"trade_date": dates, "close": close})


def _monthly_frame(months: list[str], returns: list[float],
                   states: list[list[str]]) -> pd.DataFrame:
    """合成月度收益 × states 表（compute_macro_fitness 输入）。"""
    return pd.DataFrame({
        "month": months,
        "asset_return": returns,
        "states": states,
    })


class CurrentMacroStateTests(unittest.TestCase):
    def test_empty_table(self) -> None:
        state = current_macro_state(pd.DataFrame())
        self.assertIsNone(state["asof_month"])
        self.assertEqual(state["active_states"], [])
        self.assertTrue(state["macro_unavailable"])

    def test_active_states_from_last_row(self) -> None:
        table = _scenario_table(
            months=["2026-06-30", "2026-07-31"],
            states_spec=[["rate_down"], ["rate_up", "curve_normal", "real_yield_up"]],
        )
        state = current_macro_state(table)
        self.assertEqual(state["asof_month"], "2026-07-31")
        self.assertEqual(sorted(state["active_states"]),
                         ["curve_normal", "rate_up", "real_yield_up"])
        self.assertFalse(state["macro_unavailable"])

    def test_macro_unavailable_last_month(self) -> None:
        table = _scenario_table(states_spec=[["rate_up"], ["rate_up"]],
                                unavailable_last=True)
        state = current_macro_state(table)
        self.assertEqual(state["active_states"], [])
        self.assertTrue(state["macro_unavailable"])


class MacroFitnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dates = pd.date_range("2024-01-02", periods=300, freq="B")

    def test_fit_score_normalized(self) -> None:
        """rate_up（active）月均 0.03，rate_stable 0.06，rate_down -0.01 → fit≈0.5714。"""
        months = (
            ["2026-01-31"] * 5 + ["2026-02-28"] * 4 + ["2026-03-31"] * 3
        )
        returns = [0.02, 0.04, 0.03, 0.05, 0.01] + [0.05, 0.07, 0.06, 0.06] + [-0.02, 0.00, -0.01]
        states = ([["rate_up"]] * 5 + [["rate_stable"]] * 4 + [["rate_down"]] * 3)
        monthly = _monthly_frame(months, returns, states)
        frames = {"ASSET_A": _price_frame(self.dates, base=100.0)}
        macro_state = {
            "asof_month": "2026-07-31", "phase": "early", "active_states": ["rate_up"],
            "durations": {}, "rate_proxy": "DGS30", "macro_unavailable": False,
        }
        fitness = compute_macro_fitness(["ASSET_A"], frames, {"ASSET_A": monthly}, macro_state)
        row = fitness.iloc[0]
        self.assertAlmostEqual(row["fit_score"], 0.04 / 0.07, places=4)
        self.assertAlmostEqual(row["state_hist_avg"], 3.0, places=2)
        self.assertEqual(row["sample_count"], 5)
        self.assertIn(row["confidence"], ("low", "medium", "high"))
        # 波动分位应在 0~1。
        self.assertGreaterEqual(row["vol_pctile_20d"], 0.0)
        self.assertLessEqual(row["vol_pctile_20d"], 1.0)

    def test_insufficient_samples_not_fabricated(self) -> None:
        """当前 states 匹配月数 <5 → fit_score/state_hist_avg None + confidence low。"""
        monthly = _monthly_frame(
            ["2026-07-31", "2026-06-30"],
            [0.03, 0.02],
            [["rate_up"], ["rate_up"]],
        )
        frames = {"ASSET_A": _price_frame(self.dates)}
        macro_state = {"asof_month": "2026-07-31", "active_states": ["rate_up"],
                       "durations": {}, "rate_proxy": "DGS30", "macro_unavailable": False}
        fitness = compute_macro_fitness(["ASSET_A"], frames, {"ASSET_A": monthly}, macro_state)
        row = fitness.iloc[0]
        self.assertIsNone(row["fit_score"])
        self.assertIsNone(row["state_hist_avg"])
        self.assertEqual(row["confidence"], "low")
        self.assertEqual(row["sample_count"], 2)

    def test_no_active_states(self) -> None:
        """active_states 为空 → 不匹配（样本 0），数值 None。"""
        monthly = _monthly_frame(
            ["2026-07-31", "2026-06-30"], [0.03, 0.02], [["rate_up"], ["rate_up"]],
        )
        frames = {"ASSET_A": _price_frame(self.dates)}
        macro_state = {"asof_month": "2026-07-31", "active_states": [],
                       "durations": {}, "rate_proxy": "DGS30", "macro_unavailable": False}
        fitness = compute_macro_fitness(["ASSET_A"], frames, {"ASSET_A": monthly}, macro_state)
        self.assertIsNone(fitness.iloc[0]["fit_score"])
        self.assertEqual(fitness.iloc[0]["sample_count"], 0)

    def test_render_and_write(self) -> None:
        fitness = pd.DataFrame([{
            "asset_id": "ASSET_A", "fit_score": 0.57, "state_hist_avg": 3.0,
            "cur_state_avg": 3.0, "hist_avg": 1.0, "sample_count": 5,
            "confidence": "low", "vol_pctile_20d": 0.5, "vol_pctile_60d": 0.6,
        }])
        macro_state = {"asof_month": "2026-07-31", "phase": "early",
                       "phase_basis": "rate_up", "phase_confidence": "low",
                       "durations": {"rate_up": 2}, "rate_proxy": "DGS30",
                       "active_states": ["rate_up"], "macro_unavailable": False}
        md_text = render_macro_fitness_md("three_musketeers", fitness, macro_state)
        self.assertTrue(_ASCII.match(md_text), "markdown 必须全 ASCII")
        self.assertIn("## Macro State", md_text)
        self.assertIn("ASSET_A", md_text)
        self.assertIn("valuation_na", md_text)

        with tempfile.TemporaryDirectory() as td:
            md_path, csv_path = write_macro_fitness(
                "three_musketeers", fitness, macro_state, td,
            )
            self.assertTrue(md_path.exists())
            self.assertTrue(csv_path.exists())
            csv_head = csv_path.read_text(encoding="utf-8").splitlines()[0]
            self.assertTrue(csv_head.startswith("# schema_version="))
            self.assertIn("generated_date=", csv_head)
            self.assertIn("valuation", csv_path.read_text(encoding="utf-8"))


class CorrelationTests(unittest.TestCase):
    def setUp(self) -> None:
        n = 63
        idx = pd.date_range("2026-05-06", periods=n, freq="B")
        a = np.arange(n, dtype=float)
        data = {
            "ASSET_A": a,
            "ASSET_B": a,            # 完全同向（corr=1.0）
            "ASSET_C": -a,           # 完全反向（corr=-1.0）
            "ASSET_D": [np.nan] * (n - 30) + list(range(30)),  # 仅 30 个共同日
        }
        self.returns = pd.DataFrame(data, index=idx)

    def test_diagonal_and_off_diagonal(self) -> None:
        matrix, reference, high_corr = build_correlation(
            ["ASSET_A", "ASSET_B", "ASSET_C", "ASSET_D"],
            ["ASSET_A", "ASSET_B", "ASSET_C"],
            self.returns,
        )
        self.assertAlmostEqual(matrix.loc["ASSET_A", "ASSET_A"], 1.0)
        self.assertAlmostEqual(matrix.loc["ASSET_A", "ASSET_B"], 1.0, places=4)
        self.assertAlmostEqual(matrix.loc["ASSET_A", "ASSET_C"], -1.0, places=4)
        # ASSET_D 共同日 30 < 60 → NaN（不虚构）。
        self.assertTrue(pd.isna(matrix.loc["ASSET_A", "ASSET_D"]))
        # 参考列交叉相关。
        self.assertAlmostEqual(reference.loc["ASSET_A", "ASSET_A"], 1.0, places=4)
        self.assertAlmostEqual(reference.loc["ASSET_B", "ASSET_A"], 1.0, places=4)

    def test_high_corr_pairs(self) -> None:
        _, _, high_corr = build_correlation(
            ["ASSET_A", "ASSET_B", "ASSET_C"],
            [],
            self.returns,
        )
        flags = {(r["asset_a"], r["asset_b"]): r["flag"] for _, r in high_corr.iterrows()}
        self.assertEqual(flags.get(("ASSET_A", "ASSET_B")), "HIGH_CORR")
        self.assertEqual(flags.get(("ASSET_A", "ASSET_C")), "NEG_HIGH_CORR")
        # B==A、C==-A → corr(B,C)=-1.0，也超过 |0.7|，属合法高相关对。
        self.assertEqual(flags.get(("ASSET_B", "ASSET_C")), "NEG_HIGH_CORR")

    def test_render_and_write(self) -> None:
        matrix, reference, high_corr = build_correlation(
            ["ASSET_A", "ASSET_B", "ASSET_C"], ["ASSET_A"], self.returns,
        )
        md_text = render_correlation_md(matrix, reference, high_corr)
        self.assertTrue(_ASCII.match(md_text))
        self.assertIn("## Main Matrix", md_text)
        self.assertIn("## High Correlation Pairs", md_text)
        self.assertIn("HIGH_CORR", md_text)

        with tempfile.TemporaryDirectory() as td:
            matrix_p, pairs_p, summary_p = write_correlation(
                matrix, reference, high_corr, td,
                asof_month="2026-07-31", data_asof="2026-07-31",
            )
            self.assertTrue(matrix_p.exists())
            self.assertTrue(pairs_p.exists())
            self.assertTrue(summary_p.exists())
            head = matrix_p.read_text(encoding="utf-8").splitlines()[0]
            self.assertIn("window_days=63", head)
            # 首列表头必须是 asset_id（不能是 #：A 侧以 comment='#' 读取，
            # 首列名为 # 会被当注释行跳过 → 读入变 Unnamed: 0）。
            header_line = matrix_p.read_text(encoding="utf-8").splitlines()[1]
            self.assertTrue(header_line.startswith("asset_id,"),
                            f"首列表头应为 asset_id: {header_line!r}")
            # 模拟 A 侧读端（comment='#'）：首列名应为 asset_id 而非 Unnamed。
            read_back = pd.read_csv(matrix_p, comment="#")
            self.assertNotIn("Unnamed: 0", read_back.columns)
            self.assertIn("asset_id", read_back.columns)


class StressScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        # 12 个月末价格，全部 flat（组合净值恒 1.0）。
        self.month_ends = pd.date_range("2025-08-31", periods=12, freq="ME")
        self.prices = pd.DataFrame({
            "ASSET_A": [1.0] * 12,
            "ASSET_B": [1.0] * 12,
        }, index=self.month_ends)
        self.weights = {"ASSET_A": 1.0, "ASSET_B": 0.0}

    def test_single_asset_shock(self) -> None:
        """S1 型：ASSET_A 最后 1 个月 -30% → 净值 0.7，diff -0.3。"""
        scenarios = {"S1": {"label": "gold -30% 1m", "asset": "ASSET_A",
                            "monthly_rets": [-0.30]}}
        strategies = {"TEST": self.weights}
        df = build_stress_scenarios(strategies, self.prices, self.month_ends, {}, scenarios)
        row = df.iloc[0]
        self.assertAlmostEqual(row["baseline_nav"], 1.0, places=4)
        self.assertAlmostEqual(row["stressed_nav"], 0.7, places=4)
        self.assertAlmostEqual(row["diff_amount"], -0.3, places=4)
        self.assertAlmostEqual(row["diff_pct"], -0.3, places=4)
        self.assertEqual(row["note"], "")

    def test_multi_month_compounding(self) -> None:
        """连续 3 个月累计 -20%（每月 0.8^(1/3)-1）→ 期末 0.8。"""
        scenarios = {"S2": {"label": "dividend -20% 3m", "asset": "ASSET_A",
                            "monthly_rets": [(0.80 ** (1 / 3)) - 1.0] * 3}}
        strategies = {"TEST": self.weights}
        df = build_stress_scenarios(strategies, self.prices, self.month_ends, {}, scenarios)
        row = df.iloc[0]
        self.assertAlmostEqual(row["stressed_nav"], 0.8, places=4)
        self.assertAlmostEqual(row["diff_pct"], -0.2, places=4)

    def test_replay_no_samples_noted(self) -> None:
        """S3 型：无历史压力样本 → note 非空，diff 0（不虚构）。"""
        scenarios = {"S3": {"label": "rate up replay", "replay_scenario": "rate_up_50bp",
                            "monthly_rets": None}}
        strategies = {"TEST": self.weights}
        df = build_stress_scenarios(strategies, self.prices, self.month_ends, {}, scenarios)
        row = df.iloc[0]
        self.assertAlmostEqual(row["diff_pct"], 0.0, places=4)
        self.assertIn("no replay samples", row["note"])

    def test_replay_with_samples_injects(self) -> None:
        """S3 型且有历史样本 → 按历史月均收益注入。"""
        stress_map = {"ASSET_A": {"rate_up_50bp": {
            "pnl_pct": -5.0, "sample_count": 6, "corr": None,
            "confidence": "medium", "basis": "DGS30",
        }}}
        scenarios = {"S3": {"label": "rate up replay", "replay_scenario": "rate_up_50bp",
                            "monthly_rets": None}}
        strategies = {"TEST": self.weights}
        df = build_stress_scenarios(strategies, self.prices, self.month_ends,
                                    stress_map, scenarios)
        row = df.iloc[0]
        self.assertAlmostEqual(row["stressed_nav"], 0.95, places=4)
        self.assertEqual(row["note"], "")

    def test_render_and_write(self) -> None:
        df = build_stress_scenarios(
            {"TEST": self.weights}, self.prices, self.month_ends, {},
            {"S1": {"label": "gold -30% 1m", "asset": "ASSET_A", "monthly_rets": [-0.30]}},
        )
        md_text = render_stress_md(df)
        self.assertTrue(_ASCII.match(md_text))
        self.assertIn("## Results", md_text)
        self.assertIn("## Sensitivity Notes", md_text)

        with tempfile.TemporaryDirectory() as td:
            md_path, csv_path = write_stress(df, td, asof_month="2026-07-31",
                                             data_asof="2026-07-31")
            self.assertTrue(md_path.exists())
            self.assertTrue(csv_path.exists())
            self.assertTrue(_ASCII.match(md_path.read_text(encoding="utf-8")))


def _make_contract_json() -> dict:
    """合成契约：three_musketeers + global_allocation（2 资产 + 3 资产）。"""
    def asset(aid: str, weight: float) -> dict:
        return {
            "asset_id": aid, "role": "base", "enabled": True,
            "signal_source": "mid_line", "min_weight": 0.0,
            "target_weight": weight, "max_weight": weight + 0.02,
            "target_weight_configured": True,
        }

    return {
        "schema_version": "1.0",
        "contract_type": "strategy_rules",
        "generated_at": "2026-08-10T00:00:00",
        "generated_by": "systemA",
        "strategies": [
            {
                "strategy_id": "three_musketeers", "decision_rule": "mid_line",
                "enabled": True, "use_target_ratio": True,
                "rebalance_frequency": "weekly", "rebalance_threshold_abs": 0.03,
                "asset_rebalance_threshold_abs": 0.05, "signal_filters": None,
                "preferences": {},
                "assets": [asset("512890.SH", 0.43), asset("518880.SH", 0.38),
                           asset("513650.SH", 0.19)],
            },
            {
                "strategy_id": "global_allocation", "decision_rule": "mid_line",
                "enabled": True, "use_target_ratio": True,
                "rebalance_frequency": "weekly", "rebalance_threshold_abs": 0.03,
                "asset_rebalance_threshold_abs": 0.05, "signal_filters": None,
                "preferences": {},
                "assets": [asset("512890.SH", 0.5), asset("518880.SH", 0.5)],
            },
        ],
        "shared_config": {
            "backtest": {"initial_cash": 100000, "cost_rate": 0.001,
                         "slippage_rate": 0.0005},
        },
    }


class IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.contract_path = self.root / "strategy_contract.json"
        self.contract_path.write_text(
            json.dumps(_make_contract_json(), ensure_ascii=False), encoding="utf-8",
        )
        self.output_root = self.root / "reports" / "macro_monitoring"

        dates = pd.date_range("2025-01-02", periods=200, freq="B")
        self.frames = {
            "512890.SH": _price_frame(dates, base=100.0),
            "518880.SH": _price_frame(dates, base=50.0),
            "513650.SH": _price_frame(dates, base=200.0),
        }
        self.macro_table = _scenario_table(
            months=["2026-06-30", "2026-07-31"],
            states_spec=[["rate_up"], ["rate_up"]],
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @mock.patch("qteasy_research.reference.macro_monitoring.build_stress_simulator",
                return_value={})
    @mock.patch("qteasy_research.reference.macro_monitoring.build_monthly_scenario_table")
    def test_full_pipeline_writes_outputs(self, mock_table, mock_stress) -> None:
        from qteasy_research.reference.backtest_engine import parse_contract

        mock_table.return_value = self.macro_table
        with mock.patch(
            "qteasy_research.reference.macro_monitoring.load_monitoring_frames",
            return_value=self.frames,
        ):
            contract = parse_contract(self.contract_path)
            result = run_macro_monitoring(contract, self.root, output_root=self.output_root)

        self.assertEqual(result["status"], "OK", msg=str(result["errors"]))
        self.assertEqual(result["asof_month"], "2026-07-31")
        self.assertEqual(result["counts"], {"M1": 2, "M2": 1, "M3": 1})
        self.assertEqual(result["errors"], [])

        # M1 三件套（2 rule × 2 文件）。
        expected = [
            self.output_root / "202607_macro_fitness_three_musketeers.md",
            self.output_root / "202607_macro_fitness_three_musketeers.csv",
            self.output_root / "202607_macro_fitness_global_allocation.md",
            self.output_root / "202607_macro_fitness_global_allocation.csv",
            self.output_root / "202607_correlation_matrix.csv",
            self.output_root / "202607_high_corr_pairs.csv",
            self.output_root / "202607_correlation_summary.md",
            self.output_root / "202607_stress_scenarios.md",
            self.output_root / "202607_stress_scenarios.csv",
        ]
        for path in expected:
            self.assertTrue(path.exists(), f"missing {path.name}")
        # 全 ASCII 纪律。
        for path in expected:
            self.assertTrue(_ASCII.match(path.read_text(encoding="utf-8")),
                            f"non-ascii in {path.name}")

    @mock.patch("qteasy_research.reference.macro_monitoring.build_monthly_scenario_table",
                side_effect=RuntimeError("macro data missing"))
    def test_macro_failure_degrades(self, mock_table) -> None:
        """宏观场景表构建失败 → 仍产出（空宏观背景），不中断 M2/M3。"""
        from qteasy_research.reference.backtest_engine import parse_contract

        with mock.patch(
            "qteasy_research.reference.macro_monitoring.load_monitoring_frames",
            return_value=self.frames,
        ):
            contract = parse_contract(self.contract_path)
            result = run_macro_monitoring(contract, self.root, output_root=self.output_root)

        self.assertEqual(result["status"], "PARTIAL")
        self.assertTrue(any("build_monthly_scenario_table" in e for e in result["errors"]))


if __name__ == "__main__":
    unittest.main()
