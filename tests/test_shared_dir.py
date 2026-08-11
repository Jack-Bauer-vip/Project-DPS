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

    # ---- 阶段四：月度宏观监控发布（newest_macro_monitoring_run 独立） ----

    def _write_source_files(self, source: Path) -> None:
        source.mkdir(parents=True, exist_ok=True)
        (source / "202607_correlation_matrix.csv").write_text(
            "# schema_version=1.0; data_asof=2026-07-31\n"
            "asset_id,159131.SZ\n159131.SZ,1.0\n",
            encoding="utf-8",
        )
        (source / "202607_stress_scenarios.md").write_text(
            "# Stress Scenario Resilience Simulation\n", encoding="utf-8",
        )

    def _publish_macro(self, run_id: str, *, generated_date: str = "2026-08-10") -> dict:
        source = self.root / "macro_reports"
        self._write_source_files(source)
        return self.integration.publish_run(
            run_id, source,
            subdir="macro_monitoring",
            data_asof="2026-07-31",
            generated_date=generated_date,
            cadence="monthly",
            package_kind="macro_monitoring",
        )

    def test_publish_run_monthly_macro_keeps_newest_run(self) -> None:
        """月度宏观包 run 更新独立顶层字段，newest_run 保持日度不变。"""
        self._write_run("20260807", "2026-08-07")
        self._publish_macro("20260810")
        manifest = self.integration.get_manifest()
        self.assertEqual(manifest["newest_run"], "20260807",
                         "newest_run 必须保持日度最新不变，不被月度宏观包顶掉")
        self.assertEqual(manifest["newest_macro_monitoring_run"], "20260810")
        self.assertIn("20260810", manifest["runs"])
        self.assertEqual(manifest["runs"]["20260810"]["cadence"], "monthly")
        self.assertEqual(manifest["runs"]["20260810"]["package_kind"], "macro_monitoring")

    def test_publish_run_byte_copy_verify_backup(self) -> None:
        """字节级复制 + package.json 写入子目录 + verify_run/backup 适配新位置。"""
        record = self._publish_macro("20260810")
        self.assertEqual(record["status"], "READY")
        run_dir = self.root / "systemB_ref" / "20260810"
        target = run_dir / "macro_monitoring" / "202607_correlation_matrix.csv"
        self.assertTrue(target.exists())
        self.assertEqual(
            target.read_bytes(),
            (self.root / "macro_reports" / "202607_correlation_matrix.csv").read_bytes(),
            "发布文件必须字节级一致（sha256 应等于来源文件）",
        )
        # 契约 v1.3：宏观包 package.json 写入子目录；run 根 package.json 不产生。
        macro_package = run_dir / "macro_monitoring" / "package.json"
        self.assertTrue(macro_package.exists(), "宏观包 package.json 应写入 macro_monitoring/ 子目录")
        self.assertFalse((run_dir / "package.json").exists(),
                         "宏观包不得写 run 根 package.json（该位置归日度决策包独占）")
        package = json.loads(macro_package.read_text(encoding="utf-8"))
        self.assertEqual(package["cadence"], "monthly")
        self.assertEqual(package["package_kind"], "macro_monitoring")
        names = [f["name"] for f in package["files"]]
        self.assertIn("macro_monitoring/202607_correlation_matrix.csv", names)
        self.assertIn("macro_monitoring/202607_stress_scenarios.md", names)
        # verify_run 校验子目录 package.json（files[].name 带 / 前缀）→ ok。
        report = self.integration.verify_run("20260810")
        self.assertTrue(report["ok"])
        self.assertEqual(report["file_count"], 2)
        # backup 复制整个 run 目录（含子目录 + 子目录内 package.json + .ready）。
        backup = self.integration.backup_run("20260810")
        self.assertTrue((backup / "macro_monitoring" / "202607_stress_scenarios.md").exists())
        self.assertTrue((backup / "macro_monitoring" / "package.json").exists())
        self.assertTrue((backup / ".ready").exists())
        self.assertFalse((backup / "package.json").exists())

    def test_publish_run_keeps_notice_unlisted(self) -> None:
        """既存 NOTICE 保留不动，不进 package.json files[]。"""
        run_dir = self.root / "systemB_ref" / "20260810"
        run_dir.mkdir(parents=True, exist_ok=True)
        notice = run_dir / "NOTICE_macro_monitoring_ready.json"
        notice.write_text('{"status": "ready"}', encoding="utf-8")
        self._publish_macro("20260810")
        self.assertTrue(notice.exists(), "NOTICE 必须保留")
        package = json.loads(
            (run_dir / "macro_monitoring" / "package.json").read_text(encoding="utf-8")
        )
        names = [f["name"] for f in package["files"]]
        self.assertNotIn("NOTICE_macro_monitoring_ready.json", names,
                         "NOTICE 不得进 package.json files[]")

    def test_publish_run_macro_only_no_root_package(self) -> None:
        """契约 v1.3：宏观月度包 package.json 写入子目录，run 根 package.json 不产生。"""
        self._publish_macro("20260810")
        run_dir = self.root / "systemB_ref" / "20260810"
        self.assertTrue((run_dir / "macro_monitoring" / "package.json").exists())
        self.assertFalse((run_dir / "package.json").exists(),
                         "宏观包不得写 run 根 package.json（该位置归日度决策包独占）")
        self.assertTrue((run_dir / ".ready").exists(), "run 根 .ready 仍作为完成标记保留")

    def test_publish_run_macro_subdir_keeps_daily_root_package(self) -> None:
        """同 run 共存：日度 run 根 package.json 不被宏观子目录包覆盖。"""
        # 日度包先写 run 根 package.json（data_asof=2026-08-10）。
        self._write_run("20260810", "2026-08-10")
        # 宏观包再发布到同一 run，package.json 进子目录。
        self._publish_macro("20260810")
        run_dir = self.root / "systemB_ref" / "20260810"
        # run 根 package.json 仍是日度，未被宏观包覆盖。
        root_pkg = json.loads((run_dir / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(root_pkg["data_asof"], "2026-08-10")
        self.assertNotIn("cadence", root_pkg)
        self.assertNotIn("package_kind", root_pkg)
        # 宏观 package.json 在子目录。
        macro_pkg = json.loads(
            (run_dir / "macro_monitoring" / "package.json").read_text(encoding="utf-8")
        )
        self.assertEqual(macro_pkg["cadence"], "monthly")
        self.assertEqual(macro_pkg["package_kind"], "macro_monitoring")
        # verify_run 同时校验 run 根（日度 2 文件）与子目录（宏观 2 文件）。
        report = self.integration.verify_run("20260810")
        self.assertTrue(report["ok"])
        self.assertEqual(report["file_count"], 4)

    def test_prune_recomputes_newest_macro(self) -> None:
        """备份修剪删除最新宏观包时，newest_macro_monitoring_run 回退到剩余最大值。"""
        self._publish_macro("20260801", generated_date="2026-08-01")
        self._publish_macro("20260810")
        self.integration.backup_run("20260801")
        self.integration.backup_run("20260810")
        self.integration._prune_manifest(["20260810"])
        manifest = self.integration.get_manifest()
        self.assertEqual(manifest["newest_macro_monitoring_run"], "20260801")
        self.assertNotIn("20260810", manifest["runs"])
