---
version: 1.0
updated_at: 2026-08-11
status: current
---

# 项目B · 用户使用说明书（USER_MANUAL）

> 本说明书面向**全新用户**：不预设你了解本项目的历史，按章节顺序读完即可上手。
> 阅读路径：先看「第一部分 系统功能总结」建立整体认知，再按「第二部分 详细使用说明」逐节操作。
> 配套文档：`AI_HANDOFF.md`（交接入口，给 AI）、`docs/SYSTEM_MANUAL.md`（系统功能总结与使用说明书）。

---

# 第一部分　系统功能总结

## 1. 系统是什么（30 秒概览）

项目B（`D:\Project DPS\research_engines\qteasy_lab`，内部代号 qteasy_lab）是一个面向个人投资者的**投前研究工作台**，基于 qteasy 回测系统构建。它覆盖四条能力：

| 能力 | 干什么 | 产出 |
|------|--------|------|
| **A 股投前研究** | 对 ETF / 股票 / 指数做单标的投前研究，管理研究项目与版本 | 投前研究报告（Markdown / HTML / PDF / ZIP） |
| **因子研究 / 评分** | 维护因子定义与启用配置，计算综合因子评分并输出结构化诊断 | 因子评分表、因子 tear sheet |
| **全球 ETF 宏观匹配** | 用 FRED 利率 + Yahoo Finance 价格，匹配 SPY / TLT / GLD 的宏观状态，用宏观规则修正基础评分 | 宏观条件收益、宏观规则、评分快照、交易口径换算 |
| **策略回测** | 基于 qteasy 的既有回测 + 项目B自研的策略级别事件驱动回测引擎 | 回测报告、绩效摘要、归因分析 |

两条核心研究链路：

- **A 股投前研究链路（基线）**：AKShare → Tushare → 本地 CSV/SQLite 快照 → 因子评分器 → 报告引擎。
- **全球 ETF 宏观研究链路（P0–P5）**：FRED（利率）+ Yahoo Finance（价格）→ 标准化数据 → 条件收益 Notebook → 宏观规则（人工审核）→ `GlobalEtfEngine` 评分。

系统以 **PySide6 桌面端**为主界面（7 个导航页），同时提供**命令行（CLI）**与**脚本**作为批处理入口。所有研究结论遵循「**确定性程序生成 + 人工确认**」的分工：定量指标由程序计算，宏观规则**只经人工确认后 APPROVED 生效**，**不自动生成交易指令、不自动修改组合权重/资产池/回测配置**。

> 30 秒记住这句话：**本项目是"研究端"，只产出参考结论，不做交易决策，不碰资金。**

## 2. 与系统A（FF Project）的关系：B→A 单向流水线

项目B 与项目A（`D:\FF Project`，个人 A股/ETF 投研、策略、持仓分析与交易辅助系统，**执行/决策端**）不是两个孤立系统，而是**同一条研究→决策流水线的两端**。接口契约已冻结于 `D:\FF Project\docs\integration\SYSTEM_B_CONTRACT.md`（v1.1，生效 2026-08-06）。

**单向数据流（铁律）**：

```text
B（研究端）→ 只写共享目录 → A（决策端）→ 只读 → A 写消费回执
```

- **共享目录**：`D:\FF Project\data\integration\`
  - **B 写**：`b_heartbeat.json`、`manifest.json`、`systemB_ref/{YYYYMMDD}/`（含 `.ready`、`package.json`、`decision_ref_package.json`、`assets_metadata.csv`、`grid_reference_table.csv`、`macro_hedge_efficiency.parquet`）。
  - **A 写**：`systemA_feedback/consumed_{YYYYMMDD}.json`、`strategy_contracts/strategy_contract.json` + NOTICE。
- **权限边界**：A 绝不修改 `systemB_ref/`；**B 绝不读取 `systemA_feedback/`**；B 只读 A 的 `config/`（仅交易指纹模块离线分析用）与 `data/logs/` 审计日志。

**approval_policy="REFERENCE_ONLY"（核心纪律）**：

B 产出的决策参考包携带 `approval_policy="REFERENCE_ONLY"`，含义是**所有结论仅作参考，永不自动 APPROVED**。宏观规则必须由人工在桌面端审核确认后才生效；没有 APPROVED 规则时，`final_score` 保持 `null`（**不把缺失宏观数据当中性值**），这是安全设计而非故障。

> 一句话：**B 只喂"参考"，A 才做"决策"；B 永不自动批准任何东西。**

## 3. 工作目录硬约束（第一条纪律，请先记住）

**一切 Python 命令必须从 `D:\Project DPS\research_engines\qteasy_lab` 目录执行。**

```powershell
cd D:\Project DPS\research_engines\qteasy_lab
```

从上级目录（如 `D:\Project DPS`）直接执行会出现：

```text
ModuleNotFoundError: No module named 'qteasy_research'
```

这不是系统坏了，而是**工作目录错误**。本说明书后续所有命令均假设你已处于 `qteasy_lab` 目录。

---

# 第二部分　详细使用说明

## 4. 安装与环境

### 4.1 系统要求

- Windows 11（或 Windows 10 1903+）
- Python 3.11+
- Git 2.55+
- 网络：首次数据抓取需联网；离线研究可用本地 CSV / 快照

### 4.2 安装依赖

进入项目根目录，使用虚拟环境安装依赖（本项目已预置 `.venv`，若已存在可直接跳过）：

```bash
cd "D:\Project DPS\research_engines\qteasy_lab"
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install PySide6        # 桌面端必需
```

> **统一使用 `.venv\Scripts\python.exe`**（而不是系统 python）执行所有命令，保证依赖一致。Jupyter 内核建议指向本 venv。

### 4.3 配置环境变量

| 变量 | 用途 | 必需 |
|------|------|------|
| `TUSHARE_TOKEN` | Tushare API Token | 网络数据源必需 |
| `FRED_API_KEY` | FRED API Key | 全球宏观网络抓取（本地导入可免） |
| LLM Provider Key | 联网定性证据补研（如 DeepSeek） | 可选 |

PowerShell 设置示例：

```powershell
$env:TUSHARE_TOKEN="你的Token"
$env:FRED_API_KEY="你的Key"
```

**安全铁律**：Token / Key **只从环境变量读取，绝不写入代码、文档、报告或 Git**。不要把 `.venv`、SQLite、完整行情、日志、报告和 API 密钥提交到 Git（`.gitignore` 已配置）。

## 5. 桌面端工作台（7 个导航页）

### 5.1 启动桌面端

双击任一启动脚本：

- **`启动投前研究桌面版.bat`**（推荐，已设置 UTF-8 编码）
- `start_desktop.bat`

或命令行启动：

```bash
cd "D:\Project DPS\research_engines\qteasy_lab"
.venv\Scripts\python.exe -B -m qteasy_research.desktop
```

启动后出现 **"ETF Research Desk · 投前研究工作台"** 主窗口，左侧导航 7 项：

1. 研究项目
2. 新建研究
3. 因子研究
4. 数据管理
5. 全球ETF宏观
6. 策略导入
7. 系统设置

### 5.2 研究项目

- **左侧**：类型筛选（全部项目 / 资产档案 / 策略组合）+ 项目列表。
- **右侧**：选择项目后查看项目信息与最近一次研究版本报告。
- **生成新版本**：对选中项目重新执行研究，生成新的版本快照。
- **关联资产档案**：在输入框填资产档案代码（如 `518880.SH`）后点击"关联"，建立组合与资产的引用。
- **组合标的编辑**：策略项目可逐行编辑组合标的与触发条件，格式：
  `代码 | 目标比例 | 买入条件 | 卖出条件`（或含止盈止损的扩展格式），保存后生效。

### 5.3 新建研究

填写项目基本信息后点击"创建项目"：

| 字段 | 说明 |
|------|------|
| 项目层级 | 一级：单标的资产档案 / 二级：策略/组合项目 |
| 标的代码 | 如 `518880.SH` |
| 策略名称 | 可选，记录策略名称不强制套用 |
| 项目名称 | 必填 |
| 研究目标 | 该项目想回答的问题 |
| 研究期限 | short / medium / long |
| 组合配置 | 二级项目可预填多行组合（代码 | 比例 | 期限 | 角色 | 买入 | 卖出 | 止盈 | 止损 | 研究假设） |

> 组合配置**仅保存**，不自动套用固定回测策略。

### 5.4 因子研究

- **因子列表（左）**：按类别 / 资产类型 / 期限 / 状态筛选因子，看状态标签。
- **因子详情（中）**：Tab 查看"因子定义"（经济假设 / 公式 / 值语义 / MD5）与"离线研究结论"。
- **启用配置（右）**：按资产类型 + 期限维度配置因子权重、最大回撤、暂停开关。
- **评分预览（底）**：输入代码列表 + 目标日期，计算因子综合评分，展示评分表与结构化诊断（因子缺失、日期越界、资产类型错误等）。

### 5.5 数据管理

- **数据连接与更新（上）**：
  - 模式：直连优先（AKShare → Tushare → 本地）/ 数据中台。
  - 数据集：日线 / 基础资料；代码范围；日期区间；更新策略。
  - 按钮：检查连接、导入 CSV、更新到 SQLite、生成行情因子。
- **本地数据查询（下）**：输入代码查询本地行情，表格展示。

### 5.6 全球ETF宏观

自上而下六个区：

1. **数据状态与当前宏观状态**：各序列截至日期与质量等级、利率代理（DGS30，缺失时 DGS10 降级并警告）、引擎状态、各资产宏观状态枚举。
2. **宏观规则状态**：状态筛选（全部 / DRAFT / PROVISIONAL / APPROVED / BASELINE / REJECTED），规则状态列按色区分（APPROVED 绿 / PROVISIONAL 橙 / BASELINE 灰 / DRAFT 蓝 / REJECTED 红）。选中规则后可用：
   - **确认**（DRAFT/PROVISIONAL→APPROVED）
   - **设为临时**（DRAFT→PROVISIONAL，样本 24~59 临时生效）
   - **取消临时**（PROVISIONAL→DRAFT）
   - **驳回**（DRAFT/PROVISIONAL→REJECTED）
   - **撤销**（APPROVED→REJECTED）
   - **重新提交**（REJECTED→DRAFT）
   - **历史**（查看版本链）
   - 每次动作可填写审核意见。
3. **SPY/TLT/GLD 宏观匹配评分**：基础评分、宏观修正、最终评分、支持/冲突因子、样本数；"重新计算"以目标日期生成评分并落快照。
4. **条件收益表**：展示 Notebook 产出的条件收益（月均收益 / 波动 / 最大回撤 / 胜率）。
5. **交易口径换算**：对最近一次评分，取每个研究资产的生效映射换算为交易口径评分（费用差异 + 成本折减），独立参考，不改动研究评分；研究评分来源警告（如"使用临时规则/按常态基准"）与汇率说明悬停行可见。
6. **交易资产映射**：选择研究资产 → 映射表格 + 表单。字段含交易资产代码/名称、币种、汇率方式/汇率、管理费、交易成本、跟踪误差、折溢价、优先级；支持保存 / 停用 / 删除，表格提示当前生效映射（ACTIVE + 最小 priority）。

**自动抓取**：在"交易资产代码"输入代码后按回车或失焦，自动抓取并回填**名称、管理费、折溢价、跟踪误差**（也可点"自动抓取"按钮）：

- 名称 / 管理费：优先读本地 `data/fund_basic.csv`，无则回退 Tushare API（需 `TUSHARE_TOKEN`）。
- 折溢价：AKShare 场内 ETF 实时行情（`基金折价率`）。
- 跟踪误差：从本地基金日收益与基准指数日收益的年化标准差计算；本地缺基准指数数据时留空并提示人工填写。
- 抓取失败不阻塞，已获取字段照常回填，未获取字段提示原因。

**汇率方式四选项**：

| 选项 | 含义 |
|------|------|
| `static` | 静态固定汇率。QDII 净值已含汇率，收益不再额外调整，需填写汇率。 |
| `manual` | 手动维护汇率，无自动更新。 |
| `realtime` | 实时汇率，联网取当前市价汇率。 |
| `estimate` | 估计汇率（估算值，精度较低）。 |

**操作示例——配置 SPY 的交易资产映射**：

1. 在"交易资产映射"区选择研究资产 `SPY`。
2. 表单"交易资产代码"输入 `513500.SH`，回车/失焦自动抓取名称、管理费、折溢价等。
3. 补填：币种 `CNY`、汇率方式 `static`、汇率 `7.10`、交易成本 `20`（bps）；检查自动回填的字段可手动修正。
4. 点"保存映射"。
5. 重算评分后，"交易口径换算"区显示 SPY 的交易口径评分。

### 5.7 策略导入

1. 选择 `.txt` 或 `.ipynb` 策略源码文件。
2. 点击"解析策略"，左侧显示解析结果（**解析阶段只读源码，不执行**）。
3. 点击"生成候选代码"，生成候选策略模板。
4. 确认回测资产池（默认 `518880.SH,159941.SZ,513050.SH`）。
5. 点击"确认并运行候选回测"——**必须人工核对候选代码后**才允许回测。

### 5.8 系统设置

- **研究数据目录**：选择本地存储目录（默认 `%LOCALAPPDATA%/qteasy_research` 配置指向的目录）。
- **数据模式**：直连优先 / 本地优先 / 仅本地离线。
- **研究证据**：启用 / 关闭联网定性证据补研。
- **技术指标参数**：MACD（fast/slow/signal）、RSI 周期等，保存后用于研究计算。

## 6. 命令行使用

> 所有命令均须在 `qteasy_lab` 目录下执行。`python` 一律用 `.venv\Scripts\python.exe`。

### 6.1 投前研究 CLI（research / project / followup / export）

```bash
# 执行单标的投前研究
.venv\Scripts\python.exe -m qteasy_research.pretrade research 518880.SH --horizon medium

# 离线模式（只用本地 CSV）
.venv\Scripts\python.exe -m qteasy_research.pretrade research 518880.SH --offline

# 历史回放（只用指定日期以前的数据）
.venv\Scripts\python.exe -m qteasy_research.pretrade research 518880.SH --as-of-date 2023-01-15

# 管理项目
.venv\Scripts\python.exe -m qteasy_research.pretrade project create 我的黄金项目 518880.SH --objective 判断黄金当前配置价值
.venv\Scripts\python.exe -m qteasy_research.pretrade project list
.venv\Scripts\python.exe -m qteasy_research.pretrade project close <project_id>

# 对既有研究运行继续研究（followup）
.venv\Scripts\python.exe -m qteasy_research.pretrade followup <run_id> --question 追加问题

# 导出报告（md / html / pdf）
.venv\Scripts\python.exe -m qteasy_research.pretrade export <run_id> --format pdf
.venv\Scripts\python.exe -m qteasy_research.pretrade export <run_id> --bundle
```

**research 子命令主要参数**：`--benchmark`（基准）、`--horizon`（short/medium/long）、`--provider`（none/ollama/deepseek）、`--offline`、`--data-mode`（direct/hybrid/local）、`--no-evidence`、`--update-policy`（reuse/check_update/refresh/force_refresh）、`--as-of-date`、`--indicator-config`（JSON）。

### 6.2 策略回测（run_backtest.py，qteasy 经典入口）

```bash
# 等权策略回测（默认）
.venv\Scripts\python.exe run_backtest.py

# 动量策略
.venv\Scripts\python.exe run_backtest.py --strategy momentum --lookback 126 --top-n 3

# 对比多个策略
.venv\Scripts\python.exe run_backtest.py --compare --strategies equal_weight,momentum,risk_parity,inverse_vol

# 参数调整
.venv\Scripts\python.exe run_backtest.py --start 20190801 --cash 100000 --fee 0.00016 --benchmark 000300.SH --freq ME
```

**可用策略**：`equal_weight`（等权）、`risk_parity`（风险平价）、`inverse_vol`（逆波动）、`momentum`（动量）。

### 6.3 中国宏观因子系统（run_macro.py）

```bash
.venv\Scripts\python.exe run_macro.py --init          # 首次初始化（下载全部因子历史，联网）
.venv\Scripts\python.exe run_macro.py --update        # 更新最新数据
.venv\Scripts\python.exe run_macro.py --dashboard     # 输出宏观仪表盘
.venv\Scripts\python.exe run_macro.py --backtest      # 运行回测验证
.venv\Scripts\python.exe run_macro.py --optimize      # 参数网格搜索优化
.venv\Scripts\python.exe run_macro.py                 # 无参数：输出当前评分
```

### 6.4 参考维度管线（run_reference_pipeline.py，B→A 端到端）

这是把「参考维度四件套」产出到共享目录的关键入口：

```bash
# 默认 dry-run：只写本地 outputs/，不碰共享目录（安全，可随意试）
.venv\Scripts\python.exe scripts/run_reference_pipeline.py --dry-run

# 真实写入共享目录 D:\FF Project\data\integration\systemB_ref\{YYYYMMDD}\
.venv\Scripts\python.exe scripts/run_reference_pipeline.py --real

# 带上压力情景损益（宏观压力模拟器，阶段三）
.venv\Scripts\python.exe scripts/run_reference_pipeline.py --real --include-stress

# 指定数据截止日（data_asof）
.venv\Scripts\python.exe scripts/run_reference_pipeline.py --real --target-date 2026-08-11
```

> **红线**：`--real` 会写共享目录，只应在真正需要给 A 供数时运行；日常实验一律用 `--dry-run`。每次 `--real` 运行需带 `--include-stress`（联调修复要求，见 PROJECT_B_STATUS.md）。

### 6.5 因子 tear sheet（factor_tear，alphalens 借鉴）

对因子做 IC 分布 / 分位收益 / 因子衰减 / turnover 四类 tear sheet：

```bash
# 从行情 CSV（data/fund_daily.csv）对全量标的即时计算
.venv\Scripts\python.exe -m qteasy_research.reference.factor_tear --source csv

# 从因子 Parquet（research_store/factor_values/*.parquet）读取
.venv\Scripts\python.exe -m qteasy_research.reference.factor_tear --source parquet
```

输出到 `reports/factor_tear/`（`csv_source/` 与 `parquet_source/` 两个子目录，各含 `csv/` 与 `png/`）。常用参数：`--csv-path`（行情 CSV 路径）、`--factor-dir`（因子 parquet 目录）、`--out-dir`、`--no-csv`、`--no-png`。

### 6.6 其它参考维度脚本速查

| 脚本 | 用途 | 输出位置 |
|------|------|----------|
| `scripts/run_backtest.py` | **策略级别回测**：读 A 侧策略规则契约 + B 本地数据，事件驱动回测 | `reports/backtest/` |
| `scripts/run_backtest_supplements.py` | 回测补充产出：per-asset 归因 + 网格触网汇总 | `reports/backtest/` |
| `scripts/run_macro_monitoring.py` | 宏观监控三剑客（M1 适配月报 / M2 相关性 / M3 极端情景韧性） | `reports/macro_monitoring/` |
| `scripts/run_param_sweep.py` | 参数网格扫描（离线研究工具，只写 B 本地） | `reports/param_sweep/` |
| `scripts/run_human_machine_compare.py` | 人机对比月报（正式月报需带 `--package` 指定决策包） | `reports/human_machine_compare/{YYYY-MM}_hmc.md` |
| `scripts/fetch_global_macro_data.py` | 全球宏观数据抓取（FRED + Yahoo，本地优先 + 网络回退） | `data/raw/global_macro/` 等 |
| `scripts/create_global_macro_rules.py` | 从条件收益表生成宏观规则（dry-run / write / approve） | SQLite |
| `scripts/migrate_rule_status.py` | 幂等迁移：补齐常态 BASELINE、批量提升样本 24~59 为 PROVISIONAL | SQLite |
| `scripts/build_*.py` | 因子打包（momentum / momentum_120d / volatility / liquidity / pb → Parquet） | `research_store/factor_values/` |
| `scripts/0*.py` | qteasy 数据配置与导入系列脚本（01–09） | — |

**策略级别回测示例**：

```bash
# 回测全部策略（只读契约，--no-online 离线）
.venv\Scripts\python.exe scripts/run_backtest.py --strategy-id all --no-online

# 按策略 ID / 决策规则别名
.venv\Scripts\python.exe scripts/run_backtest.py --strategy-id barbell,grid_lh --no-online
.venv\Scripts\python.exe scripts/run_backtest.py --strategy-id mid_line --no-macro-tilt --no-online
```

产出 `{strategy_id}_nav.csv` + `{strategy_id}_trades.csv` + `{strategy_id}_{YYYYMM}.md`（绩效摘要），外加补充归因文件。**只写 B 本地 `reports/backtest/`，不写共享目录、不读 `systemA_feedback/`**。

## 7. 现有功能清单

### 7.1 A 股投前研究链路

- 单资产投前研究（ETF / 股票 / 指数），报告 Markdown / HTML / PDF / ZIP 导出。
- 研究项目与版本：研究项目是组织单位、研究运行是版本单位，支持创建、关闭、重开、生成新版本。
- 资产档案（一级项目）与策略/组合项目（二级项目），组合可配置目标比例、买卖触发条件、止盈止损与研究假设。
- 因子分析：`momentum_60d`、`momentum_120d`、低波动（`low_volatility_20d`）、流动性（`liquidity_turnover`）因子骨架，因子值以 Parquet 存储。
- 结构化诊断：因子缺失、日期越界、资产类型错误等。
- 联网定性证据补研（可选，需配置 LLM Provider）。
- A 股因子评分器 `factor_lab.py` 及诊断信息。

### 7.2 全球 ETF 宏观链路

- 数据恢复与维护：FRED 四序列（DGS10 / DGS2 / DGS30 / DFII10，2003 起，质量 A）+ SPY / TLT / GLD（2003 起，质量 B）；支持 FRED 本地 CSV 导入与网络回退。
- 宏观条件收益：Notebook 产出各资产在不同宏观状态下的月均收益、波动、最大回撤、胜率，样本全部 ≥24 个月。
- 宏观规则体系：五档离散 modifier（偏差 ±0.5/±1.5pp → 1.15/1.08/1.00/0.92/0.85）+ 样本联动（<24 仅参考 / 24–59 临时 PROVISIONAL / ≥60 可 APPROVED）。
- 评分引擎 `GlobalEtfEngine`：按 APPROVED > PROVISIONAL > BASELINE 匹配生效规则，`final_score = base_score × macro_modifier`；常态状态以 BASELINE 兜底，某状态完全无规则返回 `PARTIAL`（`macro_modifier`/`final_score` 为 null，安全设计）。
- 交易资产映射（研究资产 ↔ 交易资产 1:N，含汇率、管理费、交易成本、跟踪误差、折溢价、交易时区/时段/休市风险字段）。
- 交易口径换算：相对费用差异 + 一次性交易成本折减，把研究评分换算为交易口径独立参考值。
- 规则审核与版本：历史版本链；审核状态机 approve / reject / revoke / reset / promote / demote；规则状态含 DRAFT / PROVISIONAL / APPROVED / BASELINE / REJECTED。

### 7.3 参考维度四件套（B→A 决策包）

`run_reference_pipeline.py --real` 产出四件套到共享目录 `systemB_ref/{YYYYMMDD}/`：

| 文件 | 内容 |
|------|------|
| `grid_reference_table.csv` | 网格参考表（B1-1） |
| `macro_hedge_efficiency.parquet` | 宏观对冲效率（B1-2） |
| `decision_ref_package.json` | 决策参考包（承载 `approval_policy="REFERENCE_ONLY"`、逐资产 `red_flag`、`macro_stress` 等） |
| `assets_metadata.csv` | 资产元数据（B1-3） |

另含 `.ready` 就绪标记、`package.json` 包元信息；根级 `b_heartbeat.json`（心跳，A 以 3 天判断 B 是否在线）+ `manifest.json`（清单，保留最近 30 版）+ `backup/`。

### 7.4 策略级别回测引擎

- 轻量事件驱动模拟器（`reference/backtest_engine.py`），读 A 侧 `strategy_contract.json`（只读）+ B 本地行情/宏观。
- 防未来函数：T 收盘信号 → T+1 收盘成交；首日建仓 T0 先验；宏观 phase 点内（`< T`）。
- 产出三件套到 `reports/backtest/`，并有 per-asset 归因 + 网格触网汇总补充产出。
- 当前 6 策略 = 5 OK + 1 SKIPPED（`reports/backtest/`）。

### 7.5 宏观监控框架 M1/M2/M3

- M1 适配月报 / M2 相关性矩阵 / M3 极端情景韧性（`reference/macro_monitoring.py` + `scripts/run_macro_monitoring.py`）。
- 月度产出到 `reports/macro_monitoring/`（适配月报、高相关对、压力情景表等）。

### 7.6 factor_tear 因子 tear sheet

- `reference/factor_tear.py`（alphalens 借鉴，未直接引入依赖）：IC 分布 / 分位收益 / 因子衰减 / turnover 四类 tear sheet。
- 因子链路覆盖 14 个 active ETF（`data_market_daily` + 4 因子 Parquet，2026-08-11）。
- 阶段一已交付；阶段二「生产管道健康度验收」待启动。

### 7.7 human_machine_compare 人机对比

- 分析引擎 `reference/human_machine_compare.py` + CLI `scripts/run_human_machine_compare.py`。
- 解析 A 侧人工干预审计日志 → B 信号三态 / 方向一致性三分类 / 偏离度量 / 干预后收益 / 聚合 → Markdown 月报。
- 输出 `reports/human_machine_compare/{YYYY-MM}_hmc.md`，离线不写共享目录。
- 当前基于合成数据验证通过；**真实月报待 `human_override_log` ≥30 条且 ≥3 策略**（当前 4 条，预计 1-2 个月）。

## 8. 后续待实现功能

以下为待办，**多数是"条件触发"任务——触发条件未满足时，B 不编码**。

| 优先级 | 任务 | 状态 | 触发条件 |
|---|---|---|---|
| 1 | 策略级别回测引擎 | ✅ 已交付（开发 + 端到端验证 + 补充产出） | — |
| 2 | 宏观监控框架实现 | ✅ 已交付（M1/M2/M3 + CLI + 17 测试） | 已触发（A 侧策略详情 NOTICE_20260809 就绪） |
| 3 | **参数扫描扩展** | ⬜ 待启动 | A 侧策略详情制订完成，提供参数范围 |
| 4 | **因子有效性回溯测试** | ⬜ 待启动 | B 数据包连续运行 ≥1 个月 + A 侧策略详情完成 |
| 5 | **human_machine_compare 真实月报** | ⬜ 等待触发 | `human_override_log` ≥30 条且覆盖 ≥3 策略（当前 4 条） |
| 6 | **factor_tear 阶段二「生产管道健康度验收」** | ⬜ 待启动 | 阶段一已交付（2026-08-11），阶段二待启动 |
| 7 | **利率状态样本积累后 APPROVED** | ⬜ 等待样本 | rate_up / rate_down 样本 <60（当前 52），约 2027 年样本满 60 后人工复核升级 APPROVED |

## 9. 数据维护

### 9.1 数据源策略

- **A 股链路**：AKShare → 第三方 Tushare API → 本地 CSV / SQLite 快照回退。
- **全球链路**：FRED（API Key 优先 / graph URL 回退）+ Yahoo Finance，本地 CSV 优先。
- 存储结构：`research_store/`（`research.sqlite3` 研究库 + `cache/` 快照 + `factor_values/` Parquet 因子 + `projects/` 与 `runs/` 研究版本）；`data/`（`trade_calendar.csv`、`index_basic/daily`、`fund_basic/daily`、`macro/`、`processed/global_macro/`、`raw/`、`global_etf_values/`）。

### 9.2 更新 FRED / ETF 数据（月度流程）

```bash
# 1. 抓取/更新全球宏观数据
.venv\Scripts\python.exe scripts/fetch_global_macro_data.py

# 2. 运行条件收益 Notebook（notebooks/global_macro_lab.ipynb，生成 condition_returns.csv）

# 3. 生成候选规则并审阅
.venv\Scripts\python.exe scripts/create_global_macro_rules.py --dry-run
.venv\Scripts\python.exe scripts/create_global_macro_rules.py --write --include-baseline --promote-provisional

# 4. 幂等迁移兜底（可选）
.venv\Scripts\python.exe scripts/migrate_rule_status.py --apply

# 5. 在桌面端"全球ETF宏观"页人工确认/驳回/撤销规则（样本满 60 可"确认"升级 APPROVED）
```

### 9.3 生成因子数据

```bash
# 把因子原始值打包为 Parquet（供因子研究页使用）
.venv\Scripts\python.exe scripts/build_momentum.py --input-csv <路径> --output-dir research_store/factor_values
.venv\Scripts\python.exe scripts/build_volatility.py --input-csv <路径> --output-dir research_store/factor_values
```

### 9.4 备份与还原

- 研究数据目录（默认 `research_store/`）含 SQLite 与因子快照，整体备份即可。
- 历史报告、数据快照与冻结结论**不可覆盖**，需另行归档。
- 不在 Git 中提交 `.venv`、SQLite、完整行情、报告、图表、日志与 API 密钥（见 `.gitignore`）。

## 10. 测试与验证

全量离线测试（**必须从 `qteasy_lab` 目录执行**）：

```powershell
cd "D:\Project DPS\research_engines\qteasy_lab"
.\.venv\Scripts\python.exe -B -m unittest discover -s "D:\Project DPS\tests" -q
```

当前结果（2026-08-11 阶段）：**331 tests OK（skipped=2）**（PROJECT_B_STATUS / CURRENT_STATE 记录）。覆盖：GlobalEtfEngine fixture、FRED 本地导入、宏观规则、映射、交易换算、规则审核状态机、桌面端页面、规则状态体系 BASELINE/PROVISIONAL、压力模拟器、参数扫描、策略回测引擎、宏观监控、human_machine_compare、factor_tear 等。

> 若从上级目录执行出现 `ModuleNotFoundError: No module named 'qteasy_research'`，表示未把 `qteasy_lab` 加入模块路径，请改用上述从 `qteasy_lab` 目录执行的命令。

## 11. 常见问题（FAQ）

**Q1：桌面端打不开 / 报缺少 PySide6？**
安装桌面依赖：`.venv\Scripts\pip install PySide6`，然后用 `启动投前研究桌面版.bat` 启动。

**Q2：全球ETF宏观页显示"引擎返回 PARTIAL"？**
可能原因与处理：
- **已按状态体系兜底**：常态状态（`curve_normal` 等）以 BASELINE（modifier=1.00）参与评分，样本不足状态（`rate_up` 样本 <60）可提升为 PROVISIONAL 临时生效，三者组合后 `macro_modifier`/`final_score` 可正常计算，评分显示 COMPLETED，并附"使用临时规则/按常态基准"警告提示数据缺口。
- **仍显示 PARTIAL**：某宏观状态完全没有规则（如异常状态 `curve_inverted` 无 BASELINE 兜底）。此时 `macro_modifier`/`final_score` 为空属安全行为，**不把缺失当作中性**；可为该状态补 APPROVED 规则或在桌面端审核区处理。

**Q3：如何配置 FRED / Tushare 数据？**
通过环境变量设置 `FRED_API_KEY` 与 `TUSHARE_TOKEN`（不入库）。FRED 也可手动下载 CSV 放 `data/raw/global_macro/`，用 `fetch_global_macro_data.py --local-only` 离线导入。

**Q4：交易口径换算区显示"NO_MAPPING"？**
该研究资产未配置生效的交易资产映射。在"交易资产映射"区选择该研究资产，填写表单并"保存映射"后重算即可。

**Q5：如何撤销一条已批准的规则？**
桌面端"全球ETF宏观"→"宏观规则状态"区选中 APPROVED 规则 → 点"撤销"，填写原因后提交；历史记录中会保留 revoke 动作，引擎立即停止使用该规则。

**Q6：策略导入会执行我的源码吗？**
不会。解析阶段只读源码，生成的是候选策略模板，必须人工核对后才可运行回测。

**Q7：运行命令报 `No module named 'qteasy_research'`？**
工作目录错误。先 `cd "D:\Project DPS\research_engines\qteasy_lab"` 再执行。

**Q8：为什么 `--real` 写共享目录被禁止随便跑？**
`--real` 会向 `D:\FF Project\data\integration\systemB_ref\{YYYYMMDD}\` 写入供 A 消费的决策包，属于跨系统边界行为。日常实验一律 `--dry-run`（写本地 `outputs/`）。

**Q9：为什么宏观规则不能自动 APPROVED？**
契约铁律 `approval_policy="REFERENCE_ONLY"`：B 是研究端，所有结论仅作参考；宏观规则必须人工确认后 APPROVED，避免机器自动批准研究结论直接影响 A 的决策。

## 12. 术语表

| 术语 | 含义 |
|------|------|
| **投前研究** | 交易前对标的做的基本面/技术/宏观研究，不产生交易指令 |
| **研究项目 / 运行** | 项目是组织单位，运行（run）是版本单位 |
| **资产档案** | 一级项目：单标的资产档案 |
| **策略组合** | 二级项目：策略/组合项目，可配置标的与触发条件 |
| **因子** | 如 `momentum_60d`，以 Parquet 存储的量化特征 |
| **GlobalEtfEngine** | 全球 ETF 宏观匹配评分引擎 |
| **宏观状态** | 如 `rate_up` / `curve_normal` / `real_yield_up` 三元组 |
| **macro_modifier** | 宏观修正系数（1.15 / 1.08 / 1.00 / 0.92 / 0.85） |
| **final_score** | `base_score × macro_modifier`；无 APPROVED 规则时为 null |
| **PARTIAL** | 引擎数据/规则不全时的安全状态，不把缺失当中性 |
| **DRAFT / PROVISIONAL / APPROVED / BASELINE / REJECTED** | 宏观规则状态机 |
| **参考维度四件套** | grid + hedge + decision_package + asset_metadata |
| **决策参考包** | `decision_ref_package.json`，承载 `approval_policy="REFERENCE_ONLY"` |
| **approval_policy** | 审批策略；`REFERENCE_ONLY` = 仅参考，永不自动 APPROVED |
| **red_flag** | 逐资产风险红旗标记 |
| **macro_stress** | 宏观压力情景损益 |
| **共享目录** | `D:\FF Project\data\integration\`，B 写、A 读 |
| **systemA_feedback** | A 侧消费回执目录，**B 严禁读取** |
| **factor_tear** | 因子 tear sheet（IC / 分位收益 / 衰减 / turnover） |
| **human_machine_compare** | 人机对比月报（B 信号 vs 人工干预） |
| **param_sweep** | 参数网格扫描 |
| **b_heartbeat.json** | B 心跳文件，A 以 3 天判断 B 在线 |

## 13. 安全边界与纪律（两边共同，必须遵守）

**系统级**：

- 项目B 不是自动交易系统；**不自动下单、不自动改资产池/组合权重/回测配置**。
- B 侧宏观规则 `approval_policy="REFERENCE_ONLY"` 永不自动 APPROVED。
- FRED 数据缺失时保持 `PARTIAL`、`final_score=null`，**不把缺失当中性**。
- 禁止 API token/密钥写入代码、日志、文档、Git。
- 禁止 `git reset --hard` / `git clean` 清理工作区。
- B 侧测试必须从 qteasy_lab 目录执行（`python -m qteasy_research.*` 依赖 cwd）。

**跨项目边界（M-003 单向数据流）**：

- **B 只写** `systemB_ref/` + `b_heartbeat.json` + `manifest.json`；B 侧本地研究报告只写 `reports/`。
- **B 只读** A 的 `config/` 配置文件（仅 trader_fingerprint 模块离线分析）+ `data/logs/` 审计日志（`human_override_log` + `actual_trade_ledger`）。
- **B 严禁读取 `systemA_feedback/` 内容做分析依据**；严禁修改其中任何文件。
- 每次 `--real` 运行需带 `--include-stress`。

**机器产出纪律**：

- 机器产出全 ASCII，`strategy_id` / `asset_id` 标识，**零中文策略名**。
- 不把 `GlobalEtfEngine` 与 A 股 `factor_scoring.py` 混合（引擎隔离）。
- 不增加 KDJ；不自动生成交易指令；不覆盖历史研究版本、原始数据或研究快照。

---

*本说明书由协调层按项目B当前状态整理（2026-08-11）。状态变化以 `docs/PROJECT_B_STATUS.md` 与 `docs/CURRENT_STATE.md` 为准。*
