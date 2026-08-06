"""reference/rolling_beta：多窗口滚动 Beta 数值回归测试。"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from qteasy_research.pretrade.metrics import rolling_beta as metrics_rolling_beta
from qteasy_research.reference.rolling_beta import (
    multi_benchmark_beta,
    rolling_beta,
    rolling_beta_summary,
)


def _make_frames(beta_true: float = 2.0, n: int = 300) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    rng = np.random.default_rng(7)
    bench_r = rng.normal(0.0004, 0.01, n)
    asset_r = beta_true * bench_r + rng.normal(0, 0.002, n)
    bench_close = 100 * np.cumprod(1 + bench_r)
    asset_close = 100 * np.cumprod(1 + asset_r)
    return (
        pd.DataFrame({"trade_date": dates, "close": asset_close}),
        pd.DataFrame({"trade_date": dates, "close": bench_close}),
    )


class RollingBetaTests(unittest.TestCase):
    def test_returns_all_windows(self) -> None:
        asset, bench = _make_frames()
        result = rolling_beta(asset["close"].pct_change().dropna(), bench["close"].pct_change().dropna())
        self.assertEqual(set(result), {"20", "60", "120", "252"})

    def test_beta_recovers_true_value(self) -> None:
        asset, bench = _make_frames(beta_true=2.0, n=600)
        series = rolling_beta(asset["close"].pct_change().dropna(), bench["close"].pct_change().dropna())
        self.assertAlmostEqual(float(series["120"].dropna().iloc[-1]), 2.0, delta=0.25)

    def test_zero_benchmark_var_gives_nan(self) -> None:
        dates = pd.date_range("2020-01-01", periods=200, freq="B")
        asset = pd.Series(np.linspace(100, 200, 200), index=dates)
        bench = pd.Series(np.full(200, 100.0), index=dates)  # 基准恒定 → var==0
        result = rolling_beta(asset.pct_change().dropna(), bench.pct_change().dropna())
        for _, series in result.items():
            self.assertTrue(np.all(np.isnan(series)), "基准无波动时 beta 应为 NaN")

    def test_metrics_shim_accepts_frames(self) -> None:
        asset, bench = _make_frames(beta_true=1.5, n=600)
        result = metrics_rolling_beta(asset, bench)
        self.assertEqual(set(result), {"20", "60", "120", "252"})
        self.assertAlmostEqual(float(result["120"].dropna().iloc[-1]), 1.5, delta=0.25)

    def test_summary_shape(self) -> None:
        asset, bench = _make_frames(n=600)
        summary = rolling_beta_summary(asset, bench)
        self.assertTrue(summary["available"])
        self.assertGreater(summary["overlap_days"], 0)
        self.assertIn("20", summary["latest_beta"])
        self.assertIn("data_asof", summary)

    def test_multi_benchmark_missing_is_unavailable(self) -> None:
        asset, bench = _make_frames(n=300)
        result = multi_benchmark_beta(asset, {"SPY": bench, "missing": pd.DataFrame()})
        self.assertTrue(result["SPY"]["available"])
        self.assertFalse(result["missing"]["available"])
