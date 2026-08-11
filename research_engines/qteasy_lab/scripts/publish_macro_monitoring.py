"""发布宏观监控报告到共享目录（systemB_ref/{run_id}/macro_monitoring/）。

职责解耦（保持「生成 reports/（M-003）」与「发布共享目录」分离）：
- 生成：``scripts/run_macro_monitoring.py`` -> ``reports/macro_monitoring/``（只写 B 本地）。
- 发布：本脚本把 ``reports/macro_monitoring/`` 的报告文件**字节级复制**进
  ``systemB_ref/{run_id}/macro_monitoring/``，写 ``macro_monitoring/package.json``
  （契约 v1.3：宏观包 package.json 移入子目录，run 根 package.json 归日度决策包独占）、
  touch run 根 ``.ready``（完成标记）、``backup_run``、``verify_run``，并更新
  manifest 独立顶层字段 ``newest_macro_monitoring_run``（``newest_run`` 保持日度不变，
  不打断 A 侧日度读端）。

默认 dry-run：在本地暂存目录完整演练（复制 -> sha256 -> package.json -> .ready ->
backup -> verify -> manifest），**绝不写共享目录**。``--real`` 才写真实共享目录。

安全边界：
- 只写 ``systemB_ref/{run_id}/macro_monitoring/`` + 根级 ``manifest.json``；
  不读不写 ``systemA_feedback/``。
- ``systemB_ref/{run_id}/NOTICE_macro_monitoring_ready.json`` 保留不动，
  不进 ``package.json files[]``。

示例：
  python scripts/publish_macro_monitoring.py --run-id 20260810
  python scripts/publish_macro_monitoring.py --run-id 20260810 --real
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

# 让脚本可从任意目录运行（scripts/ 的父目录即 qteasy_lab 包根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qteasy_research.reference.config import (  # noqa: E402
    MACRO_MONITOR_DIR,
    MACRO_MONITOR_MAX_AGE_DAYS,
)
from qteasy_research.reference.metadata import (  # noqa: E402
    parse_header_csv,
    today_iso,
    validate_freshness,
)
from qteasy_research.reference.shared_dir import IntegrationDir  # noqa: E402

# 月度宏观监控包的标识（manifest 独立顶层字段判定依据）。
CADENCE = "monthly"
PACKAGE_KIND = "macro_monitoring"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="发布宏观监控报告到共享目录（默认 dry-run，--real 才真实发布）",
    )
    parser.add_argument("--source", type=Path, default=MACRO_MONITOR_DIR,
                        help="本地报告目录（默认 reports/macro_monitoring/）")
    parser.add_argument("--run-id", default="20260810",
                        help="运行日期目录（YYYYMMDD，默认 20260810）")
    parser.add_argument("--subdir", default="macro_monitoring",
                        help="共享目录下子目录名（默认 macro_monitoring）")
    parser.add_argument("--data-asof", default=None,
                        help="数据截至日（默认从 *_correlation_matrix.csv 元数据头解析）")
    parser.add_argument("--max-age-days", type=int, default=MACRO_MONITOR_MAX_AGE_DAYS,
                        help="月度新鲜度阈值（默认 45 天）")
    parser.add_argument("--real", action="store_true",
                        help="真实发布到共享目录（默认 dry-run 本地演练）")
    parser.add_argument("--staging", type=Path, default=None,
                        help="dry-run 暂存根目录（默认系统临时目录）")
    return parser.parse_args(argv)


def _derive_data_asof(source: Path) -> str:
    """从 ``*_correlation_matrix.csv`` 的元数据头解析 ``data_asof``。"""
    matrix_files = sorted(source.glob("*_correlation_matrix.csv"))
    if not matrix_files:
        raise SystemExit(f"[publish_macro] ERROR no *_correlation_matrix.csv in {source}")
    header = parse_header_csv(matrix_files[0])
    data_asof = header.get("data_asof")
    if not data_asof:
        raise SystemExit(f"[publish_macro] ERROR cannot parse data_asof from {matrix_files[0].name}")
    return data_asof


def _publish(
    integration: IntegrationDir,
    run_id: str,
    source: Path,
    subdir: str,
    data_asof: str,
    generated_date: str,
) -> dict[str, Any]:
    """执行发布流程：复制 -> package.json -> .ready -> backup -> verify -> manifest。"""
    record = integration.publish_run(
        run_id, source,
        subdir=subdir,
        data_asof=data_asof,
        generated_date=generated_date,
        cadence=CADENCE,
        package_kind=PACKAGE_KIND,
    )
    backup = integration.backup_run(run_id)
    verify = integration.verify_run(run_id)
    return {"record": record, "backup": str(backup), "verify": verify}


def _dry_run(
    args: argparse.Namespace,
    source: Path,
    subdir: str,
    data_asof: str,
    generated_date: str,
) -> int:
    staging = args.staging
    tmp_ctx = tempfile.TemporaryDirectory(prefix="publish_macro_dryrun_") if staging is None else None
    root = Path(staging) if staging is not None else Path(tmp_ctx.name)  # type: ignore[union-attr]
    try:
        integration = IntegrationDir(root)
        print(f"[publish_macro][dry-run] staging root: {root}")
        out = _publish(integration, args.run_id, source, subdir, data_asof, generated_date)
        run_dir = integration.root / "systemB_ref" / args.run_id
        # 契约 v1.3：宏观包 package.json 在子目录（subdir）内。
        package_path = run_dir / args.subdir / "package.json" if args.subdir else run_dir / "package.json"
        package = json.loads(package_path.read_text(encoding="utf-8"))
        print("\n[publish_macro][dry-run] package.json:")
        print(json.dumps(package, ensure_ascii=False, indent=2))
        print("\n[publish_macro][dry-run] verify:")
        print(json.dumps(out["verify"], ensure_ascii=False, indent=2))
        manifest = integration.get_manifest()
        print(f"[publish_macro][dry-run] backup: {out['backup']}")
        print(f"[publish_macro][dry-run] manifest.newest_run={manifest.get('newest_run')}")
        print(f"[publish_macro][dry-run] manifest.newest_macro_monitoring_run="
              f"{manifest.get('newest_macro_monitoring_run')}")
        print("\n[publish_macro][dry-run] rehearsal done; real shared dir NOT touched.")
        print("[publish_macro][dry-run] rerun with --real to publish for real.")
        return 0
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


def _real(
    args: argparse.Namespace,
    source: Path,
    subdir: str,
    data_asof: str,
    generated_date: str,
) -> int:
    integration = IntegrationDir()
    integration.require_exists()
    before = integration.get_manifest().get("newest_run")
    print(f"[publish_macro][real] shared root: {integration.root}")
    print(f"[publish_macro][real] manifest.newest_run before: {before}")

    out = _publish(integration, args.run_id, source, subdir, data_asof, generated_date)
    manifest = integration.get_manifest()
    after = manifest.get("newest_run")

    # 硬断言：newest_run 必须保持发布前值不变（不打断 A 侧日度读端）。
    if before != after:
        print(f"[publish_macro][real] ASSERT FAIL newest_run changed {before} -> {after}")
        return 3
    if manifest.get("newest_macro_monitoring_run") != args.run_id:
        print(f"[publish_macro][real] ASSERT FAIL newest_macro_monitoring_run != {args.run_id}: "
              f"{manifest.get('newest_macro_monitoring_run')}")
        return 3

    verify = out["verify"]
    print(f"[publish_macro][real] backup: {out['backup']}")
    print(f"\n[publish_macro][real] verify.ok={verify.get('ok')} "
          f"file_count={verify.get('file_count')}")
    if verify.get("mismatches"):
        for mismatch in verify["mismatches"]:
            print(f"  MISMATCH {mismatch}")
    print(f"[publish_macro][real] manifest.newest_run={after} (unchanged)")
    print(f"[publish_macro][real] manifest.newest_macro_monitoring_run="
          f"{manifest.get('newest_macro_monitoring_run')}")

    run_dir = integration.root / "systemB_ref" / args.run_id
    print(f"\n[publish_macro][real] final listing of systemB_ref/{args.run_id}/:")
    for path in sorted(run_dir.rglob("*")):
        if path.is_file():
            print(f"  {path.relative_to(integration.root).as_posix()}")
    if not verify.get("ok"):
        print("\n[publish_macro][real] PUBLISH FAILED VERIFY; see mismatches above.")
        return 4
    print("\n[publish_macro][real] publish OK, verified.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source = Path(args.source).resolve()
    if not source.is_dir():
        print(f"[publish_macro] ERROR source dir missing: {source}")
        return 2
    filenames = sorted(p.name for p in source.iterdir() if p.is_file())
    if not filenames:
        print(f"[publish_macro] ERROR no files in source dir: {source}")
        return 2

    data_asof = args.data_asof or _derive_data_asof(source)
    generated_date = today_iso()

    fresh, reason = validate_freshness(data_asof, max_age_days=args.max_age_days)
    if not fresh:
        print(f"[publish_macro] FRESHNESS CHECK FAILED: {reason}")
        if args.real:
            print("[publish_macro] refusing to publish; rerun after data refresh "
                  "or override max-age-days.")
            return 2
        print("[publish_macro] dry-run continues as rehearsal only.")

    print(f"[publish_macro] run_id={args.run_id} source={source} subdir={args.subdir}")
    print(f"[publish_macro] data_asof={data_asof} generated_date={generated_date} "
          f"cadence={CADENCE} package_kind={PACKAGE_KIND}")
    print(f"[publish_macro] files_to_publish({len(filenames)}):")
    for name in filenames:
        print(f"  {args.subdir}/{name}")

    if not args.real:
        return _dry_run(args, source, args.subdir, data_asof, generated_date)
    return _real(args, source, args.subdir, data_asof, generated_date)


if __name__ == "__main__":
    raise SystemExit(main())
