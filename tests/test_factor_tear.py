"""factor_tear.py（alphalens 借鉴）单元测试。

合成 fixture 模式：``tempfile.TemporaryDirectory`` + ``pd.date_range`` +
``np.arange/random``，不依赖真实数据或外部网络。

覆盖：
- IC 方向正确（已知前导因子正 IC）；
- turnover 边界（无分位变化 = 0）；
- IC decay 随 horizon 递减；
- 与 evaluate_factor_effectiveness() 对同一输入 IC 交叉一致；
- 从 parquet 读因子值的入口正常；
- forward return 口径 close.pct_change().shift(-N)；
- CSV 即时计算因子入口正常。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from qteasy_research.pretrade.factor_research import evaluate_factor_effectiveness
from qteasy_research.reference import factor_tear as ft

ASSETS = ["a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7"]


def _ar_factor_panel(rng: np.random.Generator, dates: pd.DatetimeIndex, assets: list[str], phi: float = 0.8) -> pd.DataFrame:
    n = len(dates)
    k = len(assets)
    noise = rng.normal(0, 0.1, (n, k))
    values = np.zeros((n, k))
    for t in range(1, n):
        values[t] = phi * values[t - 1] + noise[t]
    return pd.DataFrame(values, index=dates, columns=assets)


def _leading_factor_and_close(rng: np.random.Generator, dates: pd.DatetimeIndex, assets: list[str], phi: float = 0.8, strength: float = 0.05) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回 (factor, close)：factor 为 AR(1) 前导因子，return[t]=factor[t-1]*strength+noise。"""
    n = len(dates)
    factor = _ar_factor_panel(rng, dates, assets, phi)
    returns = pd.DataFrame(np.zeros((n, len(assets))), index=dates, columns=assets)
    noise = rng.normal(0, 0.01, (n, len(assets)))
    for t in range(1, n):
        returns.iloc[t] = factor.iloc[t - 1].values * strength + noise[t]
    close = (1 + returns).cumprod() * 100
    return factor, close


class FactorTearTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rng = np.random.default_rng(42)
        self.dates = pd.date_range("2020-01-01", periods=150, freq="B")

    def test_known_leading_factor_has_positive_ic(self) -> None:
        dates = pd.date_range("2020-01-01", periods=120, freq="B")
        factor, close = _leading_factor_and_close(self.rng, dates, ASSETS)
        returns = close.pct_change()
        ic_series = ft.cross_sectional_ic_series(factor, returns, 1)
        self.assertGreater(ic_series.mean(), 0.05)
        self.assertGreater(ic_series.mean(), 0)

    def test_turnover_zero_when_quantile_stable(self) -> None:
        dates = pd.date_range("2020-01-01", periods=60, freq="B")
        panel = pd.DataFrame(
            np.tile(np.arange(len(ASSETS), dtype=float) + 1.0, (len(dates), 1)),
            index=dates,
            columns=ASSETS,
        )
        for period in (1, 5, 20):
            self.assertEqual(ft.turnover(panel, period), 0.0)

    def test_ic_decay_decreases_with_horizon(self) -> None:
        dates = pd.date_range("2020-01-01", periods=300, freq="B")
        factor, close = _leading_factor_and_close(self.rng, dates, ASSETS, phi=0.8)
        tear = ft.build_tear_sheet(
            {"f": factor}, close, forward_periods=(1, 5, 20), factor_ids=("f",)
        )
        decay = tear["f"]["ic_decay"]
        ics = decay["ic"].astype(float).tolist()
        self.assertEqual(len(ics), 3)
        self.assertGreater(ics[0], ics[1])
        self.assertGreater(ics[1], ics[2])
        # 方向正确：短周期 IC 为正
        self.assertGreater(ics[0], 0.05)
        # 每周期都有 turnover 键
        self.assertIn("turnover", tear["f"])
        self.assertEqual(len(tear["f"]["turnover"]), 3)
        # quantile_returns 每周期一份
        self.assertIn(1, tear["f"]["quantile_returns"])
        self.assertIn(20, tear["f"]["quantile_returns"])

    def test_ic_matches_evaluate_factor_effectiveness(self) -> None:
        dates = pd.date_range("2020-01-01", periods=120, freq="B")
        factor, close = _leading_factor_and_close(self.rng, dates, ASSETS)
        returns = close.pct_change()
        my_ic = ft.cross_sectional_ic_series(factor, returns, 1).mean()
        result = evaluate_factor_effectiveness(
            factor, returns, factor_id="f", forward_period=1, min_samples=10
        )
        self.assertAlmostEqual(my_ic, result.ic, places=9)
        # 且 tear sheet 的 decay 表 IC 与 evaluate 一致
        tear = ft.build_tear_sheet({"f": factor}, close, forward_periods=(1,), factor_ids=("f",))
        self.assertAlmostEqual(tear["f"]["ic_decay"].iloc[0]["ic"], result.ic, places=9)

    def test_load_factor_panel_from_parquet(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            rows = []
            for date in self.dates[:10]:
                for index, asset in enumerate(ASSETS):
                    rows.append({
                        "date": date,
                        "asset_code": asset,
                        "value": float(index) / 10.0,
                    })
            pd.DataFrame(rows).to_parquet(Path(temp) / "momentum_60d.parquet", index=False)
            panels = ft.load_factor_panel_from_parquet(temp, ("momentum_60d",))
            self.assertIn("momentum_60d", panels)
            panel = panels["momentum_60d"]
            self.assertEqual(panel.shape, (10, len(ASSETS)))
            self.assertEqual(set(panel.columns), set(ASSETS))
            # 值正确回转
            self.assertEqual(panel.loc[self.dates[0], "a1"], 0.1)

    def test_forward_returns_definition(self) -> None:
        close = pd.DataFrame(
            np.arange(1.0, 21.0).reshape(10, 2),
            index=self.dates[:10],
            columns=["A", "B"],
        )
        fwd = ft.compute_forward_returns(close, 2)
        expected = close.pct_change().shift(-2)
        pd.testing.assert_frame_equal(fwd, expected)

    def test_compute_factor_panel_from_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            csv_path = Path(temp) / "fund_daily.csv"
            rows = []
            dates = pd.date_range("2020-01-01", periods=90, freq="B")
            for code, mult in (("510300.SH", 1.0), ("510500.SH", 1.5)):
                for index, day in enumerate(dates):
                    close = mult * (100 + index)
                    rows.append({
                        "ts_code": code,
                        "trade_date": day.strftime("%Y-%m-%d"),
                        "open": close,
                        "high": close + 1,
                        "low": close - 1,
                        "close": close,
                        "pre_close": close - 1,
                        "vol": 1000,
                        "amount": close * 1000,
                    })
            pd.DataFrame(rows).to_csv(csv_path, index=False)
            panels = ft.compute_factor_panel_from_csv(csv_path, ("momentum_60d", "liquidity_turnover"))
            self.assertEqual(set(panels["momentum_60d"].columns), {"510300.SH", "510500.SH"})
            # 价格单边上涨 → momentum_60d 为正
            mom = panels["momentum_60d"]["510300.SH"].dropna()
            self.assertGreater(len(mom), 0)
            self.assertGreater(mom.iloc[0], 0)

    def test_run_tear_sheets_csv_small(self) -> None:
        """end-to-end：临时 CSV → run_tear_sheets（csv source，不导 PNG）"""
        with tempfile.TemporaryDirectory() as temp:
            csv_path = Path(temp) / "fund_daily.csv"
            rows = []
            dates = pd.date_range("2020-01-01", periods=100, freq="B")
            for code, mult in (("510300.SH", 1.0), ("510500.SH", 1.5), ("518880.SH", 2.0)):
                for index, day in enumerate(dates):
                    close = mult * (100 + index)
                    rows.append({
                        "ts_code": code,
                        "trade_date": day.strftime("%Y-%m-%d"),
                        "open": close,
                        "high": close + 1,
                        "low": close - 1,
                        "close": close,
                        "pre_close": close - 1,
                        "vol": 1000,
                        "amount": close * 1000,
                    })
            pd.DataFrame(rows).to_csv(csv_path, index=False)
            result = ft.run_tear_sheets(
                source="csv",
                forward_periods=(1, 5),
                csv_path=csv_path,
                out_dir=Path(temp) / "out",
                factor_ids=("momentum_60d",),
                export_csv=True,
                export_png=False,
            )
            self.assertIn("momentum_60d", result["tear"])
            self.assertIsNotNone(result["csv_dir"])
            self.assertTrue((Path(result["csv_dir"]) / "momentum_60d_ic_decay.csv").exists())


if __name__ == "__main__":
    unittest.main()
