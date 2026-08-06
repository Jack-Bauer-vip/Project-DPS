"""共享目录（``D:\\FF Project\\data\\integration\\``）的写入 / 校验 / 备份 / 心跳。

单向数据流纪律：
- 项目B只写 ``systemB_ref/``、``backup/``、根级 ``manifest.json``、根级 ``b_heartbeat.json``。
- ``systemA_feedback/`` 归系统A读写；``list_consumed`` 仅**只读计数**，不作分析依据。
- 根目录缺失时 ``ensure_root`` 兜底创建根目录（**仅根，不建 systemA_feedback/**），
  并写入 WARNING 日志提示系统A确认反馈子目录，管线不崩溃（评审决议）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any

import pandas as pd

from qteasy_research.reference.config import HEARTBEAT_MAX_AGE_DAYS, PIPELINE_VERSION, SCHEMA_VERSION
from qteasy_research.reference.metadata import (
    build_header,
    embed_header_any,
    now_iso,
)

logger = logging.getLogger("qteasy_research.reference")

# 系统A独占写、B 只读的子目录名。
FEEDBACK_DIR = "systemA_feedback"
# B 独占写的运行目录与备份目录名。
REF_DIR = "systemB_ref"
BACKUP_DIR = "backup"


class IntegrationDirMissing(RuntimeError):
    """共享目录根目录缺失且无法兜底创建。"""


class IntegrationDir:
    """共享目录操作对象。``root`` 为集成目录根（默认系统A路径）。"""

    def __init__(self, root: str | Path | None = None) -> None:
        from qteasy_research.reference.config import INTEGRATION_DIR

        self.root = Path(root) if root is not None else INTEGRATION_DIR

    # ---- 目录可用性 ----

    def exists(self) -> bool:
        return self.root.exists() and self.root.is_dir()

    def require_exists(self) -> None:
        """严格模式：根目录缺失时抛错并给出指引（用于需要人工确认的场景）。"""
        if not self.exists():
            raise IntegrationDirMissing(
                f"共享目录不存在：{self.root}\n"
                "请系统A新建该根目录（空目录即可）；项目B会自动创建子目录。"
            )

    def ensure_root(self) -> None:
        """兜底：根目录缺失时主动创建根目录（仅根），并 WARNING 提示。

        绝不创建 ``systemA_feedback/``（该子目录归系统A独占写，由系统A确认）。
        """
        if self.exists():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        logger.warning(
            "共享目录根目录不存在，已由项目B自动创建：%s。"
            "请系统A确认 systemA_feedback 子目录是否存在（项目B不创建该子目录）。",
            self.root,
        )

    # ---- 运行包写入 ----

    def write_run(
        self,
        run_id: str,
        payloads: dict[str, Any],
        *,
        data_asof: str,
        generated_date: str,
        schema_version: str = SCHEMA_VERSION,
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        """写入一次运行包。

        ``payloads``: {文件名 → DataFrame / dict}，按后缀写 CSV / parquet / JSON。
        流程：写数据文件 → 逐文件 sha256 → package.json → touch ``.ready`` → 更新 manifest。
        """
        self.ensure_root()
        run_dir = self.root / REF_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        header = build_header(
            generated_date=generated_date, data_asof=data_asof, schema_version=schema_version
        )
        file_records: list[dict[str, Any]] = []
        checksums: dict[str, str] = {}
        for filename, value in payloads.items():
            target = run_dir / filename
            embed_header_any(target, header, value)
            checksum = _sha256(target)
            checksums[filename] = checksum
            file_records.append({
                "name": filename,
                "format": _format_of(target.suffix),
                "sha256": checksum,
            })

        package = {
            "schema_version": schema_version,
            "source_system": "systemB",
            "generated_date": generated_date,
            "data_asof": data_asof,
            "pipeline_version": PIPELINE_VERSION,
            "files": file_records,
            "warnings": warnings or [],
        }
        (run_dir / "package.json").write_text(
            json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (run_dir / ".ready").touch()
        record = {
            "generated_date": generated_date,
            "data_asof": data_asof,
            "status": "READY",
            "checksums": checksums,
            "files": [record["name"] for record in file_records],
            "backup_path": f"{BACKUP_DIR}/{run_id}",
            "consumed": {"by": "systemA", "at": None},
        }
        self._update_manifest(run_id, record)
        return record

    # ---- 校验 / 备份 ----

    def verify_run(self, run_id: str) -> dict[str, Any]:
        """重算运行目录内文件 sha256 与 package.json 比对，返回校验报告。"""
        run_dir = self.root / REF_DIR / run_id
        if not run_dir.exists() or not (run_dir / ".ready").exists():
            return {"run_id": run_id, "ok": False, "reason": "运行目录或 .ready 缺失"}
        package_path = run_dir / "package.json"
        if not package_path.exists():
            return {"run_id": run_id, "ok": False, "reason": "package.json 缺失"}
        package = json.loads(package_path.read_text(encoding="utf-8"))
        mismatches: list[str] = []
        for record in package.get("files", []):
            target = run_dir / record["name"]
            if not target.exists():
                mismatches.append(f"{record['name']}: 文件缺失")
                continue
            if _sha256(target) != record["sha256"]:
                mismatches.append(f"{record['name']}: 校验和不一致")
        return {
            "run_id": run_id,
            "ok": not mismatches,
            "mismatches": mismatches,
            "file_count": len(package.get("files", [])),
        }

    def backup_run(self, run_id: str) -> Path:
        """把运行目录复制到 ``backup/{run_id}``（manifest 保留最近 N 版）。"""
        run_dir = self.root / REF_DIR / run_id
        backup_dir = self.root / BACKUP_DIR / run_id
        if not run_dir.exists():
            raise FileNotFoundError(f"运行目录不存在：{run_dir}")
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        shutil.copytree(run_dir, backup_dir)
        return backup_dir

    # ---- 心跳 ----

    def write_heartbeat(self, status: str = "ok") -> dict[str, Any]:
        """写心跳文件，更新 ``last_seen``（失败路径也会调用，证明B在线）。"""
        self.ensure_root()
        path = self.root / "b_heartbeat.json"
        payload = {
            "source_system": "systemB",
            "last_seen": now_iso(),
            "pipeline_version": PIPELINE_VERSION,
            "status": status,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def heartbeat_fresh(self, *, ref_date: str | None = None) -> bool:
        """心跳是否新鲜（last_seen 未超过 3 天阈值，与系统A check_b_heartbeat 对齐）。"""
        path = self.root / "b_heartbeat.json"
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            last_seen = pd.Timestamp(payload.get("last_seen", "")).tz_localize(None)
        except Exception:
            return False
        ref = pd.Timestamp(ref_date or pd.Timestamp.now()).tz_localize(None)
        return (ref - last_seen).days <= HEARTBEAT_MAX_AGE_DAYS

    # ---- 消费回执（只读，仅计数） ----

    def list_consumed(self) -> list[dict[str, Any]]:
        """读取 ``systemA_feedback/consumed_*.json``。

        **只读计数，不作分析依据**（评审纪律：避免把 A 反馈文件当数据源导致逻辑循环）。
        """
        feedback_dir = self.root / FEEDBACK_DIR
        if not feedback_dir.exists():
            return []
        receipts: list[dict[str, Any]] = []
        for path in sorted(feedback_dir.glob("consumed_*.json")):
            try:
                receipts.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:
                receipts.append({"file": path.name, "parse_error": str(exc)})
        return receipts

    # ---- manifest ----

    def _load_manifest(self) -> dict[str, Any]:
        path = self.root / "manifest.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("manifest.json 解析失败，已重置。")
        return {"schema_version": SCHEMA_VERSION, "newest_run": None, "runs": {}}

    def _update_manifest(self, run_id: str, record: dict[str, Any]) -> None:
        manifest = self._load_manifest()
        runs = dict(manifest.get("runs", {}))
        runs[run_id] = record
        manifest["runs"] = runs
        if manifest.get("newest_run") is None or run_id > str(manifest["newest_run"]):
            manifest["newest_run"] = run_id
        (self.root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get_manifest(self) -> dict[str, Any]:
        return self._load_manifest()

    # ---- 回滚（阶段二骨架） ----

    def rollback_to(self, run_id: str) -> Path:
        """从 ``backup/{run_id}`` 恢复到 ``systemB_ref/{run_id}``（阶段二启用）。"""
        backup_dir = self.root / BACKUP_DIR / run_id
        run_dir = self.root / REF_DIR / run_id
        if not backup_dir.exists():
            raise FileNotFoundError(f"备份目录不存在：{backup_dir}")
        if run_dir.exists():
            shutil.rmtree(run_dir)
        shutil.copytree(backup_dir, run_dir)
        return run_dir


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _format_of(suffix: str) -> str:
    return {"csv": "csv", "parquet": "parquet", "json": "json"}.get(suffix.lstrip("."), "unknown")
