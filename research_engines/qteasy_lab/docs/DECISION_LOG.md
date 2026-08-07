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
- 落库状态集合：先 DRAFT；APPROVED 是唯一人工升级动作；suggest 候选等级不写入 DB status 列。
- 无条件收益数据的**常态状态**（rate_stable/curve_normal/real_yield_stable）以 BASELINE（modifier=1.00）落库并参与评分，避免"无数据状态"永久阻断评分链；curve_inverted 属异常信号不兜底（2026-08-06 修订，见"P2 规则决策"）。
- 利率状态样本 52<60 时可提升为 PROVISIONAL（临时生效，仅供参考），等样本积累到 60（约 2027 年）再人工复核升级 APPROVED。
- 引擎按优先级 APPROVED > PROVISIONAL > BASELINE 取生效规则；某状态完全无规则时返回 `PARTIAL`（base_score 可算，宏观修正为空），仍属安全行为，不把缺失当作中性。

## 数据窗口决策（2026-08-04 记录）

- SPY/TLT/GLD 数据窗口统一保持 **2003 起**，不因 rate_up 样本不足而单独扩 SPY 到 1993。
- 理由：① 即使 SPY/rate_up 扩到样本 69 并 APPROVED，当前宏观状态含 curve_normal（无数据、永不落库），引擎仍 PARTIAL、final_score 仍为空；② SPY/rate_up 现窗口 modifier=1.00（NEUTRAL），APPROVED 也不改变评分；③ 单独扩 SPY 破坏三者统一窗口，TLT/GLD 受上市日硬限制（2002/2004）无法同步扩。
- 引擎在当前宏观状态返回 PARTIAL（base_score 可算，final_score 为空）为常态，属安全设计，不自动改变。
- rate_up 需等样本自然积累到 60（约 2027 年）再人工复核；届时若宏观状态不含无数据分量，评分可能 COMPLETED。

## 规则审核与版本决策（2026-08-05 记录）

- 规则版本用**独立历史表**（`global_etf_macro_rule_history`，append-only）：每次 upsert（create/update）与审核动作都落变更后快照，主表保留当前值，不破坏现有唯一约束。
- 审核状态机补全 REJECTED：确认（DRAFT→APPROVED）、驳回（DRAFT→REJECTED）、撤销（APPROVED→REJECTED）、重新提交（REJECTED→DRAFT）。REJECTED 是人工决策，与 SUSPENDED（止损/数据问题暂停）区分；驳回/撤销保留审计记录而非删除，REJECTED 规则可出现在历史对比中提示"该方向已被否定"。
- 审核动作走专用方法（approve/reject/revoke/reset），不走 upsert，保证历史表中 action 语义准确（approve ≠ update）。
- REJECTED 天然不参与评分：生效规则查询 `get_effective_global_etf_macro_rules` 只取 APPROVED/PROVISIONAL/BASELINE（2026-08-06 起替代原硬编码 APPROVED 的 `get_global_etf_macro_rules`）。
- 既有规则无历史记录（机制从上线后开始记录）；主表新增列用 PRAGMA+ALTER 迁移，旧库无损。
- 同一 (asset,state) 重复 APPROVED 受部分唯一索引约束，upsert 显式拦截并提示先撤销。

## P2 规则决策（2026-08-06 记录）

背景：交易资产换算 `trade_final_score` 为空，根因是当前宏观三元组（rate_up + curve_normal + real_yield_up）中 rate_up 样本 52<60 无 APPROVED、curve_normal 无数据永不落库，引擎 PARTIAL、final_score=None。方案：用 BASELINE/PROVISIONAL 让评分链走通，同时保留"不把缺失当作中性"的安全语义。

- 新增规则状态 **BASELINE**：常态状态（rate_stable/curve_normal/real_yield_stable）无研究数据时以 modifier=1.00 落库并参与评分；confidence='baseline'、样本=0、reason 注明"常态基准"。**curve_inverted（期限结构倒挂）是异常信号不兜底**——数据窗口内未出现倒挂本身就是信息，若未来出现应提醒人工研究而非用 1.00 掩盖。
- 新增规则状态 **PROVISIONAL**：样本 24~59 的 DRAFT 可提升为 PROVISIONAL（临时生效、仅供参考），解决"样本不足需等约 2027 年"期间用户看不到任何结果的问题；待样本积累到 60 后由人工确认升级 APPROVED。
- 生效规则查找优先级 **APPROVED > PROVISIONAL > BASELINE**：`get_effective_global_etf_macro_rules` 对每个宏观状态取优先级最高的一条（同一 (asset,state) 多状态共存时高优先级覆盖低优先级）；某状态完全无规则仍阻断评分（PARTIAL），不把缺失自动当作中性。
- 状态机扩展：`promote`（DRAFT→PROVISIONAL）、`demote`（PROVISIONAL→DRAFT）；`approve`/`reject` 允许 DRAFT 与 PROVISIONAL。历史表新增 promote/demote action，审计可追溯。
- 引擎对非 APPROVED 生效规则加警告：PROVISIONAL 标"使用临时规则（样本 N<60），仅供参考"；BASELINE 标"无研究数据，按常态基准 1.00 处理"。警告透传到交易口径换算表 tooltip，不掩盖缺失证据。
- 批量迁移：`scripts/migrate_rule_status.py` 幂等补齐常态 BASELINE 并批量提升样本 24~59 的 DRAFT 为 PROVISIONAL（--apply 执行，--dry-run 预览）。规则生成脚本 `--include-baseline` / `--promote-provisional` 同步支持。
- 冒烟测试：`tests/test_macro_rule_status.py` 覆盖状态枚举、有效规则优先级、状态机、引擎 COMPLETED 与 PARTIAL 保持语义。

## 系统升级决策（2026-08-06 记录）

背景：用户提出将项目B升级为"动态 Beta 计算器 + 前瞻宏观压力仪表盘 + 通用参考维度供应源"，向系统A单向输出通用数学参考数据。已完成需求澄清与技术方案，产出 `docs/PROJECT_AUDIT.md`（项目B升级项目审核资料，已编译为 PDF）。

- 升级范围先做**基础层 + 阶段一**（`reference/` 包 + B1-1 / B1-2 / B1-3），阶段二 / 三另行立项。
- 跨系统连接方案选**纯文件共享目录**（`D:\FF Project\data\integration\`）：单向数据流、本地 Windows 手动触发、零常驻进程、`.ready` + sha256 + `data_asof` 三重校验、`backup/` 回滚。排除共享 SQLite（违反单向红线）与 REST API（需常驻服务）。
- 共享目录由**系统A一次性新建**；项目B产出"给A侧指令"（接口契约文档，`scripts/generate_interface_contract.py` 生成，用户复制到 A）。
- 资产池**对齐系统A active 资产池**（以 `asset_pool.csv` 为准动态读取，当前 14 只，不硬编码数量）；`164824.SZ` 行情缺失标记 `quality_level=D`，不虚构。
- B1-3 交易指纹**先做静态**离线分析，沟通机制完善前不落地动态连接。
- 机器输出（CSV / parquet / JSON / manifest）**零中文策略名**，只用 `strategy_id` 标识符；所有参考维度 `approval_required=true`、包级 `approval_policy="REFERENCE_ONLY"`，永不自动 APPROVED。
- PDF 生成脚本参数化（`--source` / `--dest` / `--title` / `--footer`），默认行为不变，可复用于任意 Markdown 文档。

## 系统升级评审修订（2026-08-06 记录）

背景：审核方审阅项目B（`docs/PROJECT_AUDIT.md`）与项目A两份审核资料，结论**有条件通过 / 批准执行**。项目B架构获"高度通过"，落实 4 条修订后正式生效。

- **目录创建兜底**：`shared_dir.py` 在根目录 `D:\FF Project\data\integration\` 缺失时主动创建根目录（仅根，不建 A 子目录 `systemA_feedback/`）+ WARNING 日志提示，管线不崩溃。
- **权限矩阵确认**：`systemB_ref/` B 读写、`systemA_feedback/` A 读写、`backup/` B 读写；B 离线指纹分析严禁读取 A 反馈文件（避免逻辑循环）。
- **反向通道裁定**：A 写 `systemA_feedback/consumed_{日期}.json` 消费回执 = 单向数据流确认回执，**不属双向写库、不违反单向红线**；项目A须取消"不写共享目录"限制并实现 `confirm_consumption()`。
- **B1-3 读取对象路径核实**：系统A当前无 `data/logs/human_override_log.csv`（仅 `config/manual_override.csv` 空表）；A 若按第 8 节 A1-3 新增该文件，B 读取路径为 `data/logs/human_override_log.csv`（非 `config/`），已写入审核资料 5.2.3。
- **实施解耦与版本对齐**：B 写端基础层优先；A 读端骨架可同步/稍后，两端经共享目录契约解耦；`data_asof` 新鲜度（2 天）B/A 共用同一 `validate_freshness` 逻辑，防规则漂移。
- 审核资料更新为评审修订版（`PROJECT_AUDIT.md` / `.pdf`，10 页），含 9.3 评审决议六表 + "给项目A的修正指令"（可直接复制转发）。

## 阶段二（M3）决策（2026-08-07 记录）

背景：按 PROJECT_AUDIT 6.2 方案骨架对六模块另行立项。用户裁决阶段二**只做 3 项**，其余推后阶段三。

- **阶段二范围 = 最精简 3 项**：`duration_phase`（宏观持续期，填充 `macro_regime`）+ `red_flag`
  （逐资产红/橙/黄，填充 `asset.red_flag`）+ 版本回滚启用。**明确不做** stress_simulator /
  param_sweep / human_machine_compare（推后阶段三）；pipeline 仅保留 `include_stress=False`
  占位开关（True 只追加 warning，不实现）。
- **red_flag 逐资产评估**：与 A 单资产风险灯形成交叉验证，不重复造轮子；直接填充 schema
  预留字段，**不改 schema**。指标口径镜像 A `indicators.py`（近 60 日高点回撤 + 日频 20 日
  波动率未年化）与 `risk.py:152-156/210-226`（回撤三档优先、类型→vol 阈值），测试锁定边界。
- **red_flag 阈值容错**：`load_risk_thresholds` 读 A `strategy_params.json`；文件/JSON/段落
  缺失整体降级 `(None,[warning])`，单项缺失**仅该缺失项**默认兜底 + warning，其余保留文件值
  （不整体降级、不丢用户配置）。
- **duration_phase 多方向性并存**：9 状态全部算连续月数（`state_durations` 全量输出），scalar
  `phase` 只由方向性状态驱动并按 `_PHASE_PRIORITY` 取第一个，`phase_basis` 标注依据；分档
  `<3 early / <6 mid / >=6 late`；降级链（空表/末行 unavailable/无方向性）→ `phase=None` + 显式
  `reason`，不把缺失当早段。**macro_table 空时不入包任何键**（保住 `macro_regime=={}` 回归）。
- **备份修剪**：`backup/` 按 run_id 字典序（YYYYMMDD==时间序）保留最近 30 版（`MAX_BACKUPS`），
  同步修剪 manifest 记录并修正 `newest_run`；`backup_run` 成功后 + `rollback_to` 成功后各调用一次。
- **回滚联调方式**：篡改 `decision_ref_package.json` 数值字段（**而非删除 `.ready`**），回滚后
  断言数值恢复 + `verify_run ok`，验证文件内容确实还原。
- **阶段二完成后通知 A 侧**：决策包新增 `macro_regime.phase` 与 `asset.red_flag` 字段（A
  `integration_reader.py` 读完整 JSON 自动带入，无需改动；风控预警展示逻辑由 A 审核工作台单独设计）。

## 安全边界

- 不自动修改正式资产池。
- 不自动调整组合权重。
- 不自动生成交易指令。
- 不覆盖历史报告、数据快照或冻结结论。
- 不在 Git 中提交 `.venv`、SQLite、完整行情、报告、图表、日志和 API 密钥。
