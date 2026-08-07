"""reference/red_flag：逐资产风控红/橙/黄测试（离线 fixture，不依赖真实数据目录）。

镜像系统A口径（日频波动率 + 近60日高点回撤），断言锁定阈值边界。
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from qteasy_research.reference.red_flag import (
    assess_red_flags,
    load_risk_thresholds,
)

_ASCII = re.compile(r"^[\x00-\x7F]*$")


def _asset_frame(close_values: list[float]) -> pd.DataFrame:
    """构造行情帧；close 末值由调用方控制 drawdown_60d。"""
    n = len(close_values)
    dates = pd.date_range("2025-01-02", periods=n, freq="B")
    return pd.DataFrame({"trade_date": dates, "close": close_values, "source": "local"})


def _frame_with_drawdown(drawdown: float, vol_annual_hint: float = 0.0) -> pd.DataFrame:
    """构造近 60 日高点回撤≈drawdown 的序列（末值=peak*(1+drawdown)）。"""
    n = 120
    peak = 100.0
    # 前 60 日：缓慢上行到 peak；后 60 日：从 peak 缓跌到 target。
    pre = list(np.linspace(95.0, peak, 60))
    target = peak * (1.0 + drawdown)
    post = list(np.linspace(peak, target, 60))
    return _asset_frame(pre + post)


def _assets(n_type: str = "ETF") -> pd.DataFrame:
    return pd.DataFrame({"asset_id": ["A1", "A2"], "type": [n_type, n_type]})


class LoadRiskThresholdsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.params = self.root / "strategy_params.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, payload: dict) -> None:
        self.params.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_load_full(self) -> None:
        # 写全 7 个阈值 → 无缺失项，warnings 应为空。
        self._write({"risk_thresholds": {
            "red_drawdown": -0.20, "orange_drawdown": -0.13,
            "yellow_drawdown": -0.08, "volatility_yellow": 0.04,
            "volatility_yellow_etf": 0.05, "volatility_yellow_stock": 0.038,
            "volatility_yellow_bond_etf": 0.018,
        }})
        thresholds, warnings = load_risk_thresholds(self.params)
        self.assertEqual(thresholds["red_drawdown"], -0.20)
        self.assertEqual(thresholds["volatility_yellow_etf"], 0.05)
        self.assertEqual(warnings, [])

    def test_missing_file(self) -> None:
        thresholds, warnings = load_risk_thresholds(self.root / "absent.json")
        self.assertIsNone(thresholds)
        self.assertEqual(len(warnings), 1)

    def test_corrupt_json(self) -> None:
        self.params.write_text("{ not json", encoding="utf-8")
        thresholds, warnings = load_risk_thresholds(self.params)
        self.assertIsNone(thresholds)
        self.assertEqual(len(warnings), 1)

    def test_missing_section(self) -> None:
        self._write({"other": 1})
        thresholds, warnings = load_risk_thresholds(self.params)
        self.assertIsNone(thresholds)
        self.assertEqual(len(warnings), 1)

    def test_partial_override_keeps_file_value(self) -> None:
        # 只缺 volatility_yellow → 该缺失项用默认，其余保留文件值（审核确认项）。
        self._write({"risk_thresholds": {"red_drawdown": -0.22, "orange_drawdown": -0.15}})
        thresholds, warnings = load_risk_thresholds(self.params)
        self.assertEqual(thresholds["red_drawdown"], -0.22)  # 保留文件值
        self.assertEqual(thresholds["volatility_yellow"], 0.035)  # 缺失项默认兜底
        self.assertIn("volatility_yellow", "".join(warnings))

    def test_bad_value_falls_back(self) -> None:
        # 仅 red_drawdown 为坏值，其余 6 项写全 → 只有该单项兜底 + 1 个 warning。
        self._write({"risk_thresholds": {
            "red_drawdown": "oops", "orange_drawdown": -0.12, "yellow_drawdown": -0.07,
            "volatility_yellow": 0.035, "volatility_yellow_etf": 0.045,
            "volatility_yellow_stock": 0.035, "volatility_yellow_bond_etf": 0.015,
        }})
        thresholds, warnings = load_risk_thresholds(self.params)
        self.assertEqual(thresholds["red_drawdown"], -0.18)
        self.assertEqual(thresholds["orange_drawdown"], -0.12)  # 其余保留文件值
        self.assertEqual(len(warnings), 1)


class AssessRedFlagsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fixture = self.root / "strategy_params.json"
        self.fixture.write_text(json.dumps({"risk_thresholds": {
            "red_drawdown": -0.18, "orange_drawdown": -0.12, "yellow_drawdown": -0.07,
            "volatility_yellow": 0.035, "volatility_yellow_etf": 0.045,
            "volatility_yellow_stock": 0.035, "volatility_yellow_bond_etf": 0.015,
        }}, ensure_ascii=False), encoding="utf-8")
        self.thresholds = load_risk_thresholds(self.fixture)[0]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_drawdown_boundaries(self) -> None:
        # 边界用 ±0.0001 的裕度（浮点：93/100-1=-0.0699…95 会误判为 >-0.07）。
        thresholds = self.thresholds
        cases = [
            (-0.1801, "red"), (-0.1799, "orange"),
            (-0.1201, "orange"), (-0.1199, "yellow"),
            (-0.0701, "yellow"), (-0.0699, None),
        ]
        for drawdown, expected in cases:
            frame = _frame_with_drawdown(drawdown)
            assets = _assets()
            flags = assess_red_flags(assets, {"A1": frame}, thresholds)
            actual = flags.get("A1")
            if expected is None:
                self.assertNotIn("A1", flags, f"drawdown={drawdown}")
            else:
                self.assertEqual(actual["level"], expected, f"drawdown={drawdown}")
                self.assertEqual(actual["triggered_by"], ["drawdown_60d"])

    def test_volatility_triggers_yellow(self) -> None:
        # drawdown 浅（>-0.07）但日频 vol 超阈 → yellow，triggered=volatility_20d。
        # 确定性构造：上涨基底上交替 ±3%（d60≈-0.058 浅、vol20≈0.062 高），
        # 避免随机游走回撤失控触发 red。
        n = 120
        base = np.linspace(100.0, 106.0, n)
        cycle = np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
        close = base * (1.0 + 0.03 * cycle)
        frame = _asset_frame(close.tolist())
        thresholds = self.thresholds
        assets = _assets(n_type="fund")  # → volatility_yellow_etf=0.045
        flags = assess_red_flags(assets, {"A1": frame}, thresholds)
        self.assertEqual(flags["A1"]["level"], "yellow")
        self.assertEqual(flags["A1"]["triggered_by"], ["volatility_20d"])
        self.assertTrue(flags["A1"]["approval_required"] is True)

    def test_drawdown_priority_over_volatility(self) -> None:
        # drawdown 深到 red 同时 vol 超高 → level 仍 red（回撤优先）。
        rng = np.random.default_rng(9)
        n = 120
        peak = 100.0
        pre = list(np.linspace(95.0, peak, 60))
        post = list(np.linspace(peak, peak * 0.80, 60))  # drawdown=-0.20 → red
        close = pd.Series(pre + post)
        frame = _asset_frame(close.tolist())
        thresholds = self.thresholds
        flags = assess_red_flags(_assets(), {"A1": frame}, thresholds)
        self.assertEqual(flags["A1"]["level"], "red")
        self.assertEqual(flags["A1"]["triggered_by"], ["drawdown_60d"])

    def test_returns_only_risk_assets(self) -> None:
        assets = _assets()
        safe = _frame_with_drawdown(-0.05)
        risky = _frame_with_drawdown(-0.20)
        aligned = {"A1": safe, "A2": risky}
        thresholds = self.thresholds
        flags = assess_red_flags(assets, aligned, thresholds)
        self.assertNotIn("A1", flags)
        self.assertEqual(flags["A2"]["level"], "red")
        # 空帧资产（quality D）不出现在结果。
        flags2 = assess_red_flags(assets, {"A1": pd.DataFrame(), "A2": risky}, thresholds)
        self.assertNotIn("A1", flags2)

    def test_none_thresholds_returns_empty(self) -> None:
        assets = _assets()
        frame = _frame_with_drawdown(-0.30)
        self.assertEqual(assess_red_flags(assets, {"A1": frame}, None), {})

    def test_type_mapping(self) -> None:
        thresholds = self.thresholds
        # 高波动下，type=stock→0.035 触发黄；type=bond_etf→0.015 也触发（但更低）。
        rng = np.random.default_rng(11)
        n = 120
        close = 100.0 * np.cumprod(1.0 + rng.normal(0.0, 0.04, n))
        frame = _asset_frame(close.tolist())
        # stock: 0.04 日波动 >= 0.035 → yellow。
        flags_stock = assess_red_flags(
            pd.DataFrame({"asset_id": ["A1"], "type": ["stock"]}),
            {"A1": frame}, thresholds)
        self.assertEqual(flags_stock["A1"]["level"], "yellow")

    def test_no_chinese_in_output(self) -> None:
        rng = np.random.default_rng(13)
        n = 120
        close = 100.0 * np.cumprod(1.0 + rng.normal(0.0, 0.06, n))
        frame = _asset_frame(close.tolist())
        flags = assess_red_flags(_assets(), {"A1": frame}, self.thresholds)
        for asset_id, flag in flags.items():
            self.assertRegex(asset_id, _ASCII)
            self.assertRegex(flag["level"], _ASCII)
            for key, value in flag.items():
                if isinstance(value, str):
                    self.assertRegex(value, _ASCII, f"{asset_id}.{key}")
            self.assertEqual(flag["approval_required"], True)

    def test_short_history_no_flag(self) -> None:
        # 历史不足 min_periods → drawdown/vol None → 无风险。
        frame = _asset_frame([100.0] * 10)
        flags = assess_red_flags(_assets(), {"A1": frame}, self.thresholds)
        self.assertNotIn("A1", flags)


if __name__ == "__main__":
    unittest.main()
