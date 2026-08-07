"""生成 tests/fixtures/human_override_log_synthetic.csv（合成人工干预日志，72 行）。

与 tests/test_human_machine_compare.py 的信号假设联动（测试据此构造合成决策包）：
- 512890.SH → bullish（returns20=+0.03）
- 588230.SH → bearish（returns20=-0.02）
- 164824.SZ → bullish（returns20 缺失，beta 有正）
- 512880.SH → neutral（returns20=0，无 beta，无 stress）

策略为全 ASCII 合成 id（strat_alpha/beta/gamma），覆盖 agree/diverge/neutral 三分类、
调增/调减、跨 4 个月。文件带 BOM + CRLF，模拟 A 侧真实 human_override_log.csv 格式。
"""

from __future__ import annotations

import csv
from pathlib import Path

OUT = Path(__file__).resolve().parent / "human_override_log_synthetic.csv"

MONTHS = ["2026-05", "2026-06", "2026-07", "2026-08"]
REASONS = ["confirmation_center 网页人工修改", "风险控制手动调仓", "宏观事件应对"]

# (strategy_id, asset_id, old_weight, new_weight) —— 六模式覆盖 agree/diverge/neutral
PATTERNS = [
    ("strat_alpha", "512890.SH", 0.55, 0.56),  # agree  调增 + 看多
    ("strat_alpha", "588230.SH", 0.45, 0.44),  # agree  调减 + 看空
    ("strat_beta", "512890.SH", 0.60, 0.55),   # diverge 调减 + 看多
    ("strat_beta", "588230.SH", 0.40, 0.45),   # diverge 调增 + 看空
    ("strat_gamma", "164824.SZ", 0.30, 0.35),  # agree
    ("strat_gamma", "512880.SH", 0.50, 0.52),  # neutral 信号中性
]


def build_rows() -> list[list[str]]:
    rows: list[list[str]] = []
    counter = 0
    for month in MONTHS:
        for day in (6, 13, 20):
            for idx, (sid, aid, old, new) in enumerate(PATTERNS):
                hour = 9 + (idx % 8)
                minute = (idx * 13) % 60
                ts = f"{month}-{day:02d}T{hour:02d}:{minute:02d}:{counter % 60:02d}"
                counter += 1
                reason = REASONS[counter % len(REASONS)]
                rows.append([ts, sid, aid, f"{old:.2f}", f"{new:.2f}", reason])
    return rows


def main() -> None:
    with open(OUT, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, lineterminator="\r\n")
        writer.writerow(["time", "strategy_id", "asset_id", "old_weight", "new_weight", "reason"])
        writer.writerows(build_rows())
    print(f"已生成 {OUT}（{72} 行）")


if __name__ == "__main__":
    main()
