"""B1-3 CLI：交易指纹静态分析，输出 Markdown 到系统B reports/trader_fingerprint/。

只读 A 的 config/ 台账与干预表；不写共享目录、不写 A 任何文件。
报告全 ASCII（只用 strategy_id，中文策略名零出现）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本可从任意目录运行（scripts/ 的父目录即 qteasy_lab 包根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qteasy_research.reference.config import (
    SYSTEM_A_HUMAN_OVERRIDE_LOG,
    SYSTEM_A_MANUAL_OVERRIDE,
    SYSTEM_A_TRADE_LEDGER,
    TRADER_FINGERPRINT_DIR,
)
from qteasy_research.reference.metadata import today_iso
from qteasy_research.reference.trader_fingerprint import (
    analyze_trader_fingerprint,
    render_fingerprint_markdown,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="交易指纹静态分析（B1-3）")
    parser.add_argument("--ledger", type=Path, default=SYSTEM_A_TRADE_LEDGER,
                        help="系统A actual_trade_ledger.csv 路径")
    parser.add_argument("--override", type=Path, default=SYSTEM_A_MANUAL_OVERRIDE,
                        help="系统A manual_override.csv 路径")
    parser.add_argument("--human-log", type=Path, default=SYSTEM_A_HUMAN_OVERRIDE_LOG,
                        help="系统A human_override_log.csv（缺失则跳过）")
    parser.add_argument("--output-dir", type=Path, default=TRADER_FINGERPRINT_DIR,
                        help="输出目录（默认 reports/trader_fingerprint）")
    return parser.parse_args()


def main(args: argparse.Namespace) -> Path:
    human_log = args.human_log if args.human_log and args.human_log.exists() else None
    fingerprint = analyze_trader_fingerprint(
        args.ledger, args.override, human_log_path=human_log
    )
    report = render_fingerprint_markdown(fingerprint)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"{today_iso()}_fingerprint.md"
    output.write_text(report, encoding="utf-8")
    return output


if __name__ == "__main__":
    arguments = parse_args()
    path = main(arguments)
    print(f"交易指纹已写入：{path}")
