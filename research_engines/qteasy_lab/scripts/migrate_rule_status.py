"""迁移规则表到 BASELINE/PROVISIONAL 状态体系（幂等，可重复执行）。

背景：交易资产换算 final_score 为空，是因为 `curve_normal` 等常态状态永远
没有 APPROVED 规则，导致引擎 PARTIAL、评分链阻断。本脚本一次性补齐：

1. 为常态状态（rate_stable/curve_normal/real_yield_stable）补 BASELINE 规则
   （modifier=1.00，confidence=baseline）。若该状态已有任意规则（含 DRAFT/
   APPROVED 等）则跳过，避免覆盖人工内容。
2. 把样本 24~59 的 DRAFT 批量提升为 PROVISIONAL（临时生效，仅供参考），
   待样本积累到 60 后可由人工确认升级为 APPROVED。

curve_inverted（期限结构倒挂）是异常信号，不生成 BASELINE 兜底：数据窗口内
未出现倒挂本身就是信息，若未来出现应提醒人工研究而非用 1.00 掩盖。

用法：
    python scripts/migrate_rule_status.py --dry-run   # 只报告将执行的操作（默认）
    python scripts/migrate_rule_status.py --apply     # 实际执行
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qteasy_research.pretrade.storage import ResearchStore

# 与 create_global_macro_rules.py 保持一致
BASELINE_STATES = ("rate_stable", "curve_normal", "real_yield_stable")
ASSETS = ("SPY", "TLT", "GLD")
MIN_SAMPLE = 24
MAX_SAMPLE = 60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="迁移规则表到 BASELINE/PROVISIONAL 状态体系")
    parser.add_argument("--store-root", type=Path, default=Path("research_store"))
    parser.add_argument("--target-date", default="2026-08-03", help="BASELINE 规则的生效日")
    parser.add_argument("--dry-run", action="store_true", default=True, help="只报告将执行的操作（默认）")
    parser.add_argument("--apply", action="store_true", help="实际执行迁移")
    return parser.parse_args()


def plan_baseline(
    store: ResearchStore, *, target_date: str,
) -> tuple[list[dict[str, str]], list[str]]:
    """计算需要补的 BASELINE 规则；已存在该状态规则的 (asset,state) 跳过。"""
    planned: list[dict[str, str]] = []
    skipped: list[str] = []
    for asset in ASSETS:
        existing = {rule["macro_state"] for rule in store.list_global_etf_macro_rules(asset_code=asset)}
        for state in BASELINE_STATES:
            if state in existing:
                skipped.append(f"{asset}/{state}: 已有规则，跳过")
                continue
            planned.append({"asset_code": asset, "macro_state": state, "effective_date": target_date})
    return planned, skipped


def plan_promote(store: ResearchStore) -> tuple[list[dict[str, Any]], list[str]]:
    """计算样本 24~59 的 DRAFT，需要提升为 PROVISIONAL。"""
    planned: list[dict[str, Any]] = []
    skipped: list[str] = []
    for rule in store.list_global_etf_macro_rules(status="DRAFT"):
        n = rule.get("sample_count") or 0
        if MIN_SAMPLE <= n < MAX_SAMPLE:
            planned.append(rule)
        elif n >= MAX_SAMPLE:
            skipped.append(f"{rule['asset_code']}/{rule['macro_state']}: 样本 {n}>=60，保持 DRAFT 待人工确认")
    return planned, skipped


def apply_baseline(store: ResearchStore, planned: list[dict[str, str]]) -> list[str]:
    created: list[str] = []
    for item in planned:
        store.upsert_global_etf_macro_rule({
            "asset_code": item["asset_code"],
            "macro_state": item["macro_state"],
            "modifier": 1.00,
            "sample_start": None,
            "sample_end": None,
            "sample_count": 0,
            "confidence": "baseline",
            "status": "BASELINE",
            "effective_date": item["effective_date"],
            "reason": f"常态基准：{item['macro_state']} 无研究数据，modifier=1.00 直接生效。",
        })
        created.append(f"{item['asset_code']}/{item['macro_state']}")
    return created


def apply_promote(store: ResearchStore, planned: list[dict[str, Any]]) -> list[str]:
    promoted: list[str] = []
    for rule in planned:
        store.promote_global_etf_macro_rule(
            rule["rule_id"],
            promoted_by="migration",
            note="样本 24~59，迁移批量提升为临时生效供参考",
        )
        promoted.append(f"{rule['asset_code']}/{rule['macro_state']}（样本 {rule['sample_count']}）")
    return promoted


def main() -> int:
    args = parse_args()
    store = ResearchStore(args.store_root)
    baseline_planned, baseline_skipped = plan_baseline(store, target_date=args.target_date)
    promote_planned, promote_skipped = plan_promote(store)

    print(f"=== 迁移计划（{'dry-run 不执行' if not args.apply else '将实际执行'}）===")
    print(f"\n① BASELINE 兜底（常态状态，modifier=1.00）：{len(baseline_planned)} 条待补")
    for item in baseline_planned:
        print(f"  + {item['asset_code']}/{item['macro_state']}")
    for message in baseline_skipped:
        print(f"  · {message}")
    print(f"\n② DRAFT → PROVISIONAL（样本 {MIN_SAMPLE}~{MAX_SAMPLE}）：{len(promote_planned)} 条")
    for rule in promote_planned:
        print(f"  + {rule['asset_code']}/{rule['macro_state']}（样本 {rule['sample_count']}）")
    for message in promote_skipped:
        print(f"  · {message}")

    if not args.apply:
        print("\n执行请加 --apply。")
        return 0

    created = apply_baseline(store, baseline_planned)
    promoted = apply_promote(store, promote_planned)
    print(f"\n已补 BASELINE：{len(created)} 条")
    for name in created:
        print(f"  + {name}")
    print(f"已提升 PROVISIONAL：{len(promoted)} 条")
    for name in promoted:
        print(f"  + {name}")
    print("\n迁移完成。可在桌面端『宏观规则状态』区筛选 PROVISIONAL/BASELINE 查看，")
    print("样本满 60 后通过『确认』升级为 APPROVED。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
