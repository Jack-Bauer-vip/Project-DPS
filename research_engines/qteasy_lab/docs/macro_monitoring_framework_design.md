# 宏观监控框架设计（三剑客 / 全球配置）

> 设计日期：2026-08-09 ｜ 状态：**已实现（2026-08-10）**
> 触发：A 侧策略详情就绪（NOTICE_20260809_strategy_details_ready.json），实现
> `qteasy_research/reference/macro_monitoring.py` + `scripts/run_macro_monitoring.py`，
> 产出 `reports/macro_monitoring/`（M-003 只写 B 本地 reports/），17 新增测试全绿。
>
> **核心边界（纪律）**：本设计只定义**「展示什么」**（输出格式 / 字段口径 / 文件位置），
> **不定义「该怎么做」**（不定义决策阈值、调仓规则、买卖建议）。所有展示值均为
> **描述性统计 / 情景模拟结果**，`approval_policy=REFERENCE_ONLY`，供 A 侧人工参考。

## 1. 背景与目标

A 侧后续将对接三剑客（`three_musketeers`：黄金/标普/红利）与全球配置
（`global_allocation`：10 标的）两个策略。B 侧在回测引擎已交付（`reports/backtest/`）的
基础上，提前设计**月度宏观监控框架**，向 A 侧输出三类监控数据：

| # | 监控项 | 服务对象 | 回答的问题 |
|---|---|---|---|
| M1 | 三剑客宏观适配月报 | 三剑客 | 当前宏观状态下，黄金/标普/红利各自表现如何？历史相似月份怎样？ |
| M2 | 相关性监控 | 全球配置 + 三剑客 | 10 标的之间、以及与三剑客基底，3 个月滚动相关性多高？ |
| M3 | 极端情景韧性测试 | 三剑客 + 全球配置 | 极端价格路径下，两策略净值模拟差异多大？ |

**本期只固化输出格式**。实现依赖：
- A 侧策略详情（契约 `target_weight` 定稿后，权重以契约为准；当前用契约 v1.0 权重）；
- B 侧监控脚本（复用既有模块，见 §3）。

## 2. 资产分组（数据源：契约 strategy_contract.json v1.0）

| 分组 | 资产 | 角色 |
|---|---|---|
| 三剑客基底 | `512890.SH`（红利低波 0.43）`513650.SH`（海外宽基/标普 0.19）`518880.SH`（黄金避险 0.38） | 三剑客的契约权重（示例） |
| 全球配置 | `159131.SZ 159516.SZ 159941.SZ 159985.SZ 512890.SH 513050.SH 513520.SH 513650.SH 518880.SH 562800.SH` | 契约 global_allocation 的 10 标的 |

> **注意**：三剑客基底（512890/513650/518880）与全球配置**共享三个资产**。M2 相关性
> 监控覆盖「全球配置 10 标的 × 全球配置 10 标的」全矩阵，并将基底三资产作为
> **参考列**单独呈现（避免矩阵内嵌重复自相关，见 §5.2）。

## 3. 复用能力映射（B 侧既有模块，不新造轮子）

| B 侧模块 | 输出 | 供监控项使用 |
|---|---|---|
| `macro_scenarios.build_monthly_scenario_table(data_root)` | 逐月末场景表：`states / rate_proxy / macro_unavailable / 9 状态布尔` | M1（当前宏观状态）、M3（情景月份选择） |
| `duration_phase.build_duration_phase(macro_table)` | 9 状态持续月数 + `phase` 标量 | M1（宏观背景） |
| `stress_simulator.build_stress_simulator(assets, aligned, macro_table, data_root)` | 每资产每情景历史月均收益 `pnl_pct / corr / confidence` | M1（历史相似表现）、M3（压力情景样本） |
| `volatility_cone.current_vol_rank(returns, window)` / `build_volatility_cone` | 当前波动率分位 + 分位锥 | M1（估值/风险分位列） |
| `rolling_beta.multi_benchmark_beta` | 各基准滚动 Beta | M1（市场暴露）、M2（相关性交叉验证） |
| `backtest_engine.run_backtest / build_phase_lookup / phase_asof` | 轻量事件驱动模拟器 + 点内 phase | M3（净值差异模拟） |
| `schema.AssetDimensions.valuation` | 估值字段（**当前为空，待数据源**） | M1（估值分位列，见 §4.3 缺口） |
| `metadata.embed_header_csv` | CSV 元数据头 | M1/M2/M3 全部 CSV 产出 |

## 4. 监控项 M1：三剑客宏观适配月报

### 4.1 输出

| 文件 | 内容 |
|---|---|
| `reports/macro_monitoring/{YYYYMM}_macro_fitness_{rule}.md` | Markdown 月报（每资产一行） |
| `reports/macro_monitoring/{YYYYMM}_macro_fitness_{rule}.csv` | 同内容的机器可读 CSV（带元数据头） |

`{rule}` = `three_musketeers` 与 `global_allocation` 各一份（M1 服务三剑客，但全球配置
的基底三资产同表呈现，便于对照）。

### 4.2 Markdown 表结构（展示「当前宏观状态 → 资产表现」）

```markdown
## Macro State (asof {month_end})
- phase: {rate_up|rate_down|curve_inverted|real_yield_up|...|na}
- state_durations: {rate_up: 3m, real_yield_up: 5m, ...}
- rate_proxy: {DGS30 值} | macro_unavailable: {true|false}

## Asset Macro Fitness（每资产一行）
| asset_id | 宏观适配分 | 历史相似表现 | 估值/波动分位 | 当前状态月均收益 | 全历史月均收益 | 样本月数 |
|---|---|---|---|---|---|---|
| 518880.SH | {0-1} | {state_hist_avg: +x.x%} | {vol_pctile: 78%} | {+1.2%} | {+0.4%} | {23} |
```

### 4.3 字段口径定义（仅展示，不构成建议）

| 字段 | 定义 | 数据来源 | 缺失行为 |
|---|---|---|---|
| 宏观适配分 | 当前宏观状态（`phase` + 9 状态）下该资产**历史月均收益映射的归一化值 0~1**（相对其自身跨状态表现的分位），**描述性统计，非买入评分** | `stress_simulator` 各状态 `pnl_pct` 或 `scenario_monthly_returns` 分组均值 → min-max 归一 | 样本 < `STRESS_MIN_SAMPLES` → `None` + `confidence=low` |
| 历史相似表现 | 当前宏观状态集合下该资产的历史月均收益（与当前 `states` **完全相同**的月份） | `macro_scenarios.scenario_monthly_returns` 按 `states` 匹配 | 无匹配月 → `n/a` |
| 估值/波动分位 | 当前年化波动率在自身历史的分位（20d/60d），及 `valuation`（若数据可用） | `volatility_cone.current_vol_rank`；`valuation` 字段 | 估值数据缺失 → 仅波动分位 + `valuation_na` 标注（**不虚构**） |
| 当前状态月均收益 | 当前宏观状态下该资产全部历史月份的平均月收益 | 同上 | 样本不足 → `n/a` |
| 全历史月均收益 | 该资产全部月份的月收益均值（对照基线） | 同上 | — |

**「历史相似月份表现」列 → 展开明细**（同表下方附录，每月最多 12 行）：

```markdown
## Historical Similar Months (asset=518880.SH, states={real_yield_up, rate_stable})
| month | asset_return | states |
|---|---|---|
| 2023-09-30 | +1.8% | real_yield_up, rate_stable |
| ... | ... | ... |
```

### 4.4 明确的「不做什么」

- 不定义「适配分 < X 即调仓/减仓」——分位仅描述，不触发任何决策。
- 不定义「历史相似月份表现好 → 建议持有/加仓」——仅展示历史回放。
- 不自动决策、不写入共享目录；只写 `reports/macro_monitoring/`。

## 5. 监控项 M2：相关性监控

### 5.1 覆盖范围

- **主矩阵**：全球配置 10 标的 × 10 标的（对称矩阵）。
- **参考列**：三剑客基底三资产（512890/513650/518880）作为附加列，
  「10 标的 × 基底 3 资产」交叉相关性（复用同一 3 个月窗口，避免重复自相关行）。

### 5.2 输出

| 文件 | 内容 |
|---|---|
| `reports/macro_monitoring/{YYYYMM}_correlation_matrix.csv` | 主矩阵 + 参考列（一行资产，行/列对称） |
| `reports/macro_monitoring/{YYYYMM}_high_corr_pairs.csv` | **高相关对列表**（`>0.7`），含方向 |
| `reports/macro_monitoring/{YYYYMM}_correlation_summary.md` | Markdown 摘要（矩阵 + 高相关对清单） |

### 5.3 CSV 格式

**主矩阵**（`embed_header_csv` 元数据头：`window_days=63` 即 3 个月滚动）：

```csv
#,512890.SH,513650.SH,...,562800.SH
159131.SZ,0.42,0.31,...,0.15
...
```

**高相关对列表**（机器可读的「标红」替代方案：CSV 无法标色，用独立对表 + `flag=HIGH_CORR`）：

```csv
asset_a,asset_b,corr_3m,window_start,window_end,flag
512890.SH,515180.SH,0.82,2026-05-06,2026-08-05,HIGH_CORR
518880.SH,513650.SH,0.11,2026-05-06,2026-08-05,-
```

Markdown 摘要中对应高相关对**加粗/标红**（`**0.82**`），阈值 0.7 为**展示标记阈值**，
非决策阈值。

### 5.4 口径

- **窗口**：滚动 63 个交易日（≈3 个月），逐月重算。
- **收益口径**：hfq close 日收益（与回测引擎一致）。
- **缺失**：标的未上市期间不参与该段相关性（`NaN`，矩阵留空，不填充 0）。
- **样本下限**：共同交易日 < 60 天 → 相关性 `n/a`（标注，不虚构）。

## 6. 监控项 M3：极端情景韧性测试

### 6.1 情景定义（默认四情景，实现时可按需增删）

| 情景 | 价格路径假设（B 侧假设，进报告 Assumptions） | 参考 |
|---|---|---|
| S1 黄金暴跌 30% | 518880.SH 单月 -30%（其余资产不变） | GLD 历史最大月跌幅参考 |
| S2 红利连续下跌 20% | 512890.SH 连续 3 个月累计 -20% | 红利指数历史回撤参考 |
| S3 利率急升 | DGS30 +50bp 月份重放（`stress_simulator` 历史月份） | STRESS_RATE_UP_BP |
| S4 美股回撤 | 513650.SH 连续 2 个月 -15% | SPY 历史回撤参考 |

### 6.2 模拟方法（复用回测引擎）

- 以契约权重（`target_weight`）为基准，用 `backtest_engine` 轻量模拟器构造**情景价格路径**
  （对受影响资产注入假设路径，其余按 B 本地真实行情）。
- 输出两策略（三剑客/全球配置）在情景下的**净值模拟差异**：
  - 无情景基线净值（真实行情）vs 情景净值（注入路径）；
  - 差异 = 情景净值 - 基线净值（金额 + %）。

### 6.3 输出

| 文件 | 内容 |
|---|---|
| `reports/macro_monitoring/{YYYYMM}_stress_scenarios.md` | Markdown 报告 |
| `reports/macro_monitoring/{YYYYMM}_stress_scenarios.csv` | 每情景每策略一行 |

### 6.4 Markdown 报告结构

```markdown
## Stress Scenario Simulation（{YYYY-MM}）
### S1 gold -30%
- Assumption: 518880.SH close -30% in 1 month, others unchanged (B-side; awaiting A)
- Three Musketeers: equity {baseline} -> {stressed} ({diff_pct})
- Global Allocation: equity {baseline} -> {stressed} ({diff_pct})
### S2 dividend -20%
...
## Sensitivity Notes
- Simulation is descriptive; no decision rule implied.
```

### 6.5 明确的「不做什么」

- 不定义「情景下跌 X% → 应防御/抄底/调仓」——仅展示净值差异。
- 不产出交易建议；情景参数（幅度/路径）为 **B 侧假设**，报告标注
  `B-side; awaiting A confirmation`，A 可提供情景幅度后重跑。

## 7. 输出物与目录约定

| 产出 | 路径 | 说明 |
|---|---|---|
| 设计文档 | `docs/macro_monitoring_framework_design.md` | 本文档 |
| 实现脚本 | `scripts/run_macro_monitoring.py` + `reference/macro_monitoring.py` | 已实现（2026-08-10），仿 `run_backtest.py` 模式 |
| 监控产出 | `reports/macro_monitoring/` | 全部 CSV/MD（M-003：只写 B 本地 reports/） |

**编码纪律**：机器产出全 ASCII，`strategy_id`/`asset_id` 标识，零中文策略名；
CSV 带 `embed_header_csv` 元数据头；假设统一 `- B-side; awaiting A confirmation`。

## 8. 数据依赖与缺口（实现前需确认）

| 数据项 | 状态 | 说明 |
|---|---|---|
| 宏观场景表 / phase / durations | ✅ B 侧已有 | `macro_scenarios` + `duration_phase` |
| 压力期历史收益 | ✅ B 侧已有 | `stress_simulator`（含 5 情景） |
| 波动率分位 | ✅ B 侧已有 | `volatility_cone` |
| 契约权重 | ⚠️ 待 A 定稿 | 实现时以契约为准（当前 v1.0） |
| **估值分位（PE/PB）** | ⚠️ **缺口** | B 侧无底层估值数据；`valuation` 字段预留。若 A 提供数据源则填入，否则该列 `valuation_na`（不虚构） |
| 情景幅度校准 | ⚠️ B 侧假设 | S1-S4 幅度为 B 侧默认值，A 可覆盖 |

## 9. 纪律声明

- **M-003 单向数据流**：只写 `reports/macro_monitoring/`，不写共享目录、不写 A 侧文件。
- **不自动决策**：所有展示值为描述性统计/情景模拟，`approval_policy=REFERENCE_ONLY`。
- **等待 A 侧通知**：A 完成策略详情制订后通知 B，届时再启动实现（本设计不编码）。
