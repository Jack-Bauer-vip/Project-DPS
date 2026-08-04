# qteasy_lab 项目交接入口

本文件是后续 AI 接手本项目的唯一入口。项目目标是在保留现有 qteasy 回测系统的基础上，建设个人投资者可用的 ETF/股票投前研究、因子评分和全球 ETF 宏观匹配工具。

## 当前环境

- 项目目录：`D:\Project DPS\research_engines\qteasy_lab`
- Git 分支：`develop`
- 最近提交：
  - `975b8f3 chore: 建立 ETF 研究系统版本基线`
  - `b6f80f1 chore: init project structure`
- 当前工作区：存在未提交的研究系统升级代码和测试；接手时不得执行 `git reset`、`git clean` 或覆盖这些修改。
- Python：3.11+
- 操作系统：Windows
- 全量离线测试：60 项通过。
- GlobalEtfEngine 测试：5 项，已包含在 60 项中，全部使用离线 fixture，尚未连接真实 FRED 数据。

接手前重新确认 Git 状态：

```powershell
cd D:\Project DPS
git status --short
git branch --show-current
git log --oneline -5
git ls-files research_engines/qteasy_lab/research_store
git ls-files research_engines/qteasy_lab/data/raw
git ls-files research_engines/qteasy_lab/data/processed
```

当前预期是分支为 `develop`，存在未提交的研究系统修改，运行时数据没有被 Git 跟踪。

## 推荐阅读顺序

```text
AI_HANDOFF.md
→ docs/PROJECT_STATUS.md
→ docs/ARCHITECTURE.md
→ docs/DECISION_LOG.md
→ docs/DATA_SOURCES.md
→ docs/TESTING.md
→ docs/NEXT_STEPS.md
→ 相关源码和测试
```

## 当前项目状态

已完成：

- 单资产 ETF/股票投前研究、研究项目和研究版本管理；
- 资产档案、动态数据、因子分析和历史报告；
- Markdown、HTML、PDF、ZIP 报告导出；
- PySide6 桌面端研究项目、因子研究和数据管理页面；
- AKShare → 第三方 Tushare → 本地快照的数据回退链路；
- SQLite 因子配置和 Parquet 因子值；
- A 股因子评分器及诊断信息；
- `momentum_60d`、`momentum_120d`、低波动和流动性因子骨架；
- GlobalEtfEngine 骨架；
- SPY、TLT、GLD 的 Yahoo Finance 数据抓取；
- 全球 ETF 专用 SQLite 表；
- 60 项离线测试。

当前未完成：

- FRED DGS10、DGS2、DGS30、DFII10 数据恢复；
- 全球宏观条件收益人工审阅；
- 宏观规则 `APPROVED` 记录；
- GlobalEtfEngine 桌面端页面；
- 全球研究资产与交易资产映射；
- FRED 本地 CSV 自动导入；
- 全球 ETF 真实数据烟囱测试。

当前正确行为：

```text
FRED 利率数据缺失
→ GlobalEtfEngine = PARTIAL
→ macro_modifier = null
→ final_score = null
```

## 重要约束

- 先读取本文件和 `docs/`，再修改源码。
- 不重构现有回测主流程。
- 不把 `GlobalEtfEngine` 与 A 股 `factor_scoring.py` 混合。
- 不把缺失宏观数据当作中性值。
- 不增加 KDJ。
- 不自动生成交易指令。
- 不自动修改资产池、组合权重或回测配置。
- 不覆盖历史研究版本、原始数据或研究快照。
- 不读取、打印或提交 API Token、API Key 和密码。

## 正确的测试命令

必须从项目目录执行：

```powershell
cd D:\Project DPS\research_engines\qteasy_lab
.\.venv\Scripts\python.exe -B -m unittest discover `
  -s "D:\Project DPS\tests" -q
```

如果从 `D:\Project DPS` 上级目录直接执行，可能出现 `No module named qteasy_research`，这属于工作目录错误。

## 下一步

优先恢复或手动导入 FRED 数据，运行 `notebooks/global_macro_lab.ipynb`，人工审阅 SPY/TLT/GLD 条件收益，再创建并审核宏观规则。详细任务见 [docs/NEXT_STEPS.md](docs/NEXT_STEPS.md)。

## 可复制给后续 AI 的继续工作提示词

```text
你正在继续 qteasy_lab 项目。请先阅读 AI_HANDOFF.md 和 docs/ 下的全部交接文档，再检查当前源码和测试。当前 A 股评分器已稳定，GlobalEtfEngine 只有离线 fixture 验证，FRED 利率数据尚未恢复。请从 docs/NEXT_STEPS.md 的 P0 任务开始。不要重构 qteasy_research/backtesting、qteasy_research/strategies 或既有 pretrade 数据流；不要把缺失宏观数据当作中性；没有 APPROVED 宏观规则时不要生成最终分数；不要增加 KDJ、交易指令或资产池自动修改。所有 API 密钥只通过环境变量读取，绝不写入代码、文档、报告或 Git。
```
