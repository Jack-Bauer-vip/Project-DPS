# 项目B 当前状态（CURRENT_STATE）

> 更新于：2026-08-07 ｜ 阶段三收尾 + 联调准备

## 当前进度

| 阶段 | 内容 | 状态 |
|------|------|------|
| 基础层（M1） | `reference/` 包 + 滚动 Beta + 共享目录 + 心跳 | ✅ 已完成（提交 4c8d81a） |
| 阶段一（M2） | B1-1 网格参考表 / B1-2 宏观对冲效率 / B1-3 交易指纹 / 端到端管线 / 接口契约 | ✅ 已完成（提交 fe48091） |
| 阶段二（M3） | 宏观持续期 duration_phase + 逐资产 red_flag + 版本回滚启用 | ✅ 已完成（提交 0ac463a） |
| 阶段三（M3） | stress_simulator + param_sweep | ✅ 已完成（提交 d30cd72，验收通过） |
| human_machine_compare（人机对比月报） | 分析引擎 + 报告生成器 | ✅ 已编码（2026-08-07，基于合成数据）；真实月报待 human_override_log 积累 |

**全量测试：331 tests OK（skipped=2）**（阶段二 277 + 阶段三新增 23 + human_machine_compare 新增 31）。

## 阶段三交付总结

### 1. 压力模拟器 `reference/stress_simulator.py`
- **历史回放模式**：不假设模型、不虚拟压力路径——筛历史上真实发生压力情景的月份
  （复用 `scenario_monthly_returns`），算资产在这些月份的真实月均收益（`pnl_pct`）与
  对利率代理的条件相关性（`corr`），填充 `schema.AssetDimensions.macro_stress`
  （字段已定义，阶段三首次有值）。
- **5 情景**：`rate_up_50bp`（DGS30 月绝对差 ≥ +0.50，50bp 强加息）/ `rate_down_50bp`（≤ -0.50）/
  `curve_inverted` / `real_yield_up` / `stagnation`（滞胀 = rate_up **且** real_yield_up 组合）。
- **口径**：压力幅度阈值（±50bp）是利率**绝对变化**，用 `diff()`（`_macro_abs_change`），
  与 `_macro_state` rate_up 判定一致；rate_up 用 `>=`、rate_down 用 `<=`。
- **降级链**：空行情/空宏观表 → 不产出；样本 <5（`STRESS_MIN_SAMPLES`）→ 数值 None +
  `confidence="low"`，不虚构压力损益（与 hedge_efficiency 对齐）。
- **冒烟**：14 资产全部产出 `macro_stress`；真实数据强加息 ≥50bp 月样本仅 2~3（<5）→
  正确降级；`curve_inverted` 25 样本 / `real_yield_up` 25~28 / `stagnation` 15~16 → high。

### 2. 参数网格扫描 `reference/param_sweep.py` + `scripts/run_param_sweep.py`
- **独立 CLI**（离线研究工具）：不进每日决策包、A 侧无对应消费端；只写 B 本地
  `reports/param_sweep/`。
- 网格间距 = 基准 × multipliers，基准缺省从 60 日年化波动率推导（复用 `suggest_reference_spread`）。
- 成本模型独立镜像 `TransactionCostConfig`（`2×(commission+half_spread)+stamp_tax+impact`，
  ETF 免印花税），不反向 import pretrade。
- **冒烟**：14 资产 × 6 乘子 = 84 行，0 缺失；间距越小触发越多、成本越高（单调）；CSV 全 ASCII。

### 3. pipeline 集成
- `include_stress` 从阶段二占位（True 仅 warning）改为真实现，默认 False 零开销
  （`macro_stress` 保持空 dict）；计算失败降级 warning 不崩溃。
- `run_reference_pipeline.py` 新增 `--include-stress`。

### 纪律核对
- 新输出全 ASCII，零中文策略名；`approval_policy="REFERENCE_ONLY"` 由决策包承载；
  param_sweep 只写 B `reports/`，不写共享目录、不写 A。

## 联调结果（M3，2026-08-07 已通过）

- 共享目录 `D:\FF Project\data\integration\` 由 B 侧 `--real` 触发 `ensure_root()` 兜底创建。
- A 读端完整消费 `systemB_ref/20260807/`，`consumed_20260807.json` SUCCESS 回执已写入
  `systemA_feedback/`（B 只写不读）。
- 联调发现并修复：`run_reference_pipeline.py` `--real` 分支漏传 `include_stress`（提交 `2c10c0b`），
  重跑后 14/14 资产 macro_stress 全覆盖。
- 真实写入产物：`systemB_ref/20260807/` 含 `.ready` + `package.json` + 4 数据文件 +
  根级 b_heartbeat.json + manifest.json + backup/；verify.ok=True。
- **A 侧审核工作台已上线**（2026-08-07，A PR #2 合并 179bf52）：B 字段 macro_regime.phase /
  asset.red_flag / macro_stress 完整接入展示；红牌拦截验证通过（red 红牌弹窗拦截 + 人工确认）。

## 纪律重申（不可违反）

- **B 只写** `systemB_ref/` + `b_heartbeat.json` + `manifest.json`。
- **B 只读** A 的 `config/` 配置文件（仅 trader_fingerprint 模块离线分析）。
- **B 严禁**读取 `systemA_feedback/` 内容作分析依据。
- **B 严禁**修改 `systemA_feedback/` 中的任何文件。
- 机器产出全 ASCII，零中文策略名；`approval_policy="REFERENCE_ONLY"` 永不自动 APPROVED。

## human_machine_compare 编码（2026-08-07 提前，基于合成数据）

设计文档（`docs/human_machine_compare_design.md`）的前期设计已按用户裁定提前落地为代码：

| 产出物 | 文件 | 说明 |
|---|---|---|
| 分析引擎 | `reference/human_machine_compare.py` | 解析 / B 信号三态 / 方向一致性三分类 / 偏离度量 / 干预后收益 / 聚合 / Markdown 渲染 |
| CLI | `scripts/run_human_machine_compare.py` | 只写 `reports/human_machine_compare/{YYYY-MM}_hmc.md`，离线不写共享目录 |
| 合成数据 | `tests/fixtures/human_override_log_synthetic.csv`（72 行） | 3 策略 × 4 资产 × 4 月，覆盖 agree/diverge/neutral |
| 测试 | `tests/test_human_machine_compare.py`（31 个） | 全链路 + 无中文策略名断言 |

- **真实数据联调已验证**（2026-08-07）：真实 `human_override_log.csv`（4 行）+ 最新决策包跑通，
  一致 2 / 背离 2 / 中性 0，报告骨架正确；干预后表现正确降级（8 月干预次月 9 月行情未发生）。
- **全量测试 331 tests OK（skipped=2）**（300 + 新增 31）。
- 口径调整（相对设计文档 §3.3）：「macro_stress 无显著压力」是**不看空**而非看多，避免无压力资产
  全部看多、一致率虚高；信号矛盾（多空同时触发）→ neutral 不硬判。

## 下一步（两项目协同节奏）

| 顺序 | 任务 | 负责方 | 触发条件 |
|---|---|---|---|
| 1 | 收集真实干预数据 | A | 日常使用审核工作台，自然累积 `human_override_log` |
| 2 | 运行 human_machine_compare 真实月报 | B | `human_override_log` **≥30 条且覆盖 ≥3 策略**（当前 4 条，预计 1-2 个月） |
| 3 | 策略详情制订 | A | 审核工作台运行稳定 + 策略可行性分析启动后 |
| 4 | 策略参数扫描标准化 | B | A 侧策略详情制订完成，提供需扫描的参数范围 |

- B 侧 human_machine_compare 分析引擎已就绪（`reference/human_machine_compare.py` + CLI），
  真实月报可随时触发：`scripts/run_human_machine_compare.py --target-month YYYY-MM --package <决策包>`。
- **唯一阻塞项**：等待 A 侧 `human_override_log` 累积至阈值（≥30 条且覆盖 ≥3 策略）。
- A 侧审核工作台已上线（179bf52），B 字段已接入展示；B 无需介入，仅响应 A 侧字段需求。
