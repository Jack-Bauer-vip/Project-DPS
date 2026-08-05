# 后续任务

## P0：恢复全球宏观数据

1. 为 `fetch_global_macro_data.py` 增加 FRED 本地 CSV 导入。
2. 支持 `DGS10.csv`、`DGS2.csv`、`DGS30.csv`、`DFII10.csv`。
3. 对没有 `available_at` 的手动文件降低质量等级并记录警告。
4. 恢复或导入完整 FRED 历史数据。
5. 运行 `notebooks/global_macro_lab.ipynb`。
6. 人工审阅 SPY、TLT、GLD 条件收益表。

## P1：人工确认宏观规则（已完成）

1. ✅ 为当前宏观状态创建规则 `DRAFT`（12 条，覆盖全部有数据的资产×状态）。
2. ✅ 记录样本起止日期、样本数、置信度（五档离散 modifier + 样本联动）。
3. ✅ 样本 ≥60 且非中性的 5 条已人工确认 `APPROVED`；样本 <60 的保持 `DRAFT/CANDIDATE`。
4. ✅ 启用 SPY/TLT/GLD 研究配置（enabled=1, ENABLED）。
5. ✅ 运行 GlobalEtfEngine 生成真实数据评分（`data/global_etf_values/`）。
6. ✅ 检查 DGS30/DGS10 代理（rate_proxy=DGS30，无降级警告）、支持/冲突因子与数据质量。

### 待办（样本积累后）

- 利率状态（rate_up/rate_down）样本约 2027 年前后达到 60 个月，届时人工复核后再 APPROVED。
- 当前状态三元组因缺 rate_up/curve_normal 的 APPROVED 规则而返回 `PARTIAL`，属预期安全行为。

## P2：桌面端全球 ETF 页面（已完成）

新增 `GlobalEtfPage`（desktop/global_etf_page.py），展示：

- ✅ 数据状态和数据截至日期（各序列截至日 + 质量等级）；
- ✅ 当前宏观状态（宏观状态枚举 + 引擎状态）；
- ✅ DGS30/DGS10 代理及精度提示（利率代理 + 降级警告）；
- ✅ SPY/TLT/GLD 基础评分和宏观修正（评分表格）；
- ✅ 支持因子与冲突因子（评分表格列）；
- ✅ 条件收益表（Notebook 产出 CSV）；
- ✅ 研究规则状态（DRAFT/APPROVED 明细）；
- ✅ 重新计算（后台线程 worker）与查看 Notebook 结果。

页面接线进主窗口导航（"全球ETF宏观"），`data_root` 默认取 `store_root.parent/data`。

## P3：研究资产和交易资产映射

### 数据模型层（已完成）

- 新增 `global_etf_trade_mapping` 表：研究资产↔交易资产 1:N 映射，结构化字段含汇率（fx_pair/fx_rule/exchange_rate）、管理费（management_fee）、交易成本（trading_cost_bps）、跟踪误差（tracking_error）、折溢价（premium_discount）、交易时区/时段/休市风险（market_timezone/trading_hours/holiday_risk）、优先级（priority）、状态（ACTIVE/INACTIVE）。
- 存储方法：`upsert_global_etf_trade_mapping`（含存在性与取值范围校验）、`list_global_etf_trade_mappings`（多条件过滤 + priority 排序）、`get_global_etf_trade_mappings`（默认 ACTIVE）。
- 消费侧辅助（`core/global_etf_trade_mapping.py`）：`effective_trade_mapping`（ACTIVE + min priority）、`initialize_default_global_etf_trade_mappings`（可选 self_mapping，默认不写行）。
- `global_etf_definition.trade_asset_code` 弃用，映射以新表为准。
- GlobalEtfEngine 保持纯净，不携带映射；未配置 = 返回空（派生状态）。

### 桌面端映射配置 UI（已完成）

- `GlobalEtfPage` 新增"交易资产映射"区：按研究资产选择、映射表格展示、表单编辑。
- 表单字段：交易资产代码/名称、币种、汇率方式/汇率、管理费、交易成本、跟踪误差、折溢价、优先级。
- 支持保存（upsert）、停用（INACTIVE）、删除；表格提示当前生效映射（ACTIVE + min priority）。
- `storage.py` 新增 `delete_global_etf_trade_mapping`。

### 交易资产换算逻辑（P4，已完成）

- ✅ 新增 `core/global_etf_trade_conversion.py`：把研究评分换算到交易口径。
  - 管理费采用相对费用差异：只扣交易资产比研究资产多出的年化管理费（SPY 0.09% / TLT 0.15% / GLD 0.40%），避免重复计算研究资产自身成本；
  - 交易成本按 `trading_cost_bps/10000` 一次性折减；费用+成本合计超过 50% 时按比例压缩到上限并记警告；
  - 汇率：A 股 QDII 净值已含汇率、收益已是人民币口径，不额外调整收益；静态汇率无时间变动信息，不假设汇率变动，仅生成 `fx_note` 展示水平与敞口提示（保守，符合基线决策）；
  - 跟踪误差与折溢价仅透传展示，不进入评分计算。
- ✅ 桌面端新增"交易口径换算"独立表格（映射配置区上方）：对最近一次评分的每行取生效映射换算，独立展示，不改动研究口径 `final_score`。
- ✅ 新增 `tests/test_global_etf_trade_conversion.py`（19 项），全量测试 137 项通过。

## P5：宏观规则版本比较与规则审核（已完成）

- ✅ 新增 `global_etf_macro_rule_history` 独立历史表（append-only 版本链）：每次 upsert（create/update）与审核动作都在历史表落快照，可追溯"为什么 modifier 从 1.10 改成 1.05"。
- ✅ 审核状态机（storage.py）：`approve`（DRAFT→APPROVED）、`reject`（DRAFT→REJECTED）、`revoke`（APPROVED→REJECTED）、`reset`（REJECTED→DRAFT 重新审核）。新增 REJECTED 状态与 `rejected_by/rejected_at/reason` 三列（含旧库迁移）；驳回/撤销保留审计记录而非删除；REJECTED 天然被引擎忽略（`get_global_etf_macro_rules` 硬编码 APPROVED）。
- ✅ 桌面端"宏观规则状态"区升级：状态筛选（全部/DRAFT/APPROVED/REJECTED）+ 确认/驳回/撤销/重新提交/历史按钮，按钮按选中行状态启停，QInputDialog 收集审核意见，"历史"弹对话框展示版本链（动作/状态/modifier/样本/置信度/意见/时间）。
- ✅ `scripts/create_global_macro_rules.py` 的 `approve_eligible_rules` 改走专用 `approve_global_etf_macro_rule`，保证历史表中 action=approve。
- ✅ 新增 `tests/test_global_etf_rule_review.py`（15 项）+ 桌面端规则审核测试（6 项），全量测试 158 项通过。
- 说明：既有 12 条规则的历史链为空（历史机制从功能上线后开始记录），新变更即产生版本。

### 待办（下一项）

- 暂无剩余 P4/P5 待办；剩余长期项为利率状态（rate_up/rate_down）样本积累到 60 后人工复核（约 2027 年）。

仍不得自动生成交易指令或自动修改组合权重。
