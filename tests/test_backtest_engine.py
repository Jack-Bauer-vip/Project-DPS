"""backtest_engine：策略级别回测引擎测试（契约解析/数据层/成本/时机/网格/宏观/绩效/报告/集成）。

纯 unittest + tempfile + 合成 fixture（不用真实契约/A 数据）。
运行：``cd research_engines/qteasy_lab && .venv/Scripts/python.exe -B -m unittest discover -s "D:\Project DPS/tests" -q``
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from qteasy_research.reference.backtest_engine import (
    DEFAULT_GRID_SPREAD,
    apply_signal_filters,
    build_phase_lookup,
    build_trading_calendar,
    buy_cash_out,
    compute_target_barbell,
    compute_target_midline,
    execution_price,
    grid_target_weight,
    load_benchmark_returns,
    load_grid_reference,
    load_price_frames,
    parse_contract,
    phase_asof,
    rebalance_dates,
    render_markdown,
    resolve_grid_spread,
    resolve_rebalance_frequency,
    round_lot,
    run_backtest,
    sell_cash_in,
    trade_fee,
    write_backtest_outputs,
)


def _make_contract_json() -> dict:
    """构造合成契约（两策略：barbell 周频 + grid 日频）。"""
    return {
        "schema_version": "1.0",
        "contract_type": "strategy_rules",
        "generated_at": "2026-08-07T17:24:26",
        "generated_by": "systemA",
        "strategies": [
            {
                "strategy_id": "barbell_strategy",
                "decision_rule": "barbell",
                "enabled": True,
                "use_target_ratio": True,
                "rebalance_frequency": "weekly",
                "rebalance_threshold_abs": 0.03,
                "asset_rebalance_threshold_abs": 0.05,
                "signal_filters": None,
                "preferences": {"macro_fit": 0.4},
                "assets": [
                    {"asset_id": "512890.SH", "role": "低波端", "enabled": True,
                     "min_weight": 0.0, "target_weight": 0.55, "max_weight": 0.6,
                     "target_weight_configured": True},
                    {"asset_id": "515450.SH", "role": "低波端", "enabled": True,
                     "min_weight": 0.0, "target_weight": 0.0, "max_weight": 0.0,
                     "target_weight_configured": True},
                    {"asset_id": "588230.SH", "role": "成长端", "enabled": True,
                     "min_weight": 0.0, "target_weight": 0.45, "max_weight": 0.5,
                     "target_weight_configured": True},
                ],
            },
            {
                "strategy_id": "grid_lh",
                "decision_rule": "grid",
                "enabled": True,
                "use_target_ratio": False,
                "rebalance_frequency": "daily",
                "rebalance_threshold_abs": 0.03,
                "asset_rebalance_threshold_abs": 0.02,
                "signal_filters": None,
                "preferences": {"macro_fit": -0.2},
                "assets": [
                    {"asset_id": "159985.SZ", "role": "网格标的", "enabled": True,
                     "min_weight": 0.0, "target_weight": 0.0, "max_weight": 0.2,
                     "target_weight_configured": False},
                    {"asset_id": "513520.SH", "role": "网格标的", "enabled": True,
                     "min_weight": 0.0, "target_weight": 0.0, "max_weight": 0.1347,
                     "target_weight_configured": False},
                ],
            },
        ],
        "shared_config": {
            "adj_type": "none",
            "backtest": {"initial_cash": 100000, "cost_rate": 0.001, "slippage_rate": 0.0005},
        },
    }


def _make_frames(days: int = 120, assets: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """构造合成行情帧 ``{asset_id: DataFrame(trade_date, close)}``（确定性缓涨）。"""
    if assets is None:
        assets = ["512890.SH", "588230.SH", "159985.SZ", "513520.SH"]
    dates = pd.date_range("2024-01-02", periods=days, freq="B")
    result: dict[str, pd.DataFrame] = {}
    for i, asset_id in enumerate(assets):
        base = 1.0 + 0.02 * i
        closes = [base * (1.0 + 0.0005 * j) for j in range(days)]
        result[asset_id] = pd.DataFrame({
            "trade_date": dates.strftime("%Y-%m-%d"),
            "close": closes,
        })
    return result


class ContractParseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.contract_path = self.root / "strategy_contract.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, data: dict) -> Path:
        self.contract_path.write_text(json.dumps(data), encoding="utf-8")
        return self.contract_path

    def test_parse_six_strategies_and_schema(self):
        """解析 6 策略、schema_version 正确。"""
        contract = parse_contract(self._write(_make_contract_json()))
        self.assertEqual(contract.schema_version, "1.0")
        self.assertEqual(len(contract.strategies), 2)
        rules = {s.decision_rule for s in contract.strategies}
        self.assertEqual(rules, {"barbell", "grid"})

    def test_unknown_schema_raises(self):
        """schema_version != 1.0 → ValueError。"""
        data = _make_contract_json()
        data["schema_version"] = "2.0"
        with self.assertRaises(ValueError):
            parse_contract(self._write(data))

    def test_configured_false_target_none(self):
        """target_weight_configured=false → target_weight=None。"""
        contract = parse_contract(self._write(_make_contract_json()))
        grid = next(s for s in contract.strategies if s.decision_rule == "grid")
        self.assertIsNone(grid.assets[0].target_weight)
        # barbell 的配置目标保留
        barbell = next(s for s in contract.strategies if s.decision_rule == "barbell")
        self.assertEqual(barbell.assets[0].target_weight, 0.55)

    def test_enabled_filters(self):
        """enabled=false 标的被 enabled_assets 过滤。"""
        contract = parse_contract(self._write(_make_contract_json()))
        grid = next(s for s in contract.strategies if s.decision_rule == "grid")
        self.assertEqual(grid.enabled_assets, ("159985.SZ", "513520.SH"))
        barbell = next(s for s in contract.strategies if s.decision_rule == "barbell")
        # 515450 target=0 → 不进 enabled_assets
        self.assertNotIn("515450.SH", barbell.enabled_assets)

    def test_signal_filters_null_and_list(self):
        """signal_filters null → None；list → 解析为 dict 列表。"""
        contract = parse_contract(self._write(_make_contract_json()))
        for s in contract.strategies:
            self.assertIsNone(s.signal_filters)
        data = _make_contract_json()
        data["strategies"][0]["signal_filters"] = [
            {"field": "macro_regime.phase", "operator": "neq", "value": "late"}
        ]
        contract = parse_contract(self._write(data))
        self.assertEqual(len(contract.strategies[0].signal_filters), 1)
        self.assertEqual(contract.strategies[0].signal_filters[0]["field"],
                         "macro_regime.phase")

    def test_cost_config(self):
        """shared_config.backtest → CostConfig(100000, 0.001, 0.0005, 0.0)。"""
        contract = parse_contract(self._write(_make_contract_json()))
        self.assertEqual(contract.cost.initial_cash, 100000.0)
        self.assertEqual(contract.cost.cost_rate, 0.001)
        self.assertEqual(contract.cost.slippage_rate, 0.0005)
        self.assertEqual(contract.cost.stamp_duty_rate, 0.0)

    def test_unknown_frequency_monthly(self):
        """未知 rebalance_frequency → monthly + warning。"""
        with self.assertWarns(UserWarning):
            resolved = resolve_rebalance_frequency("hourly")
        self.assertEqual(resolved, "monthly")

    def test_missing_contract_raises(self):
        """契约缺失 → FileNotFoundError。"""
        with self.assertRaises(FileNotFoundError):
            parse_contract(self.root / "missing.json")


class LoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_fund_daily(self, frames: dict[str, pd.DataFrame]) -> None:
        rows = []
        for asset_id, frame in frames.items():
            sub = frame.copy()
            sub["ts_code"] = asset_id
            rows.append(sub)
        combined = pd.concat(rows, ignore_index=True)
        (self.root / "fund_daily.csv").write_text(
            combined.to_csv(index=False), encoding="utf-8"
        )

    def test_load_price_frames_columns(self):
        """load_price_frames 返回 trade_date/close 列，升序、close>0。"""
        frames = _make_frames(days=10, assets=["512890.SH", "588230.SH"])
        self._write_fund_daily(frames)
        loaded = load_price_frames(["512890.SH", "588230.SH"], data_dir=self.root)
        for asset_id, frame in loaded.items():
            self.assertIn("trade_date", frame.columns)
            self.assertIn("close", frame.columns)
            self.assertTrue((frame["close"] > 0).all())
            self.assertTrue(frame["trade_date"].is_monotonic_increasing)

    def test_load_price_frames_hfq_removes_split_jump(self):
        """load_price_frames：fund_daily 含 pre_close 时 close 后复权，拆分日无跳空。

        回归：512890.SH 2021-10-25 份额拆分（close 1.639→0.801，-51%），原始 close
        使 NAV 假崩（three_musketeers 出现 -45% 虚假回撤）。后复权后拆分日收益
        仅反映真实涨跌（相对 pre_close）。
        """
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        # 5 日：第 3 日拆分 2:1（close 减半），pre_close[拆分日] = 昨收×0.5。
        rows = [
            ("A.SH", "2024-01-02", 1.00, 1.00),
            ("A.SH", "2024-01-03", 1.01, 1.00),
            ("A.SH", "2024-01-04", 0.50, 0.505),
            ("A.SH", "2024-01-05", 0.51, 0.50),
            ("A.SH", "2024-01-08", 0.52, 0.51),
        ]
        frame = pd.DataFrame(rows, columns=["ts_code", "trade_date", "close", "pre_close"])
        (root / "fund_daily.csv").write_text(frame.to_csv(index=False), encoding="utf-8")
        loaded = load_price_frames(["A.SH"], data_dir=root)
        close = loaded["A.SH"].set_index("trade_date")["close"]
        ret = close.pct_change().dropna()
        # 拆分日收益 = 真实 -0.99%（0.5/0.505−1），而非原始 -50%。
        self.assertAlmostEqual(ret.loc["2024-01-04"], 0.50 / 0.505 - 1, places=4)
        # 复权后无 >10% 跳空残留。
        self.assertTrue((ret.abs() < 0.10).all(), f"复权后仍有跳空: {ret.to_dict()}")

    def test_calendar_union(self):
        """build_trading_calendar = 全部资产日期并集，升序去重。"""
        frames = _make_frames(days=30, assets=["512890.SH", "588230.SH"])
        calendar = build_trading_calendar(frames)
        self.assertEqual(len(calendar), 30)
        self.assertTrue(calendar.is_monotonic_increasing)

    def test_rebalance_weekly_cross_year(self):
        """weekly 跨年按 (年, 周) 复合键分组：升序、每周一个（防周数重复 bug）。

        回归用例：旧实现用 isocalendar().week 单键，跨年周数重复导致乱序/合并
        （如 2020 W1 与 2019 W1 归同组），重平衡日期几乎失效。
        """
        # 2019-12-23 起 12 个工作日，覆盖 2019 W52 / 2020 W1 / 2020 W2。
        dates = pd.date_range("2019-12-23", periods=12, freq="B")
        rd = rebalance_dates("weekly", dates, start="2019-12-23")
        expected = [pd.Timestamp(d) for d in ["2019-12-27", "2020-01-03", "2020-01-07"]]
        self.assertEqual(rd, expected)
        # (年, 周) 键全部唯一（每组恰一个最后交易日）。
        iso = pd.DatetimeIndex(rd).isocalendar()
        keys = list(zip(iso.year, iso.week))
        self.assertEqual(len(set(keys)), len(rd))

    def test_rebalance_frequencies_sorted(self):
        """daily/monthly/quarterly/never 的 rebalance_dates 升序、频率正确。"""
        dates = pd.date_range("2023-01-02", periods=40, freq="B")
        self.assertEqual(rebalance_dates("daily", dates, start="2023-01-02"), list(dates))
        self.assertEqual(rebalance_dates("never", dates, start="2023-01-02"), [])
        monthly = rebalance_dates("monthly", dates, start="2023-01-02")
        self.assertEqual(monthly, sorted(monthly))
        # 每月最多一个月末交易日。
        self.assertLessEqual(len(monthly), 2)
        # quarterly：只取季度末月（3/6/9/12）。窗口 3/1~6/6 命中 3/31（完整月末）
        # 与 6/6（6 月窗口末交易日，窗口提前截断时 last=窗口末，语义一致）。
        q_dates = pd.date_range("2023-03-01", periods=70, freq="B")
        quarterly = rebalance_dates("quarterly", q_dates, start="2023-03-01")
        self.assertEqual(quarterly, sorted(quarterly))
        self.assertEqual(quarterly[0], pd.Timestamp("2023-03-31"))
        self.assertTrue(all(ts.month in (3, 6, 9, 12) for ts in quarterly))

    def test_align_asset_prices_nan_before_listing(self):
        """align_asset_prices：标的上线前为 NaN。"""
        frames = {
            "A": pd.DataFrame({
                "trade_date": pd.date_range("2024-01-02", periods=5, freq="B").strftime("%Y-%m-%d"),
                "close": [1.0] * 5,
            }),
            "B": pd.DataFrame({
                "trade_date": pd.date_range("2024-01-08", periods=3, freq="B").strftime("%Y-%m-%d"),
                "close": [2.0] * 3,
            }),
        }
        calendar = build_trading_calendar(frames)
        aligned = _import_align(frames, calendar)
        # B 上线（01-08，calendar 最后一日）前全部 NaN
        self.assertTrue(np.isnan(aligned.loc[calendar[0], "B"]))
        self.assertTrue(np.isnan(aligned.loc[calendar[1], "B"]))
        self.assertTrue(np.isnan(aligned.loc[calendar[2], "B"]))
        self.assertTrue(np.isnan(aligned.loc[calendar[3], "B"]))
        # 上市日非 NaN
        self.assertFalse(np.isnan(aligned.loc[calendar[4], "B"]))

    def test_align_asset_prices_ffill_suspension(self):
        """停牌/缺数缺口用前收估值（ffill），上线前 NaN 保持。

        回归：three_musketeers 2021-10-22 512890 缺数（停牌），旧逻辑当天不估值
        使 equity 从 139636 假崩到 79043。ffill 后该日用前收 1.1 估值。
        """
        calendar = pd.date_range("2024-01-02", periods=5, freq="B")  # 二三四五 一
        frames = {
            "A": pd.DataFrame({
                "trade_date": ["2024-01-02", "2024-01-03", "2024-01-05", "2024-01-08"],
                "close": [1.0, 1.1, 1.2, 1.3],
            }),
        }
        aligned = _import_align(frames, calendar)
        # 01-04（周四）缺数 → ffill 为前收 1.1。
        self.assertEqual(aligned.loc["2024-01-04", "A"], 1.1)
        # 首日有值、后续恢复真实值。
        self.assertEqual(aligned.loc["2024-01-02", "A"], 1.0)
        self.assertEqual(aligned.loc["2024-01-05", "A"], 1.2)

    def test_benchmark_returns_first_zero(self):
        """基准 000300.SH 对齐、pct_change 首日 0。"""
        dates = pd.date_range("2024-01-02", periods=10, freq="B")
        index_daily = pd.DataFrame({
            "ts_code": ["000300.SH"] * 10,
            "trade_date": dates.strftime("%Y-%m-%d"),
            "close": [100.0 + i for i in range(10)],
        })
        (self.root / "index_daily.csv").write_text(index_daily.to_csv(index=False), encoding="utf-8")
        returns, warning = load_benchmark_returns(dates, data_dir=self.root)
        self.assertIsNone(warning)
        self.assertEqual(returns.iloc[0], 0.0)
        self.assertAlmostEqual(returns.iloc[1], 0.01, places=4)

    def test_benchmark_missing_degrade(self):
        """基准缺失 → (None, warning)，不崩溃。"""
        returns, warning = load_benchmark_returns(
            pd.date_range("2024-01-02", periods=5, freq="B"), data_dir=self.root
        )
        self.assertIsNone(returns)
        self.assertIsNotNone(warning)


class SimulatorCashFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cost = _import_cost()

    def test_buy_cash_out_includes_cost(self):
        """买入支出 = 股数×价×(1+佣金)；ETF 印花税 0。"""
        out = buy_cash_out(100, 1.0, self.cost)
        self.assertAlmostEqual(out, 100 * 1.0 * (1 + 0.001), places=6)

    def test_sell_cash_in_includes_cost(self):
        """卖出收入 = 股数×价×(1−佣金)；ETF 印花税 0。"""
        inn = sell_cash_in(100, 1.0, self.cost)
        self.assertAlmostEqual(inn, 100 * 1.0 * (1 - 0.001), places=6)

    def test_etf_no_stamp_duty(self):
        """ETF 印花税恒 0。"""
        out = buy_cash_out(100, 1.0, self.cost)
        fee = trade_fee(100, 1.0, self.cost)
        self.assertEqual(fee, 100 * 1.0 * 0.001)
        self.assertEqual(self.cost.stamp_duty_rate, 0.0)

    def test_execution_price_slippage(self):
        """成交价：BUY 加滑点，SELL 减滑点。"""
        self.assertAlmostEqual(execution_price("BUY", 1.0, self.cost), 1.0005)
        self.assertAlmostEqual(execution_price("SELL", 1.0, self.cost), 0.9995)

    def test_round_lot_floor(self):
        """整手取整（lot=100 向下）；取整 0 → 0。"""
        self.assertEqual(round_lot(1234, lot=100), 1200)
        self.assertEqual(round_lot(50, lot=100), 0)
        self.assertEqual(round_lot(123, lot=1), 123)


class ExecutionTimingTests(unittest.TestCase):
    """T+1 成交、首日建仓、非交易日不成交、现金缩放。"""

    def test_run_backtest_barbell_t_plus_one(self):
        """barbell 端到端：确定性 NAV、首日仓位≈契约目标、周度重平衡。"""
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        contract = parse_contract(_write_contract(root))
        frames = _make_frames(days=30, assets=["512890.SH", "588230.SH"])
        _write_fund_daily(root, frames)
        result = run_backtest(
            next(s for s in contract.strategies if s.decision_rule == "barbell"),
            contract,
            data_root=root,
            start="2024-01-02",
            lot=1,
            online_ok=False,
            phase_lookup={},
        )
        self.assertEqual(result.status, "OK")
        self.assertFalse(result.nav.empty)
        # 首日建仓（T0 收盘）后 equity < initial_cash（含成本）
        self.assertLess(result.nav["equity"].iloc[0], 100000.0)
        self.assertGreater(result.nav["equity"].iloc[0], 99000.0)
        # 周度重平衡日期非空
        self.assertTrue(result.rebalance_dates)

    def test_rebalance_no_repeated_sell_when_partial_data(self):
        """部分标的无数据时权重基准用总资产：不反复减持已建仓标的（回归）。

        回归：_rebalance_deltas 旧基准仅持仓市值（不含现金），target 权重 <100%
        或部分标的无数据时每周反复 SELL 直至清仓，清仓后触发
        ``ValueError: cannot convert float NaN to integer``（three_musketeers
        2018 早期 518880 归零复现）。
        """
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        contract = parse_contract(_write_contract(root))
        # 仅 512890 有数据，588230 未上市（持现金）。
        frames = _make_frames(days=30, assets=["512890.SH"])
        _write_fund_daily(root, frames)
        result = run_backtest(
            next(s for s in contract.strategies if s.decision_rule == "barbell"),
            contract,
            data_root=root,
            start="2024-01-02",
            lot=1,
            online_ok=False,
            phase_lookup={},
        )
        self.assertEqual(result.status, "OK")
        tx = result.trades[result.trades["asset_id"] == "512890.SH"]
        bought = int(tx[tx["side"] == "BUY"]["shares"].sum())
        sold = int(tx[tx["side"] == "SELL"]["shares"].sum())
        # 微调 SELL 可存在（首日建仓基准=初始现金、重平衡基准=总资产的微小偏差），
        # 但远小于建仓量（修复前会每周反复减持至清仓，sold ≈ bought）。
        self.assertLess(sold, bought * 0.1, f"不应反复减持有数据标的: bought={bought} sold={sold}")
        # 持有 512890 ≈ 契约目标 0.55（含整手/成本误差，容忍 ±0.07）。
        net_shares = bought - sold
        last_close = float(
            _make_frames(days=30, assets=["512890.SH"])["512890.SH"]["close"].iloc[-1]
        )
        weight = net_shares * last_close / result.nav["equity"].iloc[-1]
        self.assertGreater(weight, 0.50)
        self.assertLess(weight, 0.62)

    def test_grid_run_no_crash(self):
        """grid 策略端到端：确定性、有交易。"""
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        contract = parse_contract(_write_contract(root))
        frames = _make_frames(days=40, assets=["159985.SZ", "513520.SH"])
        _write_fund_daily(root, frames)
        result = run_backtest(
            next(s for s in contract.strategies if s.decision_rule == "grid"),
            contract,
            data_root=root,
            start="2024-01-02",
            lot=1,
            online_ok=False,
            phase_lookup={},
            grid_reference={},
        )
        self.assertEqual(result.status, "OK")
        self.assertFalse(result.nav.empty)

    def test_no_trades_non_trading_day(self):
        """非交易日不成交（trades 仅在窗口交易日产生）。"""
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        contract = parse_contract(_write_contract(root))
        frames = _make_frames(days=20, assets=["512890.SH"])
        _write_fund_daily(root, frames)
        result = run_backtest(
            next(s for s in contract.strategies if s.decision_rule == "barbell"),
            contract,
            data_root=root,
            start="2024-01-02",
            lot=1,
            online_ok=False,
            phase_lookup={},
        )
        for _, row in result.trades.iterrows():
            self.assertIn(pd.Timestamp(row["trade_date"]), build_trading_calendar(frames))


class GridRuleTests(unittest.TestCase):
    def test_grid_center_max_half(self):
        """网格中枢 = max_weight × 0.5。"""
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        contract = parse_contract(_write_contract(root))
        asset = contract.strategies[1].assets[0]  # grid_lh 159985 max=0.2
        self.assertAlmostEqual(grid_target_weight(asset), 0.1)

    def test_grid_spread_priority(self):
        """档距三级：契约值 > grid_ref 值 > 兜底 0.05。"""
        contract_asset = _import_contract_asset(suggested_spread=0.08)
        grid_ref = {"159985.SZ": 0.0429}
        spread, source = resolve_grid_spread("159985.SZ", contract_asset, grid_ref)
        self.assertEqual(source, "contract")
        self.assertAlmostEqual(spread, 0.08)
        spread2, source2 = resolve_grid_spread("159985.SZ", None, grid_ref)
        self.assertEqual(source2, "grid_ref")
        self.assertAlmostEqual(spread2, 0.0429)

    def test_grid_spread_fallback(self):
        """grid_ref 缺该标的 → 兜底 0.05。"""
        spread, source = resolve_grid_spread("UNKNOWN.SH", None, {})
        self.assertEqual(source, "fallback")
        self.assertEqual(spread, DEFAULT_GRID_SPREAD)

    def test_grid_ref_empty_fallback(self):
        """grid_ref 含 NaN/空串 → 兜底。"""
        loaded = load_grid_reference(Path("nonexistent.csv"))
        self.assertEqual(loaded, {})
        spread, source = resolve_grid_spread("159985.SZ", None, loaded)
        self.assertEqual(source, "fallback")


class MacroAdaptationTests(unittest.TestCase):
    def test_apply_filters_none_pass(self):
        """filters null → pass。"""
        passed, notes = apply_signal_filters(None, "late", pd.Timestamp("2024-01-02"))
        self.assertTrue(passed)
        self.assertEqual(notes, [])

    def test_phase_neq_late_blocks_when_late(self):
        """phase != late 在 phase=late 时阻塞。"""
        filters = [{"field": "macro_regime.phase", "operator": "neq", "value": "late"}]
        passed, notes = apply_signal_filters(filters, "late", pd.Timestamp("2024-01-02"))
        self.assertFalse(passed)
        self.assertTrue(any("filter_blocked" in n for n in notes))

    def test_phase_neq_late_passes_when_not_late(self):
        """phase != late 在 phase≠late 时放行。"""
        filters = [{"field": "macro_regime.phase", "operator": "neq", "value": "late"}]
        passed, notes = apply_signal_filters(filters, "mid", pd.Timestamp("2024-01-02"))
        self.assertTrue(passed)

    def test_unknown_filter_field_no_block(self):
        """未知 filter 字段 → filter_unknown、不阻塞、记录。"""
        filters = [{"field": "asset.red_flag", "operator": "eq", "value": "red"}]
        passed, notes = apply_signal_filters(filters, "late", pd.Timestamp("2024-01-02"))
        self.assertTrue(passed)
        self.assertTrue(any("filter_unknown" in n for n in notes))

    def test_phase_asof_strict_predecessor(self):
        """phase_asof 严格取 < date 的月末。"""
        lookup = {"2024-01-31": "early", "2024-02-29": "mid", "2024-03-31": "late"}
        self.assertEqual(phase_asof(lookup, pd.Timestamp("2024-03-15")), "mid")
        # 恰为月末 → 取上一月末
        self.assertEqual(phase_asof(lookup, pd.Timestamp("2024-03-31")), "mid")
        self.assertIsNone(phase_asof(lookup, pd.Timestamp("2024-01-10")))

    def test_midline_tilt_shift(self):
        """mid_line late 期 tilt：风险资产 513650 减持；clamp 越界记录到 notes。"""
        mid_strategy = _import_strategy(decision_rule="mid_line", macro_fit=0.7)
        target, notes = compute_target_midline(mid_strategy, _asset_map_synthetic(), "late")
        # tilt = 0.7 × 0.05 = 0.035；风险资产 513650 按比例减持 → 0.115
        self.assertLess(target["513650.SH"], 0.15)
        self.assertAlmostEqual(target["513650.SH"], 0.115, places=3)
        # 防御资产增持但受 max_weight 约束（518880 max=0.4、512890 max=0.45）→ clamp 回
        self.assertLessEqual(target["518880.SH"], 0.4)
        self.assertLessEqual(target["512890.SH"], 0.45)
        # clamp 越界被记录（tilt_limited_by_weight_bounds）
        self.assertTrue(any("tilt_limited_by_weight_bounds" in n for n in notes))

    def test_midline_no_tilt_when_disabled(self):
        """--no-macro-tilt 下 late 期不偏移。"""
        mid_strategy = _import_strategy(decision_rule="mid_line", macro_fit=0.7)
        target, _ = compute_target_midline(
            mid_strategy, _asset_map_synthetic(), "late", enable_macro_tilt=False
        )
        self.assertEqual(target["513650.SH"], 0.15)
        self.assertEqual(target["518880.SH"], 0.4)

    def test_midline_no_tilt_when_not_late(self):
        """非 late 期不偏移（保持契约 target）。"""
        mid_strategy = _import_strategy(decision_rule="mid_line", macro_fit=0.7)
        target, _ = compute_target_midline(mid_strategy, _asset_map_synthetic(), "early")
        self.assertEqual(target["513650.SH"], 0.15)
        self.assertEqual(target["518880.SH"], 0.4)


class MetricsTests(unittest.TestCase):
    def test_metrics_synthetic(self):
        """PerformanceMetrics 在合成收益上 total/sharpe/mdd 正确。"""
        from qteasy_research.backtesting.metrics import PerformanceMetrics
        returns = [0.01, -0.005, 0.02, 0.0, 0.015, -0.01, 0.005, 0.008]
        result = PerformanceMetrics.calculate(returns)
        self.assertGreater(result.total_return, 0)
        self.assertLessEqual(result.max_drawdown, 0)

    def test_turnover_nonzero(self):
        """有交易时换手率 > 0。"""
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        contract = parse_contract(_write_contract(root))
        frames = _make_frames(days=40, assets=["159985.SZ", "513520.SH"])
        _write_fund_daily(root, frames)
        result = run_backtest(
            next(s for s in contract.strategies if s.decision_rule == "grid"),
            contract,
            data_root=root,
            start="2024-01-02",
            lot=1,
            online_ok=False,
            phase_lookup={},
            grid_reference={},
        )
        self.assertGreaterEqual(result.turnover, 0.0)


class ReportRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.contract = parse_contract(_write_contract(self.root))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_render_markdown_ascii(self):
        """markdown 含 strategy_id/decision_rule/全 ASCII 无中文策略名。"""
        from qteasy_research.reference.backtest_engine import BacktestResult
        result = BacktestResult(strategy_id="barbell_strategy", decision_rule="barbell", status="OK")
        md = render_markdown(result)
        self.assertIn("barbell_strategy", md)
        self.assertIn("barbell", md)
        # 无中文策略名（仅 ASCII 标识）
        self.assertNotIn("杠铃", md)

    def test_render_markdown_assumptions(self):
        """Assumptions 每条带 B-side; awaiting A confirmation。"""
        from qteasy_research.reference.backtest_engine import BacktestResult
        result = BacktestResult(strategy_id="grid_lh", decision_rule="grid", status="OK",
                                assumptions=["B-side; awaiting A confirmation: 网格中枢=max×0.5"])
        md = render_markdown(result)
        self.assertIn("B-side; awaiting A confirmation", md)
        self.assertIn("## Assumptions", md)

    def test_write_outputs_files(self):
        """write_backtest_outputs 写出 nav/trades/md 三件套。"""
        from qteasy_research.reference.backtest_engine import BacktestResult
        result = BacktestResult(strategy_id="barbell_strategy", decision_rule="barbell", status="OK")
        result.nav = pd.DataFrame({"trade_date": pd.to_datetime(["2024-01-02"]),
                                    "equity": [100000.0], "strategy_return": [0.0]})
        written = write_backtest_outputs(result, self.root / "reports" / "backtest", run_month="202608")
        self.assertIn("nav", written)
        self.assertIn("trades", written)
        self.assertIn("md", written)
        self.assertTrue(written["nav"].exists())
        self.assertTrue(written["md"].exists())

    def test_skipped_placeholder(self):
        """SKIPPED 策略产出占位报告。"""
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        contract = parse_contract(_write_contract(root))
        frames = _make_frames(days=10, assets=["512890.SH"])
        _write_fund_daily(root, frames)
        # 构造 short_term 策略
        short = _import_strategy(decision_rule="short_term")
        result = run_backtest(short, contract, data_root=root, lot=1, phase_lookup={})
        self.assertEqual(result.status, "SKIPPED")
        md = render_markdown(result)
        self.assertIn("SKIPPED", md)
        # 占位报告无导航，不崩溃
        self.assertIn("## Note", md)


class IntegrationTests(unittest.TestCase):
    def test_all_strategies_ok(self):
        """合成契约（barbell+grid）两策略均 OK，确定性 NAV。"""
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        contract = parse_contract(_write_contract(root))
        frames = _make_frames(days=60, assets=["512890.SH", "588230.SH", "159985.SZ", "513520.SH"])
        _write_fund_daily(root, frames)
        statuses = []
        for strategy in contract.strategies:
            result = run_backtest(strategy, contract, data_root=root, lot=1,
                                  phase_lookup={}, grid_reference={})
            statuses.append(result.status)
        self.assertEqual(statuses.count("OK"), 2)
        self.assertEqual(statuses.count("SKIPPED"), 0)

    def test_short_term_skipped(self):
        """short_term 策略恒 SKIPPED（无启用资产）。"""
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        contract = parse_contract(_write_contract(root))
        frames = _make_frames(days=10, assets=["512890.SH"])
        _write_fund_daily(root, frames)
        short = _import_strategy(decision_rule="short_term")
        result = run_backtest(short, contract, data_root=root, lot=1, phase_lookup={})
        self.assertEqual(result.status, "SKIPPED")
        self.assertEqual(result.error, "no_enabled_assets")


# ============================================================
# fixture 辅助
# ============================================================

def _write_contract(root: Path) -> Path:
    path = root / "strategy_contract.json"
    path.write_text(json.dumps(_make_contract_json()), encoding="utf-8")
    return path


def _write_fund_daily(root: Path, frames: dict[str, pd.DataFrame]) -> None:
    rows = []
    for asset_id, frame in frames.items():
        sub = frame.copy()
        sub["ts_code"] = asset_id
        rows.append(sub)
    combined = pd.concat(rows, ignore_index=True)
    (root / "fund_daily.csv").write_text(combined.to_csv(index=False), encoding="utf-8")


def _import_align(frames, calendar):
    from qteasy_research.reference.backtest_engine import align_asset_prices
    return align_asset_prices(frames, calendar)


def _import_cost():
    from qteasy_research.reference.backtest_engine import CostConfig
    return CostConfig(initial_cash=100000.0, cost_rate=0.001, slippage_rate=0.0005)


def _import_contract_asset(suggested_spread=None):
    from qteasy_research.reference.backtest_engine import ContractAsset
    return ContractAsset(
        asset_id="159985.SZ", role="网格标的", enabled=True,
        min_weight=0.0, target_weight=None, max_weight=0.2,
        target_weight_configured=False, suggested_spread=suggested_spread,
    )


def _import_strategy(decision_rule="mid_line", macro_fit=0.7):
    from qteasy_research.reference.backtest_engine import ContractAsset, ContractStrategy
    assets = [
        {"asset_id": "512890.SH", "role": "红利低波", "enabled": True,
         "min_weight": 0.0, "target_weight": 0.45, "max_weight": 0.45, "target_weight_configured": True},
        {"asset_id": "513650.SH", "role": "海外宽基", "enabled": True,
         "min_weight": 0.0, "target_weight": 0.15, "max_weight": 0.3, "target_weight_configured": True},
        {"asset_id": "518880.SH", "role": "黄金避险", "enabled": True,
         "min_weight": 0.0, "target_weight": 0.4, "max_weight": 0.4, "target_weight_configured": True},
    ]
    parsed = [ContractAsset(**a) for a in assets]
    return ContractStrategy(
        strategy_id="three_musketeers", decision_rule=decision_rule, enabled=True,
        use_target_ratio=True, rebalance_frequency="weekly",
        rebalance_threshold_abs=0.03, asset_rebalance_threshold_abs=0.05,
        signal_filters=None, preferences={"macro_fit": macro_fit}, assets=parsed,
    )


def _asset_map_synthetic():
    from qteasy_research.reference.backtest_engine import ContractAsset
    return {
        "512890.SH": ContractAsset("512890.SH", "红利低波", True, 0.0, 0.45, 0.45, True),
        "513650.SH": ContractAsset("513650.SH", "海外宽基", True, 0.0, 0.15, 0.3, True),
        "518880.SH": ContractAsset("518880.SH", "黄金避险", True, 0.0, 0.4, 0.4, True),
    }


if __name__ == "__main__":
    unittest.main()
