"""从条件收益表生成全球 ETF 宏观规则（DRAFT → APPROVED）。

用法：
    python scripts/create_global_macro_rules.py --dry-run      # 默认：只打印候选表，不写库
    python scripts/create_global_macro_rules.py --write        # 将候选 DRAFT 写入研究库
    python scripts/create_global_macro_rules.py --approve      # 将 eligible DRAFT 翻为 APPROVED

规则只经人工确认后 APPROVED；引擎只读取 status='APPROVED' 的规则。
无数据状态（期限结构/利率平稳等）默认为保守中性 1.00 且不落库，避免掩盖缺失证据。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qteasy_research.core.global_etf_engine import suggest_modifier_from_condition_returns
from qteasy_research.pretrade.storage import ResearchStore

# 中文宏观状态 → 引擎宏状态枚举
STATE_MAP: dict[str, str] = {
    "加息/利率上行": "rate_up",
    "降息/利率下行": "rate_down",
    "利率平稳": "rate_stable",
    "期限结构倒挂": "curve_inverted",
    "期限结构正常": "curve_normal",
    "实际利率上行": "real_yield_up",
    "实际利率下行": "real_yield_down",
    "实际利率平稳": "real_yield_stable",
}

# 无条件收益数据的状态（仅在 Notebook 展示为保守中性，默认不落库）
NO_DATA_STATES = ("rate_stable", "curve_normal", "curve_inverted", "real_yield_stable")

ASSETS = ("SPY", "TLT", "GLD")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成全球 ETF 宏观规则（DRAFT/APPROVED）")
    parser.add_argument("--data-dir", type=Path, default=Path("data/processed/global_macro"))
    parser.add_argument("--store-root", type=Path, default=Path("research_store"))
    parser.add_argument("--target-date", default="2026-08-03", help="研究目标日期（固定避免窗口漂移）")
    parser.add_argument("--dry-run", action="store_true", default=True, help="只打印候选表，不写库（默认）")
    parser.add_argument("--write", action="store_true", help="将候选 DRAFT 写入研究库")
    parser.add_argument("--approve", action="store_true", help="将 eligible DRAFT 翻为 APPROVED")
    parser.add_argument("--include-no-data-neutral", action="store_true", help="把无数据状态以 1.00 保守中性写入 DRAFT")
    parser.add_argument("--include-reference-only", action="store_true", help="把样本<24 的行写入 DRAFT（默认跳过）")
    args = parser.parse_args()
    if args.approve and not args.write:
        # --approve 隐含写库动作，单独执行时也允许（只翻既有 DRAFT，不改候选）
        pass
    return args


def load_condition_returns(data_dir: Path) -> pd.DataFrame:
    path = data_dir / "condition_returns.csv"
    frame = pd.read_csv(path)
    required = {"asset", "macro_state", "sample_count", "monthly_return"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"{path.name} 缺少字段：{sorted(missing)}")
    return frame


def load_baselines(data_dir: Path) -> dict[str, dict[str, Any]]:
    """读 baseline_returns.csv；缺失时从价格 CSV 用月末重采样重算。"""
    path = data_dir / "baseline_returns.csv"
    if path.exists():
        frame = pd.read_csv(path)
        return {
            row["asset"]: {
                "baseline_monthly_return": float(row["baseline_monthly_return"]),
                "baseline_sample_count": int(row["baseline_sample_count"]),
            }
            for _, row in frame.iterrows()
        }
    # 回退：从 SPY/TLT/GLD.csv 用 month_end + pct_change 重算
    result: dict[str, dict[str, Any]] = {}
    for asset in ASSETS:
        price_path = data_dir / f"{asset}.csv"
        if not price_path.exists():
            continue
        df = pd.read_csv(price_path)
        df["observation_date"] = pd.to_datetime(df["observation_date"], errors="coerce")
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        monthly = df.set_index("observation_date")["value"].resample("ME").last().dropna().pct_change().dropna()
        result[asset] = {
            "baseline_monthly_return": float(monthly.mean()),
            "baseline_sample_count": int(len(monthly)),
        }
    return result


def build_rule_candidates(
    condition_returns: pd.DataFrame,
    baselines: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """对每个有数据的状态-资产组合应用五档离散规则，输出候选。"""
    candidates: list[dict[str, Any]] = []
    for _, row in condition_returns.iterrows():
        asset = str(row["asset"])
        macro_state_zh = str(row["macro_state"])
        if macro_state_zh not in STATE_MAP:
            continue
        macro_state = STATE_MAP[macro_state_zh]
        sample_count = int(row["sample_count"])
        if sample_count <= 0 or row["monthly_return"] is None or pd.isna(row["monthly_return"]):
            continue
        base = baselines.get(asset, {}).get("baseline_monthly_return")
        if base is None:
            continue
        suggestion = suggest_modifier_from_condition_returns(
            condition_return=float(row["monthly_return"]) * 100,
            baseline_return=float(base) * 100,
            sample_count=sample_count,
        )
        candidates.append({
            "asset_code": asset,
            "macro_state": macro_state,
            "macro_state_zh": macro_state_zh,
            "modifier": suggestion["modifier"],
            "sample_start": row.get("sample_start"),
            "sample_end": row.get("sample_end"),
            "sample_count": sample_count,
            "confidence": suggestion["confidence"],
            "suggest_status": suggestion["status"],
            "monthly_return": float(row["monthly_return"]),
            "baseline_monthly_return": float(base),
        })
    return candidates


def write_draft_rules(
    store: ResearchStore,
    candidates: list[dict[str, Any]],
    *,
    effective_date: str,
    include_no_data: bool,
    include_reference_only: bool,
) -> list[dict[str, Any]]:
    """把符合条件的数据行写入 DRAFT。样本<24 默认跳过；无数据状态默认不落库。"""
    written: list[dict[str, Any]] = []
    skipped: list[str] = []
    for cand in candidates:
        if cand["modifier"] is None:
            if include_reference_only:
                skipped.append(f"{cand['asset_code']}/{cand['macro_state']}: REFERENCE_ONLY 已按开关写入")
            else:
                skipped.append(f"{cand['asset_code']}/{cand['macro_state']}: 样本<24 仅参考，跳过")
                continue
        store.upsert_global_etf_macro_rule({
            "asset_code": cand["asset_code"],
            "macro_state": cand["macro_state"],
            "modifier": cand["modifier"] if cand["modifier"] is not None else 1.00,
            "sample_start": cand["sample_start"],
            "sample_end": cand["sample_end"],
            "sample_count": cand["sample_count"],
            "confidence": cand["confidence"],
            "status": "DRAFT",
            "effective_date": effective_date,
        })
        written.append(cand)
    if include_no_data:
        for asset in ASSETS:
            for state in NO_DATA_STATES:
                store.upsert_global_etf_macro_rule({
                    "asset_code": asset,
                    "macro_state": state,
                    "modifier": 1.00,
                    "sample_start": None,
                    "sample_end": None,
                    "sample_count": 0,
                    "confidence": "insufficient_data",
                    "status": "DRAFT",
                    "effective_date": effective_date,
                })
                written.append({
                    "asset_code": asset, "macro_state": state, "modifier": 1.00,
                    "sample_count": 0, "confidence": "insufficient_data",
                })
    for message in skipped:
        print("跳过：", message)
    return written


def approve_eligible_rules(store: ResearchStore) -> list[dict[str, Any]]:
    """把 eligible DRAFT 翻为 APPROVED：样本>=60 且非中性且非无数据。"""
    approved: list[dict[str, Any]] = []
    rejected: list[str] = []
    for rule in store.list_global_etf_macro_rules(status="DRAFT"):
        sample_count = rule.get("sample_count") or 0
        modifier = float(rule.get("modifier") or 1.0)
        if sample_count < 60:
            rejected.append(f"{rule['asset_code']}/{rule['macro_state']}: 样本 {sample_count}<60，保持 DRAFT")
            continue
        if modifier == 1.00:
            rejected.append(f"{rule['asset_code']}/{rule['macro_state']}: 中性 modifier，保持 DRAFT")
            continue
        if rule["macro_state"] in NO_DATA_STATES:
            rejected.append(f"{rule['asset_code']}/{rule['macro_state']}: 无数据保守中性，拒绝 APPROVED")
            continue
        rule["status"] = "APPROVED"
        rule["approved_by"] = "manual"
        store.upsert_global_etf_macro_rule(rule)
        approved.append(rule)
    for message in rejected:
        print("拒绝 APPROVED：", message)
    return approved


def print_candidates(candidates: list[dict[str, Any]]) -> None:
    frame = pd.DataFrame(candidates)
    if frame.empty:
        print("没有候选规则。")
        return
    cols = [
        "asset_code", "macro_state", "sample_count", "monthly_return",
        "baseline_monthly_return", "modifier", "confidence", "suggest_status",
        "sample_start", "sample_end",
    ]
    frame["monthly_return"] = frame["monthly_return"].round(4)
    frame["baseline_monthly_return"] = frame["baseline_monthly_return"].round(4)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)
    print(frame[cols].to_string(index=False))


def main() -> int:
    args = parse_args()
    data_dir = args.data_dir.resolve()
    store = ResearchStore(args.store_root)

    condition_returns = load_condition_returns(data_dir)
    baselines = load_baselines(data_dir)
    candidates = build_rule_candidates(condition_returns, baselines)

    if args.dry_run and not args.write and not args.approve:
        print("=== 候选规则（dry-run，不写库）===")
        print_candidates(candidates)
        eligible = [c for c in candidates if c["suggest_status"] == "APPROVED"]
        print(f"\n可 APPROVED 候选：{len(eligible)} 条")
        for cand in eligible:
            print(f"  {cand['asset_code']}/{cand['macro_state']}: modifier={cand['modifier']} 样本={cand['sample_count']}")
        return 0

    if args.write:
        written = write_draft_rules(
            store, candidates,
            effective_date=args.target_date,
            include_no_data=args.include_no_data_neutral,
            include_reference_only=args.include_reference_only,
        )
        print(f"已写入 DRAFT 规则：{len(written)} 条（effective_date={args.target_date}）")
        if args.approve:
            approved = approve_eligible_rules(store)
            print(f"已 APPROVED：{len(approved)} 条")
            for rule in approved:
                print(f"  {rule['asset_code']}/{rule['macro_state']}: modifier={rule['modifier']}")
        return 0

    if args.approve:
        approved = approve_eligible_rules(store)
        print(f"已 APPROVED：{len(approved)} 条")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
