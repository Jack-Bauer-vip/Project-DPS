"""阶段一端到端管线入口：三件套 → dry-run 写 outputs/ 或写共享目录。

- 默认 ``--dry-run``：调 ``run_pipeline(output_root=outputs/, ...)``，只产出三件套
  （grid_reference_table.csv / macro_hedge_efficiency.parquet /
  decision_ref_package.json + assets_metadata.csv）到本地 outputs/。
- ``--real``：写真实共享目录 ``D:\\FF Project\\data\\integration\\``；进入前显式调
  ``IntegrationDir.ensure_root()`` 兜底创建根目录（仅根 + WARNING 日志，不因 A 未建目录
  而崩溃），对齐评审决议 4.4-4（``require_exists`` 严格模式仅用于需人工确认的场景）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本可从任意目录运行（scripts/ 的父目录即 qteasy_lab 包根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qteasy_research.reference.config import (
    OUTPUTS_DIR,
    SYSTEM_A_ASSET_POOL,
    SYSTEM_B_DATA_ROOT,
)
from qteasy_research.reference.metadata import today_iso
from qteasy_research.reference.pipeline import run_pipeline
from qteasy_research.reference.shared_dir import IntegrationDir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="阶段一端到端参考维度管线（B1-1/B1-2 + 决策包）")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                        help="dry-run 写本地 outputs/（默认，不碰共享目录）")
    parser.add_argument("--real", dest="dry_run", action="store_false",
                        help="真实写入共享目录（A 建好目录后启用）")
    parser.add_argument("--target-date", default=today_iso(), help="数据截止日（data_asof），默认今天")
    parser.add_argument("--data-root", type=Path, default=SYSTEM_B_DATA_ROOT, help="B 本地数据目录")
    parser.add_argument("--asset-pool", type=Path, default=SYSTEM_A_ASSET_POOL, help="系统A asset_pool.csv")
    parser.add_argument("--output-root", type=Path, default=OUTPUTS_DIR,
                        help="dry-run 输出目录（默认 outputs/）")
    parser.add_argument("--include-stress", dest="include_stress", action="store_true",
                        help="计算宏观压力情景损益并填充 macro_stress（默认不计算）")
    parser.add_argument("--no-grid-suggestion", dest="include_grid_suggestion", action="store_false",
                        default=True, help="跳过网格建议引擎（P1-B，默认计算）")
    parser.add_argument("--include-grid-suggestion", dest="include_grid_suggestion", action="store_true",
                        help="计算网格建议（P1-B，默认）")
    parser.add_argument("--strategy-contract-path", type=Path, default=None,
                        help="策略规则契约路径（默认 config.STRATEGY_CONTRACT_PATH；测试注入用）")
    parser.add_argument("--grid-suggestion-dir", type=Path, default=None,
                        help="网格建议包本地源目录（默认 config.GRID_SUGGESTION_DIR；测试注入用）")
    parser.add_argument("--no-grid-recommendation", dest="include_grid_recommendation", action="store_false",
                        default=True, help="跳过网格推荐组合（B2/B3，默认计算）")
    parser.add_argument("--include-grid-recommendation", dest="include_grid_recommendation", action="store_true",
                        help="计算网格推荐组合（B2/B3，默认）")
    parser.add_argument("--grid-recommendation-dir", type=Path, default=None,
                        help="网格推荐组合包本地源目录（默认 config.GRID_RECOMMENDATION_DIR；测试注入用）")
    return parser.parse_args()


def _notify_publish(record: dict) -> None:
    """发包结果飞书只读推送（不阻断；未配置 URL / 异常静默跳过）。

    所有发包路径（refresh_fund_daily --publish、refresh_and_publish.bat、手动
    run_reference_pipeline --real）最终都经 main() 的 real 分支汇聚到此，一处覆盖全部入口。
    """
    try:
        from qteasy_research.common.feishu_notify import feishu_send
        run_id = record.get("run_id", "?")
        ok = record.get("verify", {}).get("ok") if record.get("verify") else None
        status = record.get("status", "?")
        files = record.get("files", [])
        if status == "COMPLETED" and ok:
            feishu_send("【B】发包成功",
                        f"run={run_id} verify.ok=True（{len(files)} 文件）")
        else:
            feishu_send("【B】发包异常",
                        f"run={run_id} status={status} verify.ok={ok} "
                        f"warnings={len(record.get('warnings', []))}")
    except Exception:
        pass


def main(args: argparse.Namespace) -> dict:
    if args.dry_run:
        record = run_pipeline(
            target_date=args.target_date,
            data_root=args.data_root,
            asset_pool=args.asset_pool,
            output_root=args.output_root,
            include_stress=args.include_stress,
            include_grid_suggestion=args.include_grid_suggestion,
            include_grid_recommendation=args.include_grid_recommendation,
            strategy_contract_path=args.strategy_contract_path,
            grid_suggestion_dir=args.grid_suggestion_dir,
            grid_recommendation_dir=args.grid_recommendation_dir,
        )
        print(f"[dry-run] 产出写入：{record['output_root']}")
        for filename in record["files"]:
            print(f"  - {Path(record['output_root']) / filename}")
        print(f"[dry-run] status={record['status']} warnings={len(record['warnings'])}")
        return record

    integration = IntegrationDir()
    # 非 dry-run 进入前显式兜底创建共享目录根目录（仅根 + WARNING），不崩溃。
    integration.ensure_root()
    record = run_pipeline(
        target_date=args.target_date,
        data_root=args.data_root,
        asset_pool=args.asset_pool,
        integration=integration,
        include_stress=args.include_stress,
        include_grid_suggestion=args.include_grid_suggestion,
        include_grid_recommendation=args.include_grid_recommendation,
        strategy_contract_path=args.strategy_contract_path,
        grid_suggestion_dir=args.grid_suggestion_dir,
        grid_recommendation_dir=args.grid_recommendation_dir,
    )
    print(f"[real] 共享目录写入：{integration.root / 'systemB_ref' / record['run_id']}")
    if record.get("grid_suggestion"):
        published = record["grid_suggestion"]
        print(f"[real] grid_suggestion 发布：{integration.root / 'systemB_ref' / record['run_id'] / 'grid_suggestion'}")
        print(f"[real]   package_kind={published.get('package_kind')} status={published.get('status')}")
    if record.get("grid_recommendation"):
        published = record["grid_recommendation"]
        print(f"[real] grid_recommendation 发布：{integration.root / 'systemB_ref' / record['run_id'] / 'grid_recommendation'}")
        print(f"[real]   package_kind={published.get('package_kind')} cadence={published.get('cadence')} status={published.get('status')}")
    print(f"[real] status={record['status']} verify.ok={record['verify'].get('ok')} "
          f"warnings={len(record['warnings'])}")
    _notify_publish(record)
    return record


if __name__ == "__main__":
    main(parse_args())
