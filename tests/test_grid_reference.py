"""reference/grid_reference：网格参考表测试（离线 fixture，不依赖真实数据目录）。"""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from qteasy_research.reference.config import VOLATILITY_WINDOWS
from qteasy_research.reference.grid_reference import build_grid_reference


def _price_frame(n: int = 300, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    close = 100.0 * np.cumprod(1.0 + rng.normal(0.0005, 0.01, n))
    return pd.DataFrame({"trade_date": dates, "close": close, "source": "local"})


def _assets() -> pd.DataFrame:
    return pd.DataFrame({
        "asset_id": ["000001.SZ", "164824.SZ"],
        "name": ["资产A", "资产B"],
    })


class GridReferenceTests(unittest.TestCase):
    def test_columns_complete(self) -> None:
        assets = _assets()
        aligned = {
            "000001.SZ": _price_frame(),
            "164824.SZ": _price_frame(seed=7),
        }
        grid = build_grid_reference(assets, aligned)
        expected = [
            "asset_id",
            *[f"current_vol_{w}d" for w in VOLATILITY_WINDOWS],
            *[f"vol_rank_{w}d" for w in VOLATILITY_WINDOWS],
            *[f"cone_{w}_p{p}" for w in VOLATILITY_WINDOWS for p in (5, 50, 95)],
            "suggested_reference_spread",
            "confidence",
            "data_quality",
            "warnings",
        ]
        self.assertEqual(list(grid.columns), expected)
        self.assertEqual(len(grid), 2)

    def test_long_history_high_confidence(self) -> None:
        assets = _assets()
        aligned = {"000001.SZ": _price_frame(300), "164824.SZ": _price_frame(300, seed=7)}
        grid = build_grid_reference(assets, aligned)
        row = grid.loc[grid["asset_id"] == "000001.SZ"].iloc[0]
        self.assertEqual(row["confidence"], "high")
        self.assertGreater(float(row["current_vol_20d"]), 0)
        self.assertGreater(float(row["current_vol_252d"]), 0)
        self.assertGreater(float(row["cone_60_p95"]), 0)
        self.assertNotEqual(row["suggested_reference_spread"], "")

    def test_short_history_degraded_to_empty(self) -> None:
        # 15 天历史连 20d 窗口都填不满 → 全部置空 + confidence=low。
        assets = _assets()
        aligned = {"000001.SZ": _price_frame(15), "164824.SZ": _price_frame(300, seed=7)}
        grid = build_grid_reference(assets, aligned)
        row = grid.loc[grid["asset_id"] == "000001.SZ"].iloc[0]
        self.assertEqual(row["confidence"], "low")
        self.assertEqual(row["current_vol_20d"], "")
        self.assertEqual(row["current_vol_60d"], "")
        self.assertEqual(row["suggested_reference_spread"], "")
        self.assertIn("历史不足", row["warnings"])

    def test_spread_uses_60d_vol(self) -> None:
        # 日收益 std≈1% → 参考间距 ≈ 0.03 × 分档乘子，落在 (0.015, 0.06)。
        assets = _assets()
        aligned = {"000001.SZ": _price_frame(300), "164824.SZ": _price_frame(300, seed=7)}
        grid = build_grid_reference(assets, aligned)
        row = grid.loc[grid["asset_id"] == "000001.SZ"].iloc[0]
        spread = float(row["suggested_reference_spread"])
        self.assertGreater(spread, 0.015)
        self.assertLess(spread, 0.06)

    def test_empty_asset_marked_missing(self) -> None:
        assets = _assets()
        aligned = {"000001.SZ": _price_frame(), "164824.SZ": pd.DataFrame()}
        grid = build_grid_reference(assets, aligned)
        row = grid.loc[grid["asset_id"] == "164824.SZ"].iloc[0]
        self.assertEqual(row["data_quality"], "D")
        self.assertEqual(row["confidence"], "low")
        self.assertEqual(row["current_vol_20d"], "")
        self.assertEqual(row["suggested_reference_spread"], "")
        self.assertIn("行情缺失", row["warnings"])

    def test_60d_missing_spread_empty(self) -> None:
        # 20d 窗口可用但 60d 不足 → 20d 有值、60d 空、spread 空（不强造间距）。
        assets = _assets()
        aligned = {"000001.SZ": _price_frame(30), "164824.SZ": _price_frame(300, seed=7)}
        grid = build_grid_reference(assets, aligned)
        row = grid.loc[grid["asset_id"] == "000001.SZ"].iloc[0]
        self.assertNotEqual(row["current_vol_20d"], "")
        self.assertEqual(row["current_vol_60d"], "")
        self.assertEqual(row["suggested_reference_spread"], "")
        self.assertEqual(row["confidence"], "low")


if __name__ == "__main__":
    unittest.main()
