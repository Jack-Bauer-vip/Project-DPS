"""reference/pipeline：端到端管线回归测试（临时 data_root + 临时集成目录）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from qteasy_research.reference import IntegrationDir, run_pipeline


class ReferencePipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_root = self.root / "data"
        self.data_root.mkdir()
        dates = pd.date_range("2024-01-02", periods=200, freq="B")
        n = len(dates)
        fund = pd.DataFrame({
            "ts_code": ["000001.SZ"] * n + ["164824.SZ"] * n,
            "trade_date": list(dates.strftime("%Y-%m-%d")) * 2,
            "close": list(100.0 + pd.Series(range(n)) * 0.05) * 2,
            "volume": [1000] * (2 * n),
            "amount": [10000] * (2 * n),
        })
        (self.data_root / "fund_daily.csv").write_text(fund.to_csv(index=False), encoding="utf-8")
        self.pool = self.root / "asset_pool.csv"
        pd.DataFrame({
            "asset_id": ["000001.SZ", "164824.SZ"],
            "code": ["000001", "164824"],
            "name": ["资产A", "资产B"],
            "type": ["fund", "fund"],
            "exchange": ["SZ", "SZ"],
            "theme": ["a", "b"],
            "status": ["active", "active"],
        }).to_csv(self.pool, index=False)
        # 系统A风控阈值 fixture（测试隔离：绝不让管线读到真实 A strategy_params.json）。
        self.risk_params = self.root / "strategy_params.json"
        self.risk_params.write_text(json.dumps({"risk_thresholds": {
            "red_drawdown": -0.18, "orange_drawdown": -0.12, "yellow_drawdown": -0.07,
            "volatility_yellow": 0.035, "volatility_yellow_etf": 0.045,
            "volatility_yellow_stock": 0.035, "volatility_yellow_bond_etf": 0.015,
        }}, ensure_ascii=False), encoding="utf-8")
        self.integration = IntegrationDir(self.root / "integration")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _run(self) -> dict:
        return run_pipeline(
            target_date="2026-08-06",
            data_root=self.data_root,
            integration=self.integration,
            online_ok=False,
            asset_pool=self.pool,
            risk_params=self.risk_params,
        )

    def _read_package(self) -> dict:
        return json.loads(
            (self.root / "integration" / "systemB_ref" / "20260806" / "decision_ref_package.json")
            .read_text(encoding="utf-8")
        )

    def _write_macro_series(self) -> None:
        """写 4 条宏观序列：DGS30 连续 8 月 +0.4 → 末 7 月 rate_up（phase=late）。"""
        macro_dir = self.data_root / "processed" / "global_macro"
        macro_dir.mkdir(parents=True)
        dates = pd.DatetimeIndex([
            "2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01",
            "2024-05-01", "2024-06-01", "2024-07-01", "2024-08-01",
        ])
        for series_id, values in [
            ("DGS30", [1.0, 1.4, 1.8, 2.2, 2.6, 3.0, 3.4, 3.8]),
            ("DGS10", [2.5] * 8),
            ("DGS2", [3.0] * 8),   # DGS10-DGS2=-0.5 → curve_inverted
            ("DFII10", [1.0] * 8),  # 平稳 → real_yield_stable
        ]:
            pd.DataFrame({
                "series_id": series_id,
                "observation_date": dates.strftime("%Y-%m-%d"),
                "available_at": (dates + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                "value": values,
                "quality_level": "A",
            }).to_csv(macro_dir / f"{series_id}.csv", index=False)

    def _write_stress_macro(self) -> None:
        """DGS30 每月 +0.6（7 个 ≥50bp 强加息月）→ rate_up_50bp 有样本。"""
        macro_dir = self.data_root / "processed" / "global_macro"
        macro_dir.mkdir(parents=True)
        dates = pd.DatetimeIndex([
            "2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01",
            "2024-05-01", "2024-06-01", "2024-07-01", "2024-08-01",
        ])
        for series_id, values in [
            ("DGS30", [1.0, 1.6, 2.2, 2.8, 3.4, 4.0, 4.6, 5.2]),
            ("DGS10", [2.5] * 8),
            ("DGS2", [3.0] * 8),
            ("DFII10", [1.0] * 8),
        ]:
            pd.DataFrame({
                "series_id": series_id,
                "observation_date": dates.strftime("%Y-%m-%d"),
                "available_at": (dates + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                "value": values,
                "quality_level": "A",
            }).to_csv(macro_dir / f"{series_id}.csv", index=False)

    def _write_risky_fund(self) -> None:
        """重写 fund_daily：000001.SZ 平稳上涨（无风险），164824.SZ 深回撤（red）。"""
        n = 120
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
        safe = list(100.0 + np.arange(n) * 0.05)
        risky = list(np.linspace(100.0, 150.0, 60)) + list(np.linspace(150.0, 120.0, 60))
        frame = pd.DataFrame({
            "ts_code": ["000001.SZ"] * n + ["164824.SZ"] * n,
            "trade_date": list(dates.strftime("%Y-%m-%d")) * 2,
            "close": safe + risky,
            "volume": [1000] * (2 * n),
            "amount": [10000] * (2 * n),
        })
        (self.data_root / "fund_daily.csv").write_text(frame.to_csv(index=False), encoding="utf-8")

    def test_pipeline_full_chain(self) -> None:
        record = self._run()
        self.assertEqual(record["status"], "COMPLETED")
        run_dir = self.root / "integration" / "systemB_ref" / "20260806"
        self.assertTrue((run_dir / ".ready").exists())
        self.assertTrue((run_dir / "package.json").exists())
        self.assertTrue((run_dir / "decision_ref_package.json").exists())
        self.assertTrue((run_dir / "assets_metadata.csv").exists())
        self.assertTrue(record["verify"]["ok"])
        # 决策包内容
        package = json.loads((run_dir / "decision_ref_package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["approval_policy"], "REFERENCE_ONLY")
        self.assertEqual(package["data_asof"], "2026-08-06")
        self.assertEqual(package["source_system"], "systemB")
        self.assertEqual(len(package["assets"]), 2)
        first = package["assets"][0]
        self.assertIn("20d", first["volatility"])
        self.assertEqual(first["data_quality"]["quality_level"], "A")
        # name 规范化：000001.SZ 未命中规范表 → 透传 A 侧 name；164824.SZ 命中 → 覆盖。
        by_id = {asset["asset_id"]: asset for asset in package["assets"]}
        self.assertEqual(by_id["000001.SZ"]["name"], "资产A")
        self.assertEqual(by_id["164824.SZ"]["name"], "印度基金LOF")
        # 心跳
        heartbeat = json.loads((self.root / "integration" / "b_heartbeat.json").read_text(encoding="utf-8"))
        self.assertEqual(heartbeat["status"], "ok")
        self.assertIn("last_seen", heartbeat)
        # 备份 + manifest
        self.assertTrue((self.root / "integration" / "backup" / "20260806" / ".ready").exists())
        self.assertEqual(self.integration.get_manifest()["newest_run"], "20260806")
        # 元数据头（build_header 顺序：schema_version 开头；generated_date 随当天变化）
        header_line = (run_dir / "assets_metadata.csv").read_text(encoding="utf-8").splitlines()[0]
        self.assertTrue(header_line.startswith("# schema_version="))
        self.assertIn("generated_date=", header_line)
        self.assertIn("data_asof=2026-08-06", header_line)

    def test_pipeline_macro_missing_does_not_block(self) -> None:
        # 无 processed/global_macro：宏观为空但仍 COMPLETED（不虚构宏观状态）。
        record = self._run()
        self.assertEqual(record["status"], "COMPLETED")
        package = json.loads(
            (self.root / "integration" / "systemB_ref" / "20260806" / "decision_ref_package.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(package["macro_regime"], {})

    def test_pipeline_missing_pool_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            run_pipeline(
                target_date="2026-08-06",
                data_root=self.data_root,
                integration=self.integration,
                online_ok=False,
                asset_pool=self.root / "absent.csv",
            )

    def test_pipeline_writes_heartbeat_on_failure(self) -> None:
        # 资产池缺失时也应写心跳（失败证明 B 在线）。
        with self.assertRaises(FileNotFoundError):
            run_pipeline(
                target_date="2026-08-06",
                data_root=self.data_root,
                integration=self.integration,
                online_ok=False,
                asset_pool=self.root / "absent.csv",
            )
        heartbeat = json.loads((self.root / "integration" / "b_heartbeat.json").read_text(encoding="utf-8"))
        self.assertEqual(heartbeat["status"], "error")

    # ---- 阶段二：宏观持续期 + 逐资产风控旗 ----

    def test_pipeline_duration_phase_merged(self) -> None:
        self._write_macro_series()
        record = self._run()
        self.assertEqual(record["status"], "COMPLETED")
        package = self._read_package()
        regime = package["macro_regime"]
        self.assertEqual(regime["phase"], "late")
        self.assertEqual(regime["phase_basis"], "rate_up")
        self.assertEqual(regime["phase_confidence"], "high")
        self.assertEqual(regime["state_durations"]["rate_up"], 7)
        self.assertEqual(regime["state_durations"]["curve_inverted"], 8)
        self.assertEqual(regime["states"], ["rate_up", "curve_inverted", "real_yield_stable"])

    def test_pipeline_duration_phase_disabled(self) -> None:
        self._write_macro_series()
        run_pipeline(
            target_date="2026-08-06",
            data_root=self.data_root,
            integration=self.integration,
            online_ok=False,
            asset_pool=self.pool,
            risk_params=self.risk_params,
            include_duration_phase=False,
        )
        package = self._read_package()
        self.assertNotIn("phase", package["macro_regime"])
        self.assertIn("states", package["macro_regime"])

    def test_pipeline_red_flag_filled(self) -> None:
        self._write_risky_fund()
        record = self._run()
        self.assertEqual(record["status"], "COMPLETED")
        package = self._read_package()
        by_id = {asset["asset_id"]: asset for asset in package["assets"]}
        self.assertIsNone(by_id["000001.SZ"]["red_flag"], "平稳资产无风险旗")
        risky = by_id["164824.SZ"]["red_flag"]
        self.assertEqual(risky["level"], "red")
        self.assertEqual(risky["triggered_by"], ["drawdown_60d"])
        self.assertTrue(risky["approval_required"] is True)

    def test_pipeline_red_flag_disabled(self) -> None:
        self._write_risky_fund()
        run_pipeline(
            target_date="2026-08-06",
            data_root=self.data_root,
            integration=self.integration,
            online_ok=False,
            asset_pool=self.pool,
            risk_params=self.risk_params,
            include_red_flag=False,
        )
        package = self._read_package()
        for asset in package["assets"]:
            self.assertIsNone(asset["red_flag"])

    def test_pipeline_include_stress_filled(self) -> None:
        # include_stress=True：DGS30 连续 +0.6 → 各资产 macro_stress 含 rate_up_50bp。
        self._write_stress_macro()
        record = run_pipeline(
            target_date="2026-08-06",
            data_root=self.data_root,
            integration=self.integration,
            online_ok=False,
            asset_pool=self.pool,
            risk_params=self.risk_params,
            include_stress=True,
        )
        self.assertEqual(record["status"], "COMPLETED")
        package = self._read_package()
        for asset in package["assets"]:
            stress = asset["macro_stress"]
            self.assertIn("rate_up_50bp", stress)
            self.assertGreaterEqual(stress["rate_up_50bp"]["sample_count"], 5)
            self.assertIsNotNone(stress["rate_up_50bp"]["pnl_pct"])

    def test_pipeline_include_stress_default_empty(self) -> None:
        # include_stress=False（默认）：macro_stress 保持空 dict，零开销。
        self._write_stress_macro()
        record = run_pipeline(
            target_date="2026-08-06",
            data_root=self.data_root,
            integration=self.integration,
            online_ok=False,
            asset_pool=self.pool,
            risk_params=self.risk_params,
        )
        self.assertEqual(record["status"], "COMPLETED")
        package = self._read_package()
        for asset in package["assets"]:
            self.assertEqual(asset["macro_stress"], {})
