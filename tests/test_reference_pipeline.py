"""reference/pipeline：端到端管线回归测试（临时 data_root + 临时集成目录）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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
        )

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
        # 心跳
        heartbeat = json.loads((self.root / "integration" / "b_heartbeat.json").read_text(encoding="utf-8"))
        self.assertEqual(heartbeat["status"], "ok")
        self.assertIn("last_seen", heartbeat)
        # 备份 + manifest
        self.assertTrue((self.root / "integration" / "backup" / "20260806" / ".ready").exists())
        self.assertEqual(self.integration.get_manifest()["newest_run"], "20260806")
        # 元数据头（build_header 顺序：schema_version 开头）
        header_line = (run_dir / "assets_metadata.csv").read_text(encoding="utf-8").splitlines()[0]
        self.assertTrue(header_line.startswith("# schema_version="))
        self.assertIn("generated_date=2026-08-06", header_line)
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
