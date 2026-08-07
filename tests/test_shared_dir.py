"""reference/shared_dir：共享目录写入 / 校验 / 心跳 / 兜底测试（临时根）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from qteasy_research.reference.shared_dir import (
    FEEDBACK_DIR,
    IntegrationDir,
    IntegrationDirMissing,
)


class IntegrationDirTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.integration = IntegrationDir(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_run(self, run_id: str = "20260806", data_asof: str = "2026-08-06") -> dict:
        return self.integration.write_run(
            run_id,
            {
                "decision_ref_package.json": {"assets": [1]},
                "assets_metadata.csv": pd.DataFrame({"asset_id": ["000001.SZ"]}),
            },
            data_asof=data_asof,
            generated_date="2026-08-06",
        )

    def test_write_run_full_chain(self) -> None:
        record = self._write_run()
        run_dir = self.root / "systemB_ref" / "20260806"
        self.assertTrue((run_dir / ".ready").exists())
        self.assertTrue((run_dir / "package.json").exists())
        self.assertTrue((run_dir / "decision_ref_package.json").exists())
        package = json.loads((run_dir / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["source_system"], "systemB")
        self.assertEqual(record["status"], "READY")
        self.assertEqual(set(record["checksums"]), {"decision_ref_package.json", "assets_metadata.csv"})
        self.assertEqual(set(record["files"]), {"decision_ref_package.json", "assets_metadata.csv"})

    def test_verify_and_backup(self) -> None:
        self._write_run()
        report = self.integration.verify_run("20260806")
        self.assertTrue(report["ok"])
        backup = self.integration.backup_run("20260806")
        self.assertTrue(backup.exists())
        self.assertTrue((backup / ".ready").exists())

    def test_verify_missing_run(self) -> None:
        report = self.integration.verify_run("20990101")
        self.assertFalse(report["ok"])
        self.assertIn("reason", report)

    def test_manifest_tracks_newest(self) -> None:
        self._write_run("20260806", "2026-08-06")
        self._write_run("20260807", "2026-08-07")
        manifest = self.integration.get_manifest()
        self.assertEqual(manifest["newest_run"], "20260807")
        self.assertIn("20260806", manifest["runs"])

    def test_heartbeat_freshness(self) -> None:
        self.integration.write_heartbeat(status="ok")
        self.assertTrue(self.integration.heartbeat_fresh())
        payload = {"source_system": "systemB", "last_seen": "2026-08-02T00:00:00+00:00", "status": "ok"}
        (self.root / "b_heartbeat.json").write_text(json.dumps(payload), encoding="utf-8")
        self.assertFalse(self.integration.heartbeat_fresh(ref_date="2026-08-06"))  # 4 天过期
        payload["last_seen"] = "2026-08-03T00:00:00+00:00"
        (self.root / "b_heartbeat.json").write_text(json.dumps(payload), encoding="utf-8")
        self.assertTrue(self.integration.heartbeat_fresh(ref_date="2026-08-06"))  # 恰好 3 天

    def test_ensure_root_creates_root_only(self) -> None:
        missing = self.root / "nested" / "integration"
        integration = IntegrationDir(missing)
        integration.ensure_root()
        self.assertTrue(missing.exists())
        self.assertFalse((missing / FEEDBACK_DIR).exists(), "兜底创建不得建 A 独占子目录")

    def test_require_exists_raises(self) -> None:
        integration = IntegrationDir(self.root / "absent")
        with self.assertRaises(IntegrationDirMissing):
            integration.require_exists()

    # ---- 阶段二：回滚 + 备份修剪 ----

    def _read_package(self, run_id: str) -> dict:
        path = self.root / "systemB_ref" / run_id / "decision_ref_package.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_rollback_restores_content(self) -> None:
        # 真实写 → 备份 → 篡改数值字段（非删 .ready）→ 回滚 → 内容与校验恢复。
        self._write_run("20260806")
        self.integration.backup_run("20260806")
        target = self.root / "systemB_ref" / "20260806" / "decision_ref_package.json"
        package = json.loads(target.read_text(encoding="utf-8"))
        package["tampered"] = 999  # 篡改数值字段
        target.write_text(json.dumps(package), encoding="utf-8")
        self.assertFalse(self.integration.verify_run("20260806")["ok"], "篡改后校验应失败")

        self.integration.rollback_to("20260806")
        restored = self._read_package("20260806")
        self.assertNotIn("tampered", restored, "回滚应还原备份时内容")
        self.assertEqual(restored["assets"], [1])
        self.assertTrue(self.integration.verify_run("20260806")["ok"])

    def test_rollback_missing_backup_raises(self) -> None:
        self._write_run("20260806")  # 仅写运行目录，未备份
        with self.assertRaises(FileNotFoundError):
            self.integration.rollback_to("20260806")

    def test_prune_keeps_newest_30(self) -> None:
        # 32 次 write+backup → 修剪到 30，最老 2 个被删，manifest 同步、newest_run 正确。
        run_ids = [f"202601{d:02d}" for d in range(1, 32)] + ["20260201"]  # 字典序==时间序
        for run_id in run_ids:
            self._write_run(run_id)
            self.integration.backup_run(run_id)
        backups = sorted(p.name for p in (self.root / "backup").iterdir() if p.is_dir())
        self.assertEqual(len(backups), 30)
        self.assertNotIn("20260101", backups, "最老备份应被删除")
        self.assertNotIn("20260102", backups)
        self.assertIn("20260201", backups, "最新备份应保留")
        manifest = self.integration.get_manifest()
        self.assertEqual(len(manifest["runs"]), 30)
        self.assertNotIn("20260101", manifest["runs"])
        self.assertEqual(manifest["newest_run"], "20260201")

    def test_prune_respects_max_keep_param(self) -> None:
        # max_keep=1 参数化：3 个备份修剪后仅保留最新 1 个。
        for i in range(1, 4):
            self._write_run(f"2026010{i}")
            self.integration.backup_run(f"2026010{i}")
        stale = self.integration._prune_backups(max_keep=1)
        backups = sorted(p.name for p in (self.root / "backup").iterdir() if p.is_dir())
        self.assertEqual(stale, ["20260101", "20260102"])
        self.assertEqual(backups, ["20260103"])
        manifest = self.integration.get_manifest()
        self.assertEqual(manifest["newest_run"], "20260103")
        self.assertIn("20260103", manifest["runs"])
        self.assertNotIn("20260101", manifest["runs"])

    def test_list_consumed_reads_only(self) -> None:
        feedback = self.root / FEEDBACK_DIR
        feedback.mkdir()
        (feedback / "consumed_20260806.json").write_text(
            json.dumps({"run_id": "20260806", "by": "systemA", "consumed_at": "2026-08-07"}),
            encoding="utf-8",
        )
        receipts = self.integration.list_consumed()
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["by"], "systemA")
        self.assertEqual(self.integration.list_consumed(), receipts)  # 无副作用（只读）
