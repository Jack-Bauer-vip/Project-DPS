# human_machine_compare（人机对比月报）前期设计

> 设计日期：2026-08-07 ｜ 状态：**分析引擎已编码（2026-08-07，基于合成数据）**
> 本文档固化的输出格式、口径、数据源映射与依赖清单已落地为代码：
> `reference/human_machine_compare.py` + `scripts/run_human_machine_compare.py`（31 测试全绿）。
> 真实月报仍待 `human_override_log` 积累（≥30 条且覆盖 ≥3 策略，约 2026-11 后评估）。

## 1. 背景与目标

系统B（qteasy_lab）向系统A单向输出通用参考维度（决策包 `decision_ref_package.json`）。
`human_machine_compare` 是月度**人机对比**：把 B 的参考维度（**应然**）与 A 的人工干预/
实际交易（**实然**）并排对照，回答三个问题：

1. 人工干预的方向是否偏离 B 参考信号（一致 / 背离 / 中性）？
2. 干预后资产表现如何（干预有效 / 无效）？
3. 决策过程是否透明可审计（干预了什么、为什么、在什么宏观背景下）？

**核心依赖**：A 侧 `human_override_log`（人工干预审计日志）积累 **≥3 个月** 后启动。
当前该表仅 4 行（1 策略、2 资产），本文档据此验证结构与口径，但**不编码实现**。

## 2. 数据源映射（A 侧字段 → B 侧分析列）

已对照 A 侧真实文件字段核实（2026-08-07）。

| A 侧数据源 | 字段 | → B 侧用途 |
|---|---|---|
| `data/logs/human_override_log.csv`（Tab 分隔） | `time` | 干预时点，按月分组 |
| | `strategy_id` | 分组键 |
| | `asset_id` | join B 决策包 `assets[].asset_id` 的键 |
| | `old_weight` / `new_weight` | 偏离量 `delta = new - old` |
| | `reason` | 场景分类（当前全为 `confirmation_center 网页人工修改`） |
| `config/actual_trade_ledger.csv`（22 列） | `trade_date, strategy_id, asset_id, side, qty, amount` | 干预后实际成交、持仓变化 |
| | `decision_source` | `manual`（当前全 manual）vs 未来 `machine` |
| | `confirm_status` | 当前全 `CONFIRMED`，非确认行需过滤 |
| B `decision_ref_package.json` | `assets[].volatility / returns / beta / red_flag / macro_stress` | 干预时该资产的参考维度基线 |
| | `macro_regime.phase / state_durations / states` | 干预时宏观背景 |

**join 语义（重要）**：B 参考维度是**资产级**，A 干预是**策略×资产级**。实现时
**先按 `asset_id` join B 决策包**，**再按 `strategy_id` 分组**；同一资产被多策略共享时，
B 维度对该资产的所有策略干预一致（共享基线）。文档明确此语义，避免实现时混用粒度。

## 3. 口径设计（对比维度）

### 3.1 干预识别
从 `human_override_log` 提取 `(month, strategy_id, asset_id, old_weight, new_weight, reason)`。
按月分组；同一月同一 `(strategy_id, asset_id)` 多次干预取末次（或累计 delta）。

### 3.2 系统建议对照
干预月份取 B 决策包中该 `asset_id` 的维度快照：
- `volatility` / `returns`（各窗口）、`beta`（多基准）、`red_flag`（红/橙/黄）、`macro_stress`（5 情景）。
- **条件依赖**：`macro_stress` 需管线 `--include-stress` 才有值（默认 False）。
  缺失维度的干预标 `dimension_na`，不虚构。

### 3.3 偏离度量
- **权重偏离**：`delta = new_weight - old_weight`（绝对值 + 方向：调增/调减）。
- **方向一致性**（三分类）：人工调增 vs B 看多信号 → **一致**；调增 vs B 看空 → **背离**；
  其余 → **中性**。B 看多信号判定（任一满足即多）：`returns(20d)>0` 或 `beta>0` 且 `red_flag` 无
  或 `macro_stress` 无显著压力情景。
  **实现口径（2026-08-07 落地）**：「macro_stress 无显著压力」按**不看空**处理而非看多
  （避免无压力资产全部看多、一致率虚高）；看空含 `returns(20d)<0` 与显著压力情景；多空同时
  触发 → `neutral` 不硬判。
- **宏观背景**：干预时 `macro_regime.phase`（early/mid/late）+ 关键 `states` 标注。

### 3.4 干预后表现
干预资产下月收益 vs 全资产月均收益（用 B 行情或 `actual_trade_ledger`），报告
"干预有效（跑赢基准）/ 无效（跑输）"。样本不足时 `confidence="low"`，不虚构结论。

### 3.5 趋势统计
月度干预次数、涉及策略/资产分布、净偏离方向、`reason` 分布。

## 4. 输出格式（Markdown 月报）

产出到 B 本地 `reports/human_machine_compare/{YYYY-MM}_hmc.md`（独立 CLI，仿
`scripts/run_param_sweep.py` 模式，只写 B `reports/`，不进每日决策包、不写共享目录）。

```markdown
# 人机对比月报 {YYYY-MM}

## 摘要
本月干预 {N} 次 / 涉及策略 {M} 个 / 净调增方向 / 方向一致率 {K}%

## 干预明细表
| 时间 | strategy_id | asset_id | old→new | delta | reason | 对应B维度(vol/ret/beta/red_flag/macro_stress) |

## 方向一致性评估
一致 {a} / 背离 {b} / 中性 {c} ｜ 背离明细表

## 干预后表现
干预资产下月收益 vs 基准 ｜ 有效/无效计数

## 宏观背景
当月 macro_regime.phase + 关键 states
```

**机器输出规则**：报告内只出现 `strategy_id` / `asset_id`（全 ASCII）；中文仅限
`reason` 原文引用与月报标题（reports 属 B 本地，允许中文标题）。策略名（三剑客/网格等）
**永不硬编码**。

## 5. 依赖清单与缺口

| 依赖 | 状态 | 说明 |
|---|---|---|
| `human_override_log` ≥3 个月积累 | 🔴 当前 4 行 | 硬依赖，2026-11 后评估启动 |
| `decision_ref_package.json` 每日产出 | ✅ 已通 | `--real` 联调通过 |
| `--include-stress` | ⚠️ 条件 | 需显式带参才产出 `macro_stress`；B 每次 `--real` 已带 |
| `actual_trade_ledger` | ✅ 640 行 | `decision_source` 全 manual（当前无机器交易，对比暂以人工为主） |

**数据缺口（A 侧补字段项，列入监控清单）**：
- `human_override_log` 缺 `operator`（操作人）/ `order_id` / `status`（生效状态）/ `scene`（场景分类）。
  如需按操作人 / 订单粒度对比，需 A 侧补充；当前粒度（权重覆盖场景）已足够做月度方向对比。
- `manual_override.csv` 有结构无数据（审批流设计未启用），暂不纳入。

**口径约束（实现时注意）**：
- B 维度资产级 vs A 干预策略×资产级：先 `asset_id` join 再 `strategy_id` 分组。
- `macro_stress` 默认行为：管线默认 `include_stress=False`，对比实现须明确读取路径
  （读决策包该字段，缺失标 `dimension_na`）。
- 现金调整行（`SOFTWARE_CASH_ADJUST`）在 `actual_trade_ledger` 中需过滤，只算真实交易。

## 6. 监控清单（等待期内主动跟踪）

```
## 监控清单（2026-08-07 起）

- [ ] 任务1：A 侧 read_with_audit 生产接线完成日期
      （B 下次 --real 写入时 A 是否自动消费并写入回执）
- [ ] 任务1：连续 ≥3 次 --real 均有 SUCCESS 回执（管道持续畅通确认）
- [ ] 任务3：A 审核工作台设计反馈的字段需求记录
      （如 phase 置信度明细 / red_flag 触发历史 / macro_stress 补充情景）
- [ ] 任务3：human_override_log 新增字段（operator 等）时更新本设计口径
- [ ] 依赖：human_override_log 行数达到 ≥3 个月阈值（约 2026-11），评估立项
```

## 附：A 侧数据源现状（2026-08-07 核实）

| 文件 | 存在 | 行数 | 关键字段 |
|---|---|---|---|
| `data/logs/human_override_log.csv` | ✅ | 5（4 数据） | `time, strategy_id, asset_id, old_weight, new_weight, reason` |
| `config/manual_override.csv` | ✅ | 2（无数据） | `date, asset_id, override_action, override_weight, reason, approved_by, expires_on` |
| `config/actual_trade_ledger.csv` | ✅ | 640 | 22 列（含 side/qty/amount/decision_source/confirm_status） |
| `systemA_feedback/consumed_20260807.json` | ✅ | — | `consumed_run, status:SUCCESS, reason, consumed_at` |
