# 策略规则契约（strategy_contract）约定

> 文档日期：2026-08-07 ｜ 状态：**约定草案（待 A 产出契约后审阅确认）**，B 侧当前不编码
> 本文档是 B 侧对 A 侧策略规则契约的读端期望基线，供 A 产出契约草案时两系统共同审阅确认。

## 1. 背景与归属（关键决策变更）

策略级别回测归属项目B（2026-08-07 用户裁定）：

| 职责 | 归属 |
|---|---|
| 策略规则定义 | 项目A |
| 策略规则契约导出 | 项目A |
| 策略级别回测引擎 | **项目B** |
| 回测报告输出 | **项目B**（`reports/backtest/`） |
| 参数扫描扩展 | **项目B**（扩展 param_sweep） |

**单向数据流**：A 定义策略规则并导出契约（`strategy_contracts/`）→ B 只读契约，用 B 的历史
行情/宏观数据跑回测/参数扫描/因子回溯 → 输出报告到 B 本地 `reports/`。B **绝不写共享目录
的 `strategy_contracts/`**、绝不写 A。

**边界澄清**：读 `strategy_contracts/` 是 B 合法读取（A 主动提供给 B 的策略定义输入，方向
A→B），与「严禁读 `systemA_feedback/`」不冲突——feedback 是 A 对 B 输出的反馈（避免逻辑循环），
契约是 A 给 B 的策略定义。

## 2. 读取路径约定

- 契约目录：`D:\FF Project\data\integration\strategy_contracts\`
- 契约文件：`strategy_contract.json`（单文件，含全部策略的契约；或按策略拆分多文件待定）
- B 侧读取：**只读**该目录，绝不写入、绝不修改

## 3. 字段约定草案（待两系统共同确认）

A 侧定义契约格式时建议采用以下字段命名与类型（B 侧读端视角）：

| 字段 | 类型 | 说明 | B 侧读端语义 |
|---|---|---|---|
| `strategy_id` | string | 策略唯一标识（全 ASCII） | join A `human_override_log` / `actual_trade_ledger` 的分组键 |
| `decision_rule` | string | 策略路由规则（barbell/mid_line/grid/short_term） | 决定回测引擎采用的规则分支 |
| `target_weights` | dict[asset_id, float] | 策略内目标权重 | asset_id 用 B 资产代码（如 512890.SH），权重合计 1.0 |
| `pref_*` | dict | 5 维偏好参数（momentum/valuation/volatility/reversal/macro_fit） | 回测信号打分权重，各维度缺省/空 dict 需明确 |
| `rebalance_frequency` | string | daily/weekly/monthly/quarterly/never | 回测调仓频率；never 表示一次性建仓 |
| `min_weight` / `max_weight` | dict[asset_id, float] | 权重约束范围 | 回测权重 clamp 区间 |
| `signal_filters` | list | 信号触发条件（如 `phase≠late`） | 与 B 决策包 `macro_regime.phase` 等维度对齐 |

**约定原则**：
- 所有标识符（strategy_id / asset_id / decision_rule）全 ASCII，零中文策略名。
- `target_weights` / `min_weight` / `max_weight` 的键用 B 资产代码，B 侧直接 join 本地行情
  `fund_daily.csv`（ts_code 匹配）。
- `signal_filters` 引用的 B 维度字段（`macro_regime.phase` / `asset.red_flag` / `macro_stress`）
  与决策包 schema v1.0 对齐；引用未知字段时 B 侧标注 `filter_unknown`，不硬判。
- 缺失字段降级：缺 `pref_*` 用中性权重（各 0.2）；缺 `rebalance_frequency` 默认 `monthly`；
  缺权重约束视为无约束（0~1）。降级规则待确认后固化，**不虚构契约不存在的约束**。

## 4. 审阅流程

1. A 侧定义契约格式，将**草案发到共享目录**供 B 审阅。
2. B 侧按本文档字段基线逐项核对（命名/类型/语义），确认口径一致。
3. 双方确认后，A 再实现导出逻辑；B 侧再开发读取与回测引擎。

当前状态：**等待 A 产出契约草案**，B 侧不编码。

## 5. 条件触发任务清单（当前就绪等待）

| # | 任务 | 触发条件 | 说明 |
|---|---|---|---|
| 1 | 读取策略规则契约 | A 侧 `strategy_contract.json` 写入共享目录后 | 从 `strategy_contracts/` 读取 A 策略规则定义 |
| 2 | 策略级别回测引擎开发 | 契约格式已定，且至少一个策略契约可读 | 基于契约 + B 行情/宏观跑回测，输出 `reports/backtest/` |
| 3 | 参数扫描扩展 | A 策略详情制订完成，提供需扫描的参数范围 | 将 param_sweep 扩展为所有策略参数的扫描框架 |
| 4 | 因子有效性回溯测试 | B 数据包连续运行 ≥1 个月，且 A 策略详情制订完成 | 对当前因子做 IC/IR 回溯测试 |
| 5 | human_machine_compare 真实月报 | `human_override_log` ≥30 条且覆盖 ≥3 策略（当前 4 条/1 策略） | 运行 `scripts/run_human_machine_compare.py --package <最新决策包>` |

**当前就绪状态**：任务 5 已具备运行能力（分析引擎就绪，`0f0a7f8`）；任务 1-4 均等待 A 侧
产出（契约/策略详情/数据包连续运行），B 侧不编码。

## 6. 纪律（对契约任务同样适用）

- B 只写 `reports/`（回测/扫描/月报），只读 `strategy_contracts/` + A `config/` + `data/logs/` 审计日志。
- 严禁读取/修改 `systemA_feedback/`（M-003）。
- 每次 `--real` 运行带 `--include-stress`（`2c10c0b` 已生效）。
- 机器产出全 ASCII，`strategy_id`/`asset_id` 标识，零中文策略名。

## 7. A 侧 PR #3 契约 v1.0 审阅记录（2026-08-07）

**审阅对象**：A PR #3（`feature/strategy-contract`）→ `data/integration/strategy_contracts/strategy_contract.json`
schema v1.0（`src/strategy_contract.py` + `docs/integration/strategy_contract_设计_20260807.md`）。

**结论：契约 schema 可用（可合并）**，核心字段满足 B 侧回测引擎输入需求；附 3 条非阻塞建议。

### 7.1 审阅清单逐项核对

| 检查项 | B 侧基线要求 | A 实际 v1.0 | 判定 |
|---|---|---|---|
| decision_rule 枚举 | barbell/mid_line/grid/short_term | 5 枚举：mid_line/short_term/grid/barbell/**defensive** | ✅ 通过（超集） |
| rebalance_frequency 枚举 | daily/weekly/monthly/quarterly/never | 仅 **weekly/daily** | ⚠️ 值域缩窄，需确认 |
| signal_filters 结构 | `{field, operator, value}` | 预留 `null`（结构未定义） | ⚠️ 当前无需求，未来 1.1 约定 |
| min_weight/max_weight 映射 | dict[asset_id, float] | 内嵌 `assets[].min_weight/target_weight/max_weight` + `target_weight_configured` | ✅ 通过（结构不同但更优） |
| 类型匹配 | 与回测引擎预期一致 | preferences 解析后 dict / enabled bool / backtest 成本 / 带时区 ISO | ✅ 通过 |

### 7.2 A 实际 schema（与基线草案差异）

- `target_weights` dict → 改为 `assets[].target_weight` + `target_weight_configured`（`false`=系统计算目标）。
- `pref_*` 前缀 → `preferences`（解析后 dict，5 键可负，如 volatility=-0.3）。
- 顶层含 `contract_type`/`generated_at`(带 +08:00 时区)/`generated_by`；`shared_config` 含
  adj_type/backtest/signal_thresholds/risk_thresholds/short_term_score_params/position_limits。
- B 侧回测引擎读取方式：`strategies[]` 按 strategy_id 遍历 → `assets[]` 按 asset_id 遍历，
  **不再用 dict 映射**；`enabled=false` 的策略/标的需过滤。

### 7.3 非阻塞建议（回测引擎开发前需确认，不阻塞 PR 合并）

1. **`rebalance_frequency` 值域**：A 仅导出 weekly/daily，缺 monthly/quarterly/never。请 A 确认未来
   是否会出现这些值；B 侧引擎将容忍未知值并按 `use_target_ratio` + `rebalance_threshold_abs`
   （策略级）/ `asset_rebalance_threshold_abs`（标的级）触发再平衡。
2. **`target_weight_configured=false` 推导口径**：A 未定义"系统计算目标"的推导规则，B 侧无法独立复现。
   当前阶段建议：B 侧对这些标的按 `target_weight=null` 处理（不虚构权重），或 A 明确推导口径后固化。
3. **`signal_filters` 结构**：当前 null。未来 schema 1.1 填充时建议按 B 侧基线
   `{"field": "<B维度>", "operator": "<eq/neq/gt/lt>", "value": "<值>"}` 结构约定
   （如 `{"field": "macro_regime.phase", "operator": "neq", "value": "late"}`），避免再次对齐成本。

### 7.4 触发条件更新

任务 1「读取策略规则契约」与任务 2「策略级别回测引擎开发」的**触发条件已满足**（A 契约 v1.0 已产出），
B 侧可进入引擎开发；开发启动仍需人工裁定（当前不编码）。
