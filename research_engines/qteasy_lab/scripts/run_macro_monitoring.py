"""宏观监控 CLI：M1 适配月报 / M2 相关性 / M3 极端情景韧性（阶段四监控框架实现）。

触发：A 侧策略详情已就绪（NOTICE_20260809_strategy_details_ready.json），
设计文档 ``docs/macro_monitoring_framework_design.md`` 进入实现阶段。

读 A 侧只读契约（``strategy_contract.json``）+ B 本地行情/宏观数据 → 产出到
``reports/macro_monitoring/``（M-003：只写 B 本地 reports/，不写共享目录、
不写系统A、不读 ``systemA_feedback/``）。

- 机器输出全 ASCII，``strategy_id``/``asset_id`` 标识，零中文策略名。
- 所有展示值为描述性统计/情景模拟，``approval_policy=REFERENCE_ONLY``，无决策阈值。
- 假设统一标注 ``- B-side; awaiting A confirmation``。

示例：
  python scripts/run_macro_monitoring.py
  python scripts/run_macro_monitoring.py --rule three_musketeers
  python scripts/run_macro_monitoring.py --no-online --output-root reports/macro_monitoring
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本可从任意目录运行（scripts/ 的父目录即 qteasy_lab 包根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qteasy_research.reference.backtest_engine import parse_contract
from qteasy_research.reference.config import (
    MACRO_MONITOR_DIR,
    STRATEGY_CONTRACT_PATH,
    SYSTEM_B_DATA_ROOT,
)
from qteasy_research.reference.macro_monitoring import run_macro_monitoring

# M1 服务对象（设计文档 §4.1：three_musketeers 与 global_allocation 各一份）。
DEFAULT_RULES = ("three_musketeers", "global_allocation")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="宏观监控：只读契约 + B 本地数据，只写 reports/macro_monitoring/"
    )
    parser.add_argument("--contract", type=Path, default=STRATEGY_CONTRACT_PATH,
                        help="A 侧策略规则契约（只读）")
    parser.add_argument("--data-root", type=Path, default=SYSTEM_B_DATA_ROOT,
                        help="B 本地数据目录（fund_daily/index_daily/macro）")
    parser.add_argument("--output-root", type=Path, default=MACRO_MONITOR_DIR,
                        help="输出目录（只写 B 本地 reports/macro_monitoring）")
    parser.add_argument(
        "--rule", default=",".join(DEFAULT_RULES),
        help="逗号分隔的 M1 服务策略 rule：three_musketeers/global_allocation/all"
             "（默认两者）",
    )
    parser.add_argument("--no-online", dest="online_ok", action="store_false",
                        default=True,
                        help="行情缺失时不走在线补齐（确定性优先）")
    return parser.parse_args()


def _resolve_rules(selector: str) -> tuple[str, ...]:
    """按 selector 解析 M1 rule：all / 逗号分隔。"""
    if selector == "all":
        return DEFAULT_RULES
    parts = [p.strip() for p in selector.split(",") if p.strip()]
    return tuple(parts) if parts else DEFAULT_RULES


def main(args: argparse.Namespace) -> dict:
    contract = parse_contract(args.contract)  # 契约缺失 → FileNotFoundError
    rules = _resolve_rules(args.rule)
    result = run_macro_monitoring(
        contract,
        args.data_root,
        output_root=args.output_root,
        rules=rules,
        online_ok=args.online_ok,
    )

    asof = result["asof_month"]
    print(f"[macro_monitoring] asof_month={asof} status={result['status']}")
    for item in result["written"]["M1"]:
        print(f"[macro_monitoring] M1 {item['rule']} OK  md={item['md']}")
    if result["written"]["M2"]:
        print(f"[macro_monitoring] M2 OK  matrix={result['written']['M2']['matrix']}")
    if result["written"]["M3"]:
        print(f"[macro_monitoring] M3 OK  md={result['written']['M3']['md']}")
    for error in result["errors"]:
        print(f"[macro_monitoring] ERROR {error}")
    if result["errors"]:
        print(f"[macro_monitoring] done PARTIAL ({len(result['errors'])} error(s)); "
              f"see reports/macro_monitoring/ for successful items")
    else:
        print("[macro_monitoring] done OK; outputs in reports/macro_monitoring/")
    return result


if __name__ == "__main__":
    main(parse_args())
