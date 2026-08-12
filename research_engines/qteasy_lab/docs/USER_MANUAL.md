---
version: 1.2
updated_at: 2026-08-12
status: current
---

# 项目B · 新用户使用说明书（qteasy_lab 投前研究工作台）

> 这份说明书写给**人**，不写给 AI。核心回答一个问题：**你想做一件研究/回测的事，具体怎么跑、会看到什么结果**。
>
> 每节统一格式：**📌 目的** → **✅ 步骤** → **💡 示例**（完整走一遍给你看）。
>
> 新用户建议顺序：**第 1 章 30 秒 → 第 2 章先记住工作目录纪律 → 第 3 章跑通第一次 → 第 4 章看完整流程**。

---

## 1. 这是什么系统（30 秒看完）

一句话：**它是你的"投前研究"助手**——在你要买入之前，帮你研究单只 ETF/股票、做因子评分、匹配全球宏观环境、回测策略，然后产出研究报告。

```
你要研究的标的 ──▶ 项目B 研究引擎 ──▶ 研究报告 / 因子评分 / 回测绩效
                      （研究端）
```

**和项目A 的关系**（一句话）：**项目B 是"研究端"，项目A 是"决策端"。** B 把研究成果（决策参考包、宏观监控包）写入共享目录，A 只读查看。**B 永不自动批准任何东西**（`REFERENCE_ONLY`），所有宏观规则必须人工确认后才生效。

> 记住一句：**B 只喂"参考"，A 才做"决策"；B 不碰资金，不自动交易。**

---

## 2. 第一个纪律：工作目录（先记住，能少踩很多坑）

**一切 Python 命令必须从 `D:\Project DPS\research_engines\qteasy_lab` 目录执行。**

```powershell
cd "D:\Project DPS\research_engines\qteasy_lab"
```

**💡 如果没这么做会看到什么**：报错 `ModuleNotFoundError: No module named 'qteasy_research'`。

**这不是系统坏了，是你目录错了。** 本项目所有命令都是相对项目根目录的，从上级目录跑必然找不到模块。本说明书所有命令都假设你已经 `cd` 到这里。

---

## 3. 快速上手：从零到跑出第一份报告（约 15 分钟）

> **📌 目的**：让你第一次就成功。全程离线（用本地数据），安全不改任何东西。

### 3.1 准备环境（只需一次）

**✅ 步骤**：

```powershell
cd "D:\Project DPS\research_engines\qteasy_lab"
.venv\Scripts\python.exe -m qteasy_research.desktop   # 桌面版（推荐新用户）
```

或命令行版（不依赖图形界面）：

```powershell
.venv\Scripts\python.exe -m qteasy_research.pretrade --help
```

> **📌 统一用 `.venv\Scripts\python.exe`**，别用系统 python，保证依赖一致。

**需要联网数据源时配置环境变量**（一次性）：

```powershell
$env:TUSHARE_TOKEN = "你的Token"
$env:FRED_API_KEY = "你的Key"
```

> **安全铁律**：Token/Key **只从环境变量读**，绝不写进代码、文档、报告或 Git。

### 3.2 跑第一份投前研究（命令行，推荐先跑通这个）

**✅ 步骤**：

```powershell
cd "D:\Project DPS\research_engines\qteasy_lab"
.venv\Scripts\python.exe -B -m qteasy_research.pretrade research 518880.SH --offline --provider none
```

- `518880.SH`：你想研究的标的代码（黄金 ETF）；
- `--offline`：只用本地数据，不联网，快且可复现；
- `--provider none`：不调用 AI，纯定量研究。

**💡 会看到什么**：控制台打印研究报告全文（技术指标、因子分析、宏观适配、结论），末尾提示文件路径：

```
报告文件：research_store/runs/{run_id}/report.md
结果文件：research_store/runs/{run_id}/result.json
图表目录：research_store/runs/{run_id}/charts/
```

**📌 记下 `run_id`**——后面的"追问/导出"都要用它。

### 3.3 导出报告成文件

**✅ 步骤**（把 `{run_id}` 换成上面看到的真实值）：

```powershell
.venv\Scripts\python.exe -B -m qteasy_research.pretrade export {run_id} --format md --format html --format pdf
```

**💡 会看到什么**：在 `research_store/runs/{run_id}/` 下生成 `report.md`、`report.html`、`report.pdf`，命名类似 `518880.SH_v1_{run_id}/report.md`。

> 想一次打包全部（报告+图表+说明）？用 `--bundle` 生成 zip。

**到这里，你已经能跑通研究链路了。** 接下来是三个核心完整流程，见第 4 章。

---

## 4. 核心流程

### 4.1 完整流程一：给一个标的做完整投前研究（项目化）

> **📌 目的**：正式场景下你不是跑一次就算了，而是建一个"研究项目"、反复研究、留下版本历史。这条流程把 CLI 的命令串成一条完整链路。

**✅ 步骤**：

**第 1 步：创建研究项目**（把研究和散跑分开管理）

```powershell
.venv\Scripts\python.exe -B -m qteasy_research.pretrade project create "黄金ETF研究" 518880.SH --objective "评估黄金中短期配置价值" --project-type ASSET_PROFILE
```

**第 2 步：执行研究**（输出里记下 `run_id`）

```powershell
.venv\Scripts\python.exe -B -m qteasy_research.pretrade research 518880.SH --offline --provider none
```

**第 3 步（可选）：追问一个问题**（比如看下行风险）

```powershell
.venv\Scripts\python.exe -B -m qteasy_research.pretrade followup {run_id} "当前黄金ETF的主要下行风险是什么" --provider deepseek
```

**第 4 步：导出报告**

```powershell
.venv\Scripts\python.exe -B -m qteasy_research.pretrade export {run_id} --format md --format html --format pdf --output-dir "D:/我的报告"
```

**💡 完整示例（可整体复制）**：

```powershell
cd "D:\Project DPS\research_engines\qteasy_lab"

# 第1步 建项目
.venv\Scripts\python.exe -B -m qteasy_research.pretrade project create "黄金ETF研究" 518880.SH --objective "评估配置价值" --project-type ASSET_PROFILE

# 第2步 研究（记下 run_id）
.venv\Scripts\python.exe -B -m qteasy_research.pretrade research 518880.SH --offline --provider none

# 第3步 导出（把 <run_id> 换成上面看到的）
.venv\Scripts\python.exe -B -m qteasy_research.pretrade export <run_id> --format md --format html --format pdf --output-dir "D:/Project DPS/research_engines/qteasy_lab/reports"
```

**💡 会看到什么（预期产出）**：

```
reports/518880.SH_v1_<run_id>/report.md
reports/518880.SH_v1_<run_id>/report.html
reports/518880.SH_v1_<run_id>/report.pdf
reports/518880.SH_v1_<run_id>/manifest.json
reports/518880.SH_v1_<run_id>/charts/*.png
```

> 同一个项目反复研究 → 每次是一个"版本"。想看之前版本选对应 `run_id` 即可。桌面端做同样的事，路径见第 5 章 5.3。

### 4.2 完整流程二：回测一个策略（A 契约 → B 回测 → 看绩效）

> **📌 目的**：项目A 把策略规则写成"策略契约"（`strategy_contract.json`），B 读这份契约做**历史回测**，回答"这个策略历史上表现如何"。这是 B 侧最核心的跨项目能力。
>
> 前提：A 侧已导出契约（A 每次确认策略配置变更时**自动**导出，也可手动 `python -m src.main export_strategy_contract`）。

**✅ 步骤**：

**第 1 步：确认契约在**（B 只读 A 的契约，路径固定）

```
D:\FF Project\data\integration\strategy_contracts\strategy_contract.json
```

**第 2 步：跑回测**（必须在 qteasy_lab 目录）

```powershell
.venv\Scripts\python.exe scripts/run_backtest.py --strategy-id all --no-online
```

- `--strategy-id all`：回测契约里全部策略（也可 `barbell,grid_lh` 选多个，或 `barbell` 只测一个）；
- `--no-online`：行情缺失时**不走在线补齐**，保证结果可复现。

**第 3 步：看结果**（在 `reports/backtest/` 下）

```
reports/backtest/{strategy_id}_nav.csv          # 净值曲线
reports/backtest/{strategy_id}_trades.csv       # 逐笔成交
reports/backtest/{strategy_id}_{YYYYMM}.md      # 绩效摘要（人读）
reports/backtest/{strategy_id}_attribution.csv  # 分标的归因
```

**💡 完整示例（可整体复制）**：

```powershell
cd "D:\Project DPS\research_engines\qteasy_lab"
.venv\Scripts\python.exe scripts/run_backtest.py --strategy-id barbell --no-online
# 控制台打印 [backtest] barbell_strategy OK total=... annual=... sharpe=... nav=reports/backtest/barbell_strategy_nav.csv
# 打开 reports/backtest/barbell_strategy_2026-08.md 看年化/夏普/最大回撤
```

> **📌 安全性质**：回测**只读** A 的契约 + 读 B 本地数据，**只写 B 本地 `reports/backtest/`**。不写共享目录、不读 `systemA_feedback/`。

### 4.3 完整流程三：把研究成果"发布"给项目A（B→A 供数）

> **📌 目的**：B 产出"决策参考包"写入共享目录，A 的「审核工作台」才看得到。**先 dry-run 本地验，再 real 发布**——这是铁律。

**✅ 步骤**：

**第 1 步：dry-run（本地验证，安全，可随意跑）**

```powershell
.venv\Scripts\python.exe scripts/run_reference_pipeline.py --dry-run
```

**💡 会看到什么**：控制台打印 `[dry-run] 三件套写入：outputs/`，产出：

```
outputs/grid_reference_table.csv        # 网格参考表
outputs/macro_hedge_efficiency.parquet  # 宏观对冲效率
outputs/decision_ref_package.json       # 决策参考包
outputs/assets_metadata.csv             # 资产质量清单
outputs/b_heartbeat.json                # 心跳
```

**第 2 步：确认无误后 real 发布**（带 `--include-stress`，联调要求）

```powershell
.venv\Scripts\python.exe scripts/run_reference_pipeline.py --real --include-stress
```

**💡 会看到什么**：写入共享目录 `D:\FF Project\data\integration\systemB_ref\{YYYYMMDD}\`（含 `.ready` 就绪标记、`package.json`），并更新 `manifest.json` 的 `newest_run` 和 `b_heartbeat.json`。

> **红线**：`--real` 是**跨系统边界**动作，只有真正需要给 A 供数时才跑。日常实验一律 `--dry-run`。

### 4.4 完整流程四：跑一次因子 tear sheet

> **📌 目的**：验证某个因子（如动量、低波）到底有没有预测力——看它的 IC 分布、分位收益、因子衰减、换手率四张图/表。

**✅ 步骤**：

```powershell
.venv\Scripts\python.exe -B -m qteasy_research.reference.factor_tear --source csv
```

**💡 会看到什么**：控制台打印 ASCII 汇总表（每个前瞻期一行 IC/RankIC/ICIR/spread/obs），并在 `reports/factor_tear/csv_source/` 下产出：

```
csv_source/csv/momentum_60d_ic_decay.csv       # IC 衰减
csv_source/csv/momentum_60d_quantile_returns.csv
csv_source/png/momentum_60d_tear_sheet.png     # 图
csv_source/png/low_volatility_20d_tear_sheet.png
```

---

### 4.5 完整流程五：组合分析（L3/L4）

> **📌 目的**：对一组资产做**组合级**风险收益分析——三口径年化波动（因子模型 / 样本 / EWMA）、年化收益、最大回撤、Sharpe、相关性矩阵、资产与因子两级风险贡献，以及 **L4 组合因子暴露 g=w'X**。最后给三组参考比例（风险平价 / 逆波动 / 等权），可选有效前沿。结果全部是 **REFERENCE_ONLY** 参考，**不产生交易指令**。

**输入**：一组权重（契约策略自动读取，或手填 `asset:w,asset:w`）+ 暴露窗口（默认 252，最小 120）。因子默认 `momentum_60d / momentum_120d / low_volatility_20d / liquidity_turnover`，可勾选含宏观因子 `ΔDGS30 / ΔDFII10`。

**✅ 步骤（命令行）**：

```powershell
# 1. 用契约策略 three_musketeers 跑（默认只写 B 本地 reports/）
.venv\Scripts\python.exe scripts/run_portfolio_analysis.py --strategy three_musketeers

# 2. 自定义权重 + 含宏观因子
.venv\Scripts\python.exe scripts/run_portfolio_analysis.py --weights 512890.SH:0.43,513650.SH:0.19,518880.SH:0.38 --include-macro

# 3. 给 A 共享目录供数（必须 --real；日常一律不发布）
.venv\Scripts\python.exe scripts/run_portfolio_analysis.py --strategy three_musketeers --publish --real
```

> ⚠️ 只有 `--publish` 才写 `systemB_ref/`；本任务默认**不写共享目录**。写共享目录必须 `--real`。

**✅ 步骤（桌面端）**：左侧「**组合分析**」页，四步引导：

- **① 选择组合**：契约策略（下拉）或自定义权重（`asset:w,asset:w`）；可勾选「含宏观因子」、改暴露窗口。
- **② 组合风险收益**：年化收益 / 三口径波动 / 最大回撤 / Sharpe；下方是波动一致性警告与被剔除资产。
- **③ 因子暴露与风险贡献**：左表 L4 组合因子暴露 g=w'X，右表资产级风险贡献 RC。
- **④ 比例建议**：风险平价 / 逆波动 / 等权三锚点，可选有效前沿。

**💡 示例（完整走一遍）**：

```powershell
.venv\Scripts\python.exe scripts/run_portfolio_analysis.py --strategy three_musketeers
```

输出在 `reports/portfolio_analysis/{run_id}/`：`summary.md`（全 ASCII）、`portfolio_analysis.json`、`exposure_matrix.csv`、`factor_cov.csv`、`correlation_matrix.csv`、`risk_contributions.csv`、`risk_parity_weights.csv`、`efficient_frontier.csv`。summary 里会看到三口径波动，差异过大时有警告。

**常见问题**：
- **为什么有的资产被剔除？** 暴露窗口内样本不足（<120）时，缺失**不虚构**：整行剔除并标注 `quality_level=D` 与警告；某因子有效资产 <3 个则整列剔除。
- **为什么有效前沿状态是 degraded？** 未装 scipy。风险平价自动降级为固定点迭代（+逆波动回退），有效前沿诚实省略，只给三个锚点，不影响其它功能。
- **比例建议能直接用吗？** 不能。全部是参考（REFERENCE_ONLY），且不自动改 A 的组合权重 / 回测配置。

---

## 5. 桌面端工作台（8 个导航页）

> **📌 用途**：不想记命令行时，用图形界面完成同样的事。启动：

```powershell
cd "D:\Project DPS\research_engines\qteasy_lab"
.venv\Scripts\python.exe -B -m qteasy_research.desktop
```

（也可双击 `启动投前研究桌面版.bat`。）

左侧 8 页导航：

| 页 | 名字 | 你用它做什么 |
|---|---|---|
| 1 | 研究项目 | 看已有项目、生成新版本、导出报告 |
| 2 | 新建研究 | 创建新的研究项目（表单填） |
| 3 | 因子研究 | 看因子定义/评分，配置启用权重，评分预览 |
| 4 | 数据管理 | 下载/同步行情数据，查本地数据 |
| 5 | 全球ETF宏观 | 全球宏观匹配、宏观规则审核（DRAFT→APPROVED） |
| 6 | 组合分析 | 组合风险收益 + 因子暴露 + 比例建议（L3/L4，四步引导） |
| 7 | 策略导入 | 解析策略源码 → 生成候选 → 人工核对 → 候选回测 |
| 8 | 系统设置 | 存储目录、数据模式、研究证据开关、指标参数 |

### 5.1 在桌面端做完整研究（对应 4.1 的图形版）

**第 1 步**：左侧「**新建研究**」→ 填表单（项目层级选"一级：单标的资产档案"、标的代码如 `518880.SH`、项目名称、研究目标）→ 点「**创建项目**」。

**第 2 步**：左侧「**研究项目**」→ 选中刚建的项目 → 点「**生成新版本**」→ 状态栏看进度（数据获取→技术指标→因子分析→报告生成）。

**第 3 步**：在「研究项目」页切到「**研究报告**」选项卡 → 选历史版本 → 点「导出 Markdown/HTML/PDF/完整报告包」→ 选目录保存。

### 5.2 在桌面端审核宏观规则

「**全球ETF宏观**」页 → 选中一条 DRAFT 规则 → 点「**确认**」升级 APPROVED（样本 ≥60 才可）、「设为临时」PROVISIONAL（样本 24~59）、「驳回」/「撤销」等。每次动作可填审核意见。

> **📌 为什么这里要人工**：契约 `REFERENCE_ONLY`，宏观规则**永不自动 APPROVED**，必须人工确认。这设计是为了防止机器自动批准研究结论去影响 A 的决策。

---

## 6. 命令行速查

> 所有命令在 `qteasy_lab` 目录执行，用 `.venv\Scripts\python.exe`。

| 想干什么 | 命令 |
|---|---|
| 看全部研究命令 | `.venv\Scripts\python.exe -B -m qteasy_research.pretrade --help` |
| 单标的投前研究 | `.venv\Scripts\python.exe -B -m qteasy_research.pretrade research 518880.SH --offline --provider none` |
| 历史回放研究 | 上面加 `--as-of-date 2023-01-15` |
| 建项目 | `.venv\Scripts\python.exe -B -m qteasy_research.pretrade project create "名字" 518880.SH --objective "..."` |
| 追问 | `.venv\Scripts\python.exe -B -m qteasy_research.pretrade followup {run_id} "问题" --provider deepseek` |
| 导出报告 | `.venv\Scripts\python.exe -B -m qteasy_research.pretrade export {run_id} --format pdf` |
| 策略回测 | `.venv\Scripts\python.exe scripts/run_backtest.py --strategy-id all --no-online` |
| 因子 tear sheet | `.venv\Scripts\python.exe -B -m qteasy_research.reference.factor_tear --source csv` |
| 给 A 供数（先 dry-run） | `.venv\Scripts\python.exe scripts/run_reference_pipeline.py --dry-run` |
| 给 A 供数（发布，带压力） | `.venv\Scripts\python.exe scripts/run_reference_pipeline.py --real --include-stress` |
| 宏观监控月报 | `.venv\Scripts\python.exe scripts/run_macro_monitoring.py` |
| 人机对比月报 | `.venv\Scripts\python.exe scripts/run_human_machine_compare.py` |
| 组合分析（L3） | `.venv\Scripts\python.exe scripts/run_portfolio_analysis.py --strategy three_musketeers` |
| 组合分析（自定义权重+宏观） | `.venv\Scripts\python.exe scripts/run_portfolio_analysis.py --weights 512890.SH:0.43,513650.SH:0.19,518880.SH:0.38 --include-macro` |

---

## 7. 系统已经有什么（功能速览）

> 只想确认"某功能有没有"，查这里。列到 2026-08-12。

**A 股投前研究**：单标的投前研究（ETF/股票/指数）、项目与版本管理、资产档案/策略组合两类项目、因子分析（momentum_60d/120d、低波、流动性）、结构化诊断、联网定性证据补研（可选）。

**全球 ETF 宏观**：FRED 四序列（2003 起）+ SPY/TLT/GLD 匹配、宏观条件收益、宏观规则五档 modifier（1.15/1.08/1.00/0.92/0.85）、状态机（DRAFT/PROVISIONAL/APPROVED/BASELINE/REJECTED）、交易资产映射与交易口径换算。

**参考维度四件套（B→A）**：`grid_reference_table.csv`、`macro_hedge_efficiency.parquet`、`decision_ref_package.json`（承载 `REFERENCE_ONLY`）、`assets_metadata.csv`，另含 `.ready`、`package.json`、`b_heartbeat.json`、`manifest.json`。

**策略级别回测引擎**：读 A 契约 + B 本地数据，事件驱动，防未来函数（T 收盘信号→T+1 成交），产出 NAV/交易/绩效摘要三件套 + 归因。当前 6 策略 = 5 OK + 1 SKIPPED。

**宏观监控框架 M1/M2/M3**：适配月报 / 相关性矩阵 / 极端情景韧性。

**factor_tear**：IC 分布/分位收益/因子衰减/换手率四类 tear sheet，覆盖 14 个 active ETF。

**human_machine_compare**：解析 A 的人工干预审计日志 → 人机对比月报（真实月报需日志 ≥30 条且 ≥3 策略，当前 4 条，预计 1-2 个月）。

**组合分析（L3/L4）**：组合风险收益（三口径波动/回撤/Sharpe）、相关性矩阵、资产与因子两级风险贡献、L4 组合因子暴露 g=w'X；比例建议（风险平价/逆波动/等权，可选有效前沿）。全部 REFERENCE_ONLY，默认只写 B 本地 `reports/portfolio_analysis/`。

---

## 8. 还没实现的功能（系统边界）

| 功能 | 状态 | 触发条件 |
|---|---|---|
| 参数扫描扩展（全策略参数） | ⬜ 待启动 | A 侧策略详情制订完成 |
| 因子有效性回溯测试 | ⬜ 待启动 | B 数据包连续运行 ≥1 个月 |
| human_machine_compare 真实月报 | ⬜ 等待触发 | 日志 ≥30 条且 ≥3 策略 |
| factor_tear 阶段二（生产管道验收） | ⬜ 待启动 | 数据满 1 个月后 |
| 利率状态样本满 60 后 APPROVED | ⬜ 等待样本 | 当前 52 样本，约 2027 年 |

> 这些都不影响你日常使用已交付的功能。

---

## 9. 数据维护

### 9.1 数据放哪 / 从哪来

- `research_store/`：研究库（SQLite）+ 快照 + Parquet 因子 + 项目/版本。
- `data/`：行情（`fund_daily.csv`、`index_daily.csv`）、交易日历、宏观。
- 数据源策略：A股 = AKShare → Tushare → 本地 CSV；全球 = FRED + Yahoo，本地优先。

### 9.2 更新 FRED / ETF 数据（月度流程）

```powershell
# 1. 抓取/更新全球宏观数据
.venv\Scripts\python.exe scripts/fetch_global_macro_data.py

# 2. 运行条件收益 Notebook（notebooks/global_macro_lab.ipynb）

# 3. 生成候选规则（dry-run → write）
.venv\Scripts\python.exe scripts/create_global_macro_rules.py --dry-run
.venv\Scripts\python.exe scripts/create_global_macro_rules.py --write --include-baseline --promote-provisional

# 4. 桌面端「全球ETF宏观」页人工确认/驳回规则
```

### 9.3 备份与还原

- 备份整个 `research_store/`（含 SQLite 与因子快照）即可。
- 历史报告、数据快照、冻结结论**不可覆盖**，另行归档。
- 不在 Git 里提交 `.venv`、SQLite、完整行情、报告、图表、日志、密钥。

---

## 10. 测试与验证

```powershell
cd "D:\Project DPS\research_engines\qteasy_lab"
.\.venv\Scripts\python.exe -B -m unittest discover -s "D:\Project DPS\tests" -q
```

当前结果（2026-08-12）：**445 tests OK（skipped=2）**。

> 从上级目录跑会报 `No module named 'qteasy_research'`——先 `cd` 到 `qteasy_lab` 再跑。

---

## 11. 常见问题

**Q1：桌面端打不开？** 装 PySide6：`.venv\Scripts\pip install PySide6`，再用 `启动投前研究桌面版.bat` 启动。

**Q2：全球ETF宏观页显示"PARTIAL"？** 某宏观状态没有生效规则时是**安全行为**，`final_score` 为空、不把缺失当中性。补 APPROVED 规则或人工在审核区处理即可。

**Q3：怎么配 FRED/Tushare？** 环境变量 `FRED_API_KEY` / `TUSHARE_TOKEN`（不入库）。FRED 也可下载 CSV 放 `data/raw/global_macro/` 用 `--local-only` 离线导入。

**Q4：交易口径换算显示"NO_MAPPING"？** 该资产没配生效的交易映射。在「全球ETF宏观」→交易资产映射区填表单保存后重算。

**Q5：怎么撤销一条已批准的宏观规则？** 桌面端「全球ETF宏观」→选中 APPROVED → 点「撤销」→ 填原因。引擎立即停止使用。

**Q6：策略导入会执行我的源码吗？** 不会。解析阶段只读源码，生成的候选代码必须人工核对后才允许回测。

**Q7：报 `No module named 'qteasy_research'`？** 工作目录错了，先 `cd "D:\Project DPS\research_engines\qteasy_lab"`。

**Q8：为什么 `--real` 不能随便跑？** 它向 A 的共享目录写决策包，属跨系统边界。日常一律 `--dry-run`。

**Q9：为什么宏观规则不能自动 APPROVED？** 契约 `approval_policy="REFERENCE_ONLY"`：B 是研究端，只产参考，机器不自动批准影响 A 决策。

**Q10：组合分析页显示"degraded"或"fallback"？** 未装 scipy 时的预期行为：风险平价走固定点迭代（+逆波动回退），有效前沿诚实省略，只给三锚点。功能可用，只是优化器降级。

---

## 12. 术语表

| 术语 | 意思 |
|---|---|
| 投前研究 | 买入前对标的研究，不产生交易指令 |
| 研究项目 / 运行 | 项目是组织单位，运行（run）是版本单位 |
| 资产档案 / 策略组合 | 一级项目（单标的）/ 二级项目（策略） |
| GlobalEtfEngine | 全球 ETF 宏观匹配评分引擎 |
| macro_modifier | 宏观修正系数（1.15/1.08/1.00/0.92/0.85） |
| final_score | `base_score × macro_modifier` |
| PARTIAL | 数据/规则不全时的安全状态 |
| DRAFT/PROVISIONAL/APPROVED/BASELINE/REJECTED | 宏观规则状态机 |
| 参考维度四件套 | grid + hedge + decision_package + asset_metadata |
| approval_policy | `REFERENCE_ONLY` = 仅参考，永不自动 APPROVED |
| 共享目录 | `D:\FF Project\data\integration\`，B 写、A 读 |
| systemA_feedback | A 的消费回执目录，**B 严禁读取** |
| factor_tear | 因子 tear sheet（IC/分位/衰减/turnover） |
| b_heartbeat.json | B 心跳，A 判断 B 是否在线 |

---

## 13. 安全边界与纪律（必须遵守）

**系统级**：
- B **不是自动交易系统**；不自动下单、不改资产池/组合权重/回测配置。
- 宏观规则 `approval_policy="REFERENCE_ONLY"` **永不自动 APPROVED**。
- FRED 数据缺失保持 `PARTIAL`、`final_score=null`，**不把缺失当中性**。
- 禁止 API token/密钥写入代码、日志、文档、Git。
- 禁止 `git reset --hard` / `git clean` 清理工作区。
- 测试必须从 `qteasy_lab` 目录执行。

**跨项目边界（单向数据流）**：
- B **只写** `systemB_ref/` + `b_heartbeat.json` + `manifest.json`；B 本地报告只写 `reports/`。
- B **只读** A 的 `config/`（仅交易指纹模块）+ `data/logs/` 审计日志。
- B **严禁读取/修改** `systemA_feedback/`。
- 每次 `--real` 运行带 `--include-stress`。

**机器产出纪律**：
- 机器产出全 ASCII，`strategy_id`/`asset_id` 标识，**零中文策略名**。
- 不把 `GlobalEtfEngine` 与 A 股 `factor_scoring.py` 混用。
- 不增加 KDJ；不自动生成交易指令；不覆盖历史研究版本、原始数据、研究快照。

---

*本说明书由协调层按项目B当前状态整理（2026-08-12）。状态变化以 `docs/PROJECT_B_STATUS.md` 与 `docs/CURRENT_STATE.md` 为准。*
