# 已确认设计决策

这些决策是当前项目基线。后续 AI 如需修改，必须先补充测试和说明理由。

## 数据和版本

- 本地数据优先复用，更新时生成新的数据快照。
- 原始数据、来源事实和历史研究版本不可覆盖。
- 研究项目是组织单位，研究运行是版本单位。
- SQLite 保存配置和元数据，Parquet 保存因子值，文件系统保存报告和图表。
- API Key、Token 和密码只通过环境变量读取。

## A 股因子

- 生产评分器只支持 `exclude` 和 `neutral` 缺失处理。
- `industry_median`、`missing_indicator` 在生产端降级为排除并记录警告。
- 因子值必须经过离线研究确认，不把技术指标自动视为有效因子。
- 不增加 KDJ。
- 因子评分不自动产生交易指令。

## 全球 ETF 宏观

- TLT 优先使用 DGS30，缺失时回退 DGS10，并标记精度降低。
- TLT 使用日频收益和利率变化；SPY、GLD 使用月频研究。
- 样本少于 24 个月的宏观状态只能作为参考。
- 宏观修正系数必须由人工确认。
- 没有有效 `APPROVED` 规则时，`macro_modifier` 和 `final_score` 必须为 `null`。
- 研究资产和交易资产分开，不首期强行建立等价替代品。
- FRED 数据缺失时返回 `PARTIAL`，不把缺失当作中性。

## P1 规则决策（2026-08-04 记录）

- modifier 用五档离散规则：偏差（条件收益−全期基准月均收益，百分点）≥+1.5→1.15、≥+0.5→1.08、>−0.5 且 <+0.5→1.00、≤−0.5→0.92、≤−1.5→0.85。
- 样本联动：<24 个月仅作参考（REFERENCE_ONLY，modifier 不生效）；24–59 为候选（CANDIDATE）；≥60 才允许 APPROVED。
- 落库状态最小集合：一律先 DRAFT，APPROVED 是唯一人工升级动作；suggest 候选等级不写入 DB status 列。
- 无条件收益数据的状态（rate_stable/curve_normal/curve_inverted/real_yield_stable）保守中性 1.00，**不落库**，避免掩盖缺失证据。
- 利率状态样本 52<60 时不破例 APPROVED，保持 DRAFT，等样本积累到 60（约 2027 年）再人工复核。
- 当前状态三元组缺 rate_up/curve_normal 的 APPROVED 规则时引擎返回 `PARTIAL`（base_score 可算，宏观修正为空），属预期安全行为，不是失败。

## 数据窗口决策（2026-08-04 记录）

- SPY/TLT/GLD 数据窗口统一保持 **2003 起**，不因 rate_up 样本不足而单独扩 SPY 到 1993。
- 理由：① 即使 SPY/rate_up 扩到样本 69 并 APPROVED，当前宏观状态含 curve_normal（无数据、永不落库），引擎仍 PARTIAL、final_score 仍为空；② SPY/rate_up 现窗口 modifier=1.00（NEUTRAL），APPROVED 也不改变评分；③ 单独扩 SPY 破坏三者统一窗口，TLT/GLD 受上市日硬限制（2002/2004）无法同步扩。
- 引擎在当前宏观状态返回 PARTIAL（base_score 可算，final_score 为空）为常态，属安全设计，不自动改变。
- rate_up 需等样本自然积累到 60（约 2027 年）再人工复核；届时若宏观状态不含无数据分量，评分可能 COMPLETED。

## 规则审核与版本决策（2026-08-05 记录）

- 规则版本用**独立历史表**（`global_etf_macro_rule_history`，append-only）：每次 upsert（create/update）与审核动作都落变更后快照，主表保留当前值，不破坏现有唯一约束。
- 审核状态机补全 REJECTED：确认（DRAFT→APPROVED）、驳回（DRAFT→REJECTED）、撤销（APPROVED→REJECTED）、重新提交（REJECTED→DRAFT）。REJECTED 是人工决策，与 SUSPENDED（止损/数据问题暂停）区分；驳回/撤销保留审计记录而非删除，REJECTED 规则可出现在历史对比中提示"该方向已被否定"。
- 审核动作走专用方法（approve/reject/revoke/reset），不走 upsert，保证历史表中 action 语义准确（approve ≠ update）。
- REJECTED 天然不参与评分：`get_global_etf_macro_rules` 硬编码 `status='APPROVED'`，引擎无需改动。
- 既有规则无历史记录（机制从上线后开始记录）；主表新增列用 PRAGMA+ALTER 迁移，旧库无损。
- 同一 (asset,state) 重复 APPROVED 受部分唯一索引约束，upsert 显式拦截并提示先撤销。

## 安全边界

- 不自动修改正式资产池。
- 不自动调整组合权重。
- 不自动生成交易指令。
- 不覆盖历史报告、数据快照或冻结结论。
- 不在 Git 中提交 `.venv`、SQLite、完整行情、报告、图表、日志和 API 密钥。
