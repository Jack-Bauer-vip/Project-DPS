"""生成共享目录接口契约 README.md（给系统A侧的指令，与 PROJECT_AUDIT 5.4 一致）。

默认输出到 ``outputs/README.md`` 供人工审阅（不碰共享目录）；确认无误后
用 ``--install`` 写入共享目录 ``D:\\FF Project\\data\\integration\\README.md``。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本可从任意目录运行（scripts/ 的父目录即 qteasy_lab 包根）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qteasy_research.reference.config import INTEGRATION_DIR, OUTPUTS_DIR
from qteasy_research.reference.metadata import today_iso


def build_contract_text() -> str:
    """接口契约 Markdown 文本（对齐审核资料 5.4 六条要点）。"""
    return f"""# 系统B → 系统A 集成接口契约（共享目录）

> 生成日期：{today_iso()} ｜ 来源：项目B（qteasy_lab） ｜ 单向下发参考维度，非双向写库。

## 一、目录结构

```
D:\\FF Project\\data\\integration\\
├── manifest.json              # 最新运行索引（newest_run → 版本记录）
├── b_heartbeat.json           # B 在线心跳（last_seen，3 天阈值）
├── systemB_ref\\
│   └── {{YYYYMMDD}}\\         # 一次运行包
│       ├── package.json       # 文件清单 + sha256 + 元数据头
│       ├── decision_ref_package.json    # 决策参考包（维度汇总）
│       ├── assets_metadata.csv          # 资产质量清单
│       ├── grid_reference_table.csv     # 网格参考表（B1-1）
│       └── macro_hedge_efficiency.parquet  # 宏观对冲效率（B1-2）
├── backup\\
│   └── {{YYYYMMDD}}\\         # 最近 N 版备份（回滚用）
└── systemA_feedback\\          # ★ 系统A 独占写
    └── consumed_{{YYYYMMDD}}.json   # 消费回执（仅写入此子目录）
```

## 二、读侧约定

1. 读 `manifest.json` → `newest_run`，定位最新运行目录。
2. 校验 `.ready` 标记存在 + `package.json` 中 `data_asof` 新鲜（见四）+ 逐文件 sha256 一致。
3. CSV 用 `read_csv(..., comment="#")`（首行元数据头以 `#` 开头）；parquet 用
   `pd.read_parquet`（pyarrow key-value 元数据头）；JSON 用 `json.load`。

## 三、消费回执

A 读包成功后写 `systemA_feedback/consumed_{{YYYYMMDD}}.json`：
`{{"package_date": "…", "status": "SUCCESS"|"FAILED", "reason": "…"}}`。
回执**只写入 `systemA_feedback/`，绝不触碰 `systemB_ref/`**；属单向数据流确认回执，
非双向写库。

## 四、新鲜度与批准

- **过期规则**：`data_asof < 今天 − 2 天` → 丢弃（两侧共用 `validate_freshness` 单一实现）。
- **批准**：所有参考维度 `approval_required = true`，**永不自动 APPROVED**，
  须桌面端人工批准后才可进入交易参数。

## 五、版本回滚

- 复制 `backup/{{旧日期}}/` 内容并更新 `manifest.json`；或请 B 重跑生成。

## 六、禁止项

1. A 不得向 `systemB_ref/` 写任何文件（双向写库被红线禁止）。
2. B 不得把 `systemA_feedback/` 当分析依据（避免逻辑循环）。
3. A 不得修改 B 写出的任何数据文件。
4. `systemA_feedback/` 归 A 独占写，B 只读计数。
5. 全部维度供人工参考，机器无任何自动批准路径。
6. 数据新鲜度 / 目录结构变更以本契约为准，两侧同步。

---

> 首次一次性动作：新建 `D:\\FF Project\\data\\integration\\`（空目录即可，B 自动建子目录）。
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成共享目录接口契约 README.md")
    parser.add_argument("--output", type=Path, default=OUTPUTS_DIR / "README.md",
                        help="输出路径（默认 outputs/README.md 供审阅）")
    parser.add_argument("--install", action="store_true",
                        help="写入共享目录（INTEGRATION_DIR/README.md）")
    return parser.parse_args()


def main(args: argparse.Namespace) -> Path:
    target = INTEGRATION_DIR / "README.md" if args.install else args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_contract_text(), encoding="utf-8")
    return target


if __name__ == "__main__":
    arguments = parse_args()
    path = main(arguments)
    print(f"接口契约已生成：{path}")
