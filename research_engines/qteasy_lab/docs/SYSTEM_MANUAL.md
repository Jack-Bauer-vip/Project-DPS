# Project DPS 投前研究系统 · 功能总结与使用说明书

> 版本：2026-08-06 ｜ 分支：`develop` ｜ 全量测试：181 项通过（2 跳过）

---

# 第一部分　系统功能总结

## 1. 系统概述

Project DPS（投前研究系统）是一个面向个人投资者的**投前研究工作台**，覆盖两条研究链路：

| 链路 | 数据源 | 研究对象 | 产出 |
|------|--------|----------|------|
| **A 股投前研究（基线）** | AKShare → Tushare → 本地快照 | ETF / 股票 / 指数 | 投前研究报告、因子评分、项目版本 |
| **全球 ETF 宏观研究（P0–P5）** | FRED + Yahoo Finance | SPY / TLT / GLD | 宏观条件收益、宏观规则、评分换算 |

系统以 **PySide6 桌面端**为主界面，同时提供**命令行（CLI）**与**脚本**作为批处理入口。所有研究结论遵循"确定性程序生成 + 人工确认"的分工：定量指标由程序计算，宏观规则只经人工确认后 APPROVED 生效（常态状态以 BASELINE 兜底、样本不足以 PROVISIONAL 临时参考并带警告），**不自动生成交易指令、不自动修改组合权重**。

## 2. 系统架构

### 2.1 A 股研究链路

```
AKShare / Tushare / 本地 CSV
        │
        ▼
SQLite（因子配置、研究项目、激活表） + Parquet（因子值）
        │
        ▼
因子评分器 factor_scoring → 综合评分 / 结构化诊断
        │
        ▼
报告引擎（Markdown / HTML / PDF / ZIP） → 桌面端展示
```

### 2.2 全球 ETF 研究链路

```
FRED（利率）+ Yahoo Finance（价格）
        │
        ▼
data/processed/global_macro（标准化 CSV，2003 起）
        │
        ▼
Notebook 条件收益表（月均收益/波动/回撤/胜率）
        │
        ▼
宏观规则（DRAFT/PROVISIONAL → 人工 APPROVED，常态 BASELINE 兜底，五档离散 modifier + 样本联动）
        │
        ▼
GlobalEtfEngine → base_score × macro_modifier → 评分快照
        │
        ▼
交易资产映射 → 交易口径换算（费用差异 + 成本折减）
```

### 2.3 引擎隔离

- `GlobalEtfEngine` 只读全球宏观数据与全球 ETF 表，**不导入** A 股因子评分器。
- 两类模块共享 `research.sqlite3` 但使用不同表。
- 数据缺失时**不把缺失当作中性**，返回 `PARTIAL` 并点名缺失项，属安全设计。

## 3. 功能模块详解

### 3.1 A 股投前研究（基线）

- **单资产投前研究**：对 ETF / 股票执行投前研究，产出报告（Markdown / HTML / PDF / ZIP 导出）。
- **研究项目与版本**：研究项目是组织单位、研究运行是版本单位，支持创建、关闭、重开、生成新版本。
- **资产档案**：单标的资产档案（一级项目）、策略/组合项目（二级项目），组合可配置目标比例、买卖触发条件、止盈止损与研究假设。
- **因子分析**：`momentum_60d`、`momentum_120d`、低波动（`low_volatility_20d`）、流动性（`liquidity_turnover`）因子骨架，因子值以 Parquet 存储。
- **结构化诊断**：因子缺失、日期越界、资产类型错误等诊断信息。
- **联网定性证据补研**：可选的联网来源事实补研（需配置 LLM Provider）。

### 3.2 全球 ETF 宏观研究

- **数据恢复与维护**（P0）：FRED 四序列（DGS10 / DGS2 / DGS30 / DFII10，2003 起，质量 A）+ SPY / TLT / GLD（2003 起，质量 B）；支持 FRED 本地 CSV 导入与网络回退，`available_at` 点内时点过滤。
- **宏观条件收益**（P1）：Notebook 产出各资产在不同宏观状态下的月均收益、波动、最大回撤、胜率，样本全部 ≥24 个月。
- **宏观规则体系**（P1）：五档离散 modifier（偏差 ±0.5/±1.5pp → 1.15/1.08/1.00/0.92/0.85）+ 样本联动（<24 仅参考 / 24–59 临时 PROVISIONAL / ≥60 可 APPROVED）。当前 BASELINE 9 条（常态状态）、PROVISIONAL 6 条（rate_up/rate_down 样本 24~59）、APPROVED 5 条（real_yield 状态）、DRAFT 1 条。
- **评分引擎**（P2）：`GlobalEtfEngine` 按优先级 APPROVED > PROVISIONAL > BASELINE 匹配每个宏观状态的生效规则，`final_score = base_score × macro_modifier`；常态状态以 BASELINE（modifier=1.00）兜底，某状态完全无规则仍返回 PARTIAL。输出 CSV / JSON 快照。
- **交易资产映射**（P3）：研究资产 ↔ 交易资产 1:N 映射，含汇率、管理费、交易成本、跟踪误差、折溢价、交易时区/时段/休市风险字段。
- **交易口径换算**（P4）：相对费用差异 + 一次性交易成本折减，把研究评分换算为交易口径独立参考值；汇率不调整收益仅展示敞口提示；研究评分来源警告（临时/常态兜底）透传到换算表 tooltip。
- **规则审核与版本**（P5）：`global_etf_macro_rule_history` 历史版本链；审核状态机 approve / reject / revoke / reset / promote / demote，规则状态含 DRAFT / PROVISIONAL / APPROVED / BASELINE / REJECTED；驳回/撤销保留审计记录。

### 3.3 桌面端工作台（7 个页面）

| 导航 | 页面 | 核心能力 |
|------|------|----------|
| 研究项目 | 项目列表 / 详情 | 按类型筛选、查看最近研究版本、生成新版本、关联资产档案、编辑组合标的与触发条件 |
| 新建研究 | 项目创建表单 | 一级资产档案 / 二级策略组合，填标的风险与目标后创建 |
| 因子研究 | FactorResearchPage | 因子列表筛选、因子定义与离线研究结论、因子启用配置（资产×期限×权重）、评分预览 |
| 数据管理 | DataManagementPage | 数据连接检查、导入 CSV、更新 SQLite、生成行情因子、本地行情查询 |
| 全球ETF宏观 | GlobalEtfPage | 数据状态、宏观状态、评分表格、条件收益表、规则审核、交易口径换算、交易资产映射配置 |
| 策略导入 | 策略代码导入 | 解析 TXT / Notebook 策略源码 → 候选模板（需人工核对）→ 确认后运行回测 |
| 系统设置 | 本地存储与数据模式 | 研究数据目录、数据模式（直连/混合/仅本地）、联网证据开关、技术指标参数 |

### 3.4 命令行与脚本

| 入口 | 用途 |
|------|------|
| `python -m qteasy_research.pretrade` | 投前研究 CLI：`research` / `project` / `followup` / `export` 子命令 |
| `run_backtest.py` | ETF 组合策略历史回测（等权 / 风险平价 / 逆波动 / 动量） |
| `run_macro.py` | 中国宏观因子驱动资产配置（初始化 / 更新 / 仪表盘 / 回测 / 优化） |
| `scripts/fetch_global_macro_data.py` | 全球宏观数据抓取（FRED + Yahoo，本地优先 + 网络回退） |
| `scripts/create_global_macro_rules.py` | 从条件收益表生成宏观规则（dry-run / write / approve） |
| `scripts/build_*.py` | 因子打包（momentum / volatility / liquidity / PB → Parquet） |
| `scripts/0*.py` | qteasy 数据配置与导入系列脚本（01–09） |

### 3.5 数据源与存储

**数据源策略**：A 股链路 AKShare → 第三方 Tushare API → 本地 CSV / SQLite 快照回退；全球链路 FRED（API Key 优先 / graph URL 回退）+ Yahoo Finance，本地 CSV 优先。

**存储结构**：
- `research_store/`：`research.sqlite3`（研究项目、运行、因子配置、宏观规则与历史、交易映射、评分快照）+ `cache/`（数据快照 JSON）+ `factor_values/`（Parquet 因子）+ `projects/` 与 `runs/`（研究版本、报告、图表）
- `data/`：`trade_calendar.csv`、`index_basic/daily`、`fund_basic/daily`、`macro/`（中国宏观因子）、`processed/global_macro/`（全球宏观标准化数据）、`raw/`（原始抓取）、`global_etf_values/`（引擎评分输出）

### 3.6 安全边界与决策约束

- 不自动修改正式资产池、不自动调整组合权重、不自动生成交易指令。
- 不覆盖历史报告、数据快照或冻结结论。
- 宏观修正系数必须人工确认；生效规则按 APPROVED > PROVISIONAL > BASELINE 优先级查找，某状态完全无规则时 `final_score` 为 null（不把缺失当作中性）。
- 常态状态（`rate_stable`/`curve_normal`/`real_yield_stable`）以 BASELINE（modifier=1.00）落库并参与评分；样本 24~59 可提升为 PROVISIONAL 临时生效，待样本积累到 60（约 2027 年）复核升级 APPROVED；`curve_inverted`（期限结构倒挂）属异常信号不兜底。
- API Key / Token 只通过环境变量读取，不入库不入 Git。

---

# 第二部分　详细使用说明书

## 4. 环境要求与安装

### 4.1 系统要求

- Windows 11（或 Windows 10 1903+）
- Python 3.11+
- Git 2.55+
- 网络：首次数据抓取需联网；离线研究可用本地 CSV / 快照

### 4.2 安装依赖

进入项目根目录，使用虚拟环境安装依赖：

```bash
cd "D:\Project DPS\research_engines\qteasy_lab"
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install PySide6        # 桌面端必需
```

> 若已存在 `.venv`，可直接使用；Jupyter 内核建议指向本 venv。

### 4.3 配置环境变量

| 变量 | 用途 | 必需 |
|------|------|------|
| `TUSHARE_TOKEN` | Tushare API Token | 网络数据源必需 |
| `FRED_API_KEY` | FRED API Key | 全球宏观网络抓取（本地导入可免） |
| LLM Provider Key | 联网定性证据补研（如 DeepSeek） | 可选 |

> Token / Key 仅从环境变量读取，不会写入项目文件或 Git。

## 5. 快速开始

### 5.1 启动桌面端

双击任一启动脚本：

- **`启动投前研究桌面版.bat`**（推荐，已设置 UTF-8 编码）
- `start_desktop.bat`

或命令行启动：

```bash
cd "D:\Project DPS\research_engines\qteasy_lab"
.venv\Scripts\python.exe -B -m qteasy_research.desktop
```

启动后出现 **"ETF Research Desk · 投前研究工作台"** 主窗口，左侧导航 7 项。

### 5.2 首次使用流程

1. **系统设置** → 确认研究数据目录（默认 `research_store`）、数据模式（默认"直连优先"）。
2. **新建研究** → 创建一级资产档案项目（如 `518880.SH` 黄金 ETF）或二级策略组合项目。
3. **因子研究** → 检查因子定义、启用配置，输入代码列表做评分预览。
4. **全球ETF宏观** → 查看数据状态与宏观状态，配置交易资产映射，点击"重新计算"生成评分。

## 6. 桌面端页面使用详解

### 6.1 研究项目

- **左侧**：类型筛选（全部 / 资产档案 / 策略组合）+ 项目列表。
- **右侧**：选择项目后查看项目信息与最近一次研究版本报告。
- **生成新版本**：对选中项目重新执行研究，生成新的版本快照。
- **关联资产档案**：在输入框填资产档案代码（如 `518880.SH`）后点击"关联"，建立组合与资产的引用。
- **组合标的编辑**：策略项目可逐行编辑组合标的与触发条件，格式：
  `代码 | 目标比例 | 买入条件 | 卖出条件`（或含止盈止损的扩展格式），保存后生效。

### 6.2 新建研究

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

> 组合配置仅保存，不自动套用固定回测策略。

### 6.3 因子研究

- **因子列表（左）**：按类别 / 资产类型 / 期限 / 状态筛选因子，看状态标签。
- **因子详情（中）**：Tab 查看"因子定义"（经济假设 / 公式 / 值语义 / MD5）与"离线研究结论"。
- **启用配置（右）**：按资产类型 + 期限维度配置因子权重、最大回撤、暂停开关。
- **评分预览（底）**：输入代码列表 + 目标日期，计算因子综合评分，展示评分表与结构化诊断（因子缺失、日期越界、资产类型错误等）。

### 6.4 数据管理

- **数据连接与更新（上）**：
  - 模式：直连优先（AKShare → Tushare → 本地）/ 数据中台。
  - 数据集：日线 / 基础资料；代码范围；日期区间；更新策略。
  - 按钮：检查连接、导入 CSV、更新到 SQLite、生成行情因子。
- **本地数据查询（下）**：输入代码查询本地行情，表格展示。

### 6.5 全球ETF宏观

自上而下六个区：

1. **数据状态与当前宏观状态**：各序列截至日期与质量等级、利率代理（DGS30，缺失时 DGS10 降级并警告）、引擎状态、各资产宏观状态枚举。
2. **宏观规则状态**：状态筛选（全部 / DRAFT / PROVISIONAL / APPROVED / BASELINE / REJECTED），规则状态列按色区分（APPROVED 绿 / PROVISIONAL 橙 / BASELINE 灰 / DRAFT 蓝 / REJECTED 红）；选中规则后可用 **确认**（DRAFT/PROVISIONAL→APPROVED）、**设为临时**（DRAFT→PROVISIONAL，样本 24~59 临时生效）、**取消临时**（PROVISIONAL→DRAFT）、**驳回**（DRAFT/PROVISIONAL→REJECTED）、**撤销**（APPROVED→REJECTED）、**重新提交**（REJECTED→DRAFT）、**历史**（查看版本链）。每次动作可填写审核意见。
3. **SPY/TLT/GLD 宏观匹配评分**：基础评分、宏观修正、最终评分、支持/冲突因子、样本数；"重新计算"以目标日期生成评分并落快照。
4. **条件收益表**：展示 Notebook 产出的条件收益（月均收益 / 波动 / 最大回撤 / 胜率）。
5. **交易口径换算**：对最近一次评分，取每个研究资产的生效映射换算为交易口径评分（费用差异 + 成本折减），独立参考，不改动研究评分；研究评分来源警告（如"使用临时规则/按常态基准"）与汇率说明悬停行可见。
6. **交易资产映射**：选择研究资产 → 映射表格 + 表单。字段含交易资产代码/名称、币种、汇率方式/汇率、管理费、交易成本、跟踪误差、折溢价、优先级；支持保存 / 停用 / 删除，表格提示当前生效映射（ACTIVE + 最小 priority）。

**自动抓取**：在"交易资产代码"输入代码后按回车或失焦，自动抓取并回填**名称、管理费、折溢价、跟踪误差**（也可点"自动抓取"按钮）：
- 名称 / 管理费：优先读本地 `data/fund_basic.csv`，无则回退 Tushare API（需 `TUSHARE_TOKEN`）。
- 折溢价：AKShare 场内 ETF 实时行情（`基金折价率`）。
- 跟踪误差：从本地基金日收益与基准指数日收益的年化标准差计算；本地缺基准指数数据时留空并提示人工填写。
- 抓取失败不阻塞，已获取字段照常回填，未获取字段提示原因。

**汇率方式四选项**（悬停下拉可见说明）：
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

### 6.6 策略导入

1. 选择 `.txt` 或 `.ipynb` 策略源码文件。
2. 点击"解析策略"，左侧显示解析结果（**解析阶段只读源码，不执行**）。
3. 点击"生成候选代码"，生成候选策略模板。
4. 确认回测资产池（默认 `518880.SH,159941.SZ,513050.SH`）。
5. 点击"确认并运行候选回测"——**必须人工核对候选代码后**才允许回测。

### 6.7 系统设置

- **研究数据目录**：选择本地存储目录（默认 `%LOCALAPPDATA%/qteasy_research` 配置指向的目录）。
- **数据模式**：直连优先 / 本地优先 / 仅本地离线。
- **研究证据**：启用 / 关闭联网定性证据补研。
- **技术指标参数**：MACD（fast/slow/signal）、RSI 周期等，保存后用于研究计算。

## 7. 命令行使用

### 7.1 投前研究 CLI

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

# 导出报告（md / html / pdf）
.venv\Scripts\python.exe -m qteasy_research.pretrade export <run_id> --format pdf
.venv\Scripts\python.exe -m qteasy_research.pretrade export <run_id> --bundle
```

**research 子命令主要参数**：`--benchmark`（基准）、`--horizon`（short/medium/long）、`--provider`（none/ollama/deepseek）、`--offline`、`--data-mode`（direct/hybrid/local）、`--no-evidence`、`--update-policy`（reuse/check_update/refresh/force_refresh）、`--as-of-date`、`--indicator-config`（JSON）。

### 7.2 策略回测

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

### 7.3 中国宏观因子系统

```bash
.venv\Scripts\python.exe run_macro.py --init          # 首次初始化（下载全部因子历史，联网）
.venv\Scripts\python.exe run_macro.py --update        # 更新最新数据
.venv\Scripts\python.exe run_macro.py --dashboard     # 输出宏观仪表盘
.venv\Scripts\python.exe run_macro.py --backtest      # 运行回测验证
.venv\Scripts\python.exe run_macro.py --optimize      # 参数网格搜索优化
.venv\Scripts\python.exe run_macro.py                 # 无参数：输出当前评分
```

### 7.4 全球宏观数据抓取

```bash
# 默认（本地优先，缺失时联网）
.venv\Scripts\python.exe scripts/fetch_global_macro_data.py

# 强制走网络
.venv\Scripts\python.exe scripts/fetch_global_macro_data.py --prefer-network

# 仅本地导入（离线）
.venv\Scripts\python.exe scripts/fetch_global_macro_data.py --local-only

# 指定系列与日期区间
.venv\Scripts\python.exe scripts/fetch_global_macro_data.py --series DGS10 SPY --start 2020-01-01
```

### 7.5 宏观规则生成

```bash
# 查看候选规则（不写库）
.venv\Scripts\python.exe scripts/create_global_macro_rules.py --dry-run

# 写入 DRAFT 规则（--include-baseline 同时为常态状态生成 BASELINE）
.venv\Scripts\python.exe scripts/create_global_macro_rules.py --write
.venv\Scripts\python.exe scripts/create_global_macro_rules.py --write --include-baseline

# 写入后把样本 24~59 的 DRAFT 批量提升为 PROVISIONAL
.venv\Scripts\python.exe scripts/create_global_macro_rules.py --write --promote-provisional

# 将 eligible DRAFT/PROVISIONAL 翻为 APPROVED
.venv\Scripts\python.exe scripts/create_global_macro_rules.py --approve

# 幂等迁移：补齐常态 BASELINE 并批量提升样本 24~59 的 DRAFT（--dry-run 预览 / --apply 执行）
.venv\Scripts\python.exe scripts/migrate_rule_status.py --dry-run
.venv\Scripts\python.exe scripts/migrate_rule_status.py --apply
```

> 生效规则按优先级 APPROVED > PROVISIONAL > BASELINE 参与评分；样本 <60 的规则以 PROVISIONAL 临时生效（仅供参考），样本满 60 后由人工确认升级 APPROVED。

## 8. 数据维护

### 8.1 更新 FRED / ETF 数据

每月数据更新流程：

```bash
# 1. 抓取/更新全球宏观数据
.venv\Scripts\python.exe scripts/fetch_global_macro_data.py

# 2. 运行条件收益 Notebook（生成 condition_returns.csv）
#    notebooks/global_macro_lab.ipynb

# 3. 生成候选规则并审阅（--include-baseline 补常态 BASELINE，--promote-provisional 提升样本不足规则）
.venv\Scripts\python.exe scripts/create_global_macro_rules.py --dry-run
.venv\Scripts\python.exe scripts/create_global_macro_rules.py --write --include-baseline --promote-provisional

# 4. 幂等迁移兜底（可选：补齐常态 BASELINE / 批量提升样本 24~59 为 PROVISIONAL）
.venv\Scripts\python.exe scripts/migrate_rule_status.py --apply

# 5. 在桌面端"全球ETF宏观"页人工确认/驳回/撤销规则（样本满 60 可"确认"升级 APPROVED）
```

### 8.2 生成因子数据

```bash
# 把因子原始值打包为 Parquet（供因子研究页使用）
.venv\Scripts\python.exe scripts/build_momentum.py --input-csv <路径> --output-dir research_store/factor_values
.venv\Scripts\python.exe scripts/build_volatility.py --input-csv <路径> --output-dir research_store/factor_values
```

### 8.3 备份与还原

- 研究数据目录（默认 `research_store/`）含 SQLite 与因子快照，整体备份即可。
- 历史报告、数据快照与冻结结论**不可覆盖**，需另行归档。
- 不在 Git 中提交 `.venv`、SQLite、完整行情、报告、图表、日志与 API 密钥（见 `.gitignore`）。

## 9. 测试与验证

全量离线测试（必须从 `qteasy_lab` 目录执行）：

```powershell
cd "D:\Project DPS\research_engines\qteasy_lab"
.\.venv\Scripts\python.exe -B -m unittest discover -s "D:\Project DPS\tests" -q
```

当前结果：

```text
Ran 181 tests
OK (skipped=2)
```

覆盖：GlobalEtfEngine fixture（5）、FRED 本地导入（15）、宏观规则（15）、映射（19）、交易换算（19）、规则审核状态机（15）、桌面端页面/映射/审核 UI（15）、规则状态体系 BASELINE/PROVISIONAL（6）等。

> 若从上级目录执行出现 `ModuleNotFoundError: No module named 'qteasy_research'`，表示未把 `qteasy_lab` 加入模块路径，请改用上述从 `qteasy_lab` 目录执行的命令。

## 10. 常见问题（FAQ）

**Q1：桌面端打不开 / 报缺少 PySide6？**
安装桌面依赖：`.venv\Scripts\pip install PySide6`，然后用 `启动投前研究桌面版.bat` 启动。

**Q2：全球ETF宏观页显示"引擎返回 PARTIAL"？**
可能原因与处理：
- **已按状态体系兜底**：常态状态（`curve_normal` 等）以 BASELINE（modifier=1.00）参与评分，样本不足状态（`rate_up` 样本 <60）可提升为 PROVISIONAL 临时生效，三者组合后 `macro_modifier`/`final_score` 可正常计算，评分显示 COMPLETED，并附"使用临时规则/按常态基准"警告提示数据缺口。
- **仍显示 PARTIAL**：某宏观状态完全没有规则（如异常状态 `curve_inverted` 无 BASELINE 兜底）。此时 `macro_modifier`/`final_score` 为空属安全行为，不把缺失当作中性；可为该状态补 APPROVED 规则或在桌面端审核区处理。

**Q3：如何配置 FRED / Tushare 数据？**
通过环境变量设置 `FRED_API_KEY` 与 `TUSHARE_TOKEN`（不入库）。FRED 也可手动下载 CSV 放 `data/raw/global_macro/`，用 `fetch_global_macro_data.py --local-only` 离线导入。

**Q4：交易口径换算区显示"NO_MAPPING"？**
该研究资产未配置生效的交易资产映射。在"交易资产映射"区选择该研究资产，填写表单并"保存映射"后重算即可。

**Q5：如何撤销一条已批准的规则？**
桌面端"全球ETF宏观"→"宏观规则状态"区选中 APPROVED 规则 → 点"撤销"，填写原因后提交；历史记录中会保留 revoke 动作，引擎立即停止使用该规则。

**Q6：策略导入会执行我的源码吗？**
不会。解析阶段只读源码，生成的是候选策略模板，必须人工核对后才可运行回测。

## 11. 附：目录结构速查

```
qteasy_lab/
├── qteasy_research/
│   ├── core/                    # 全球ETF引擎、交易映射、交易换算
│   ├── desktop/                 # PySide6 桌面端（主窗口 + 各页面）
│   ├── pretrade/                # A股投前研究（CLI/编排/报告/存储）
│   ├── macro/                   # 中国宏观因子系统
│   ├── strategies/              # 回测策略（等权/风险平价/逆波动/动量）
│   └── factor_lab.py            # A股因子评分器
├── scripts/                     # 数据抓取、规则生成、因子打包脚本
├── docs/                        # 架构、数据源、项目状态、决策记录
├── notebooks/                   # 全球宏观条件收益研究 Notebook
├── data/                        # 本地数据（日历/指数/基金/宏观/全球）
├── research_store/              # 研究库（SQLite/因子/项目/运行/评分）
├── run_backtest.py              # 回测入口
├── run_macro.py                 # 中国宏观系统入口
├── start_desktop.bat            # 桌面端启动脚本
└── 启动投前研究桌面版.bat         # 桌面端启动脚本（UTF-8）
```
