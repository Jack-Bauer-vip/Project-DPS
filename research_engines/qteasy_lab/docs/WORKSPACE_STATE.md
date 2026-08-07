# 工作区状态快照

> 保存于：2026-08-07 ｜ 阶段一（M2）已完成；阶段二（M3）已完成；阶段三（M3）已完成。

## 当前里程碑

- **阶段三（M3）已完成**：压力模拟器（stress_simulator，填充 `macro_stress`）+
  参数网格扫描（param_sweep，独立 CLI）。
- **human_machine_compare 分析引擎已编码**（0f0a7f8，合成数据）：31 测试全绿。
- **全量测试 331 tests OK（skipped=2）**（阶段二 277 + 阶段三新增 23 + hmc 新增 31）。
- **git 状态**：`develop` 分支，全部已提交（最新 0f0a7f8），工作区干净。
- **A 侧审核工作台已上线**（2026-08-07，A PR #2 179bf52）：B 字段已接入展示。

## 阶段一产出物（均已入库）

| 模块 | 文件 |
|---|---|
| B1-1 网格参考表 | `reference/grid_reference.py` + `scripts/build_grid_reference.py` |
| B1-2 宏观对冲效率 | `reference/hedge_efficiency.py` + `scripts/build_macro_hedge_efficiency.py` |
| B1-3 交易指纹 | `reference/trader_fingerprint.py` + `scripts/analyze_trader_fingerprint.py` |
| 管线扩展 | `reference/pipeline.py`（include_grid / include_hedge / output_root dry-run） |
| 端到端入口 | `scripts/run_reference_pipeline.py`（默认 `--dry-run` 写 outputs/） |
| 接口契约 | `scripts/generate_interface_contract.py`（默认 outputs/README.md） |
| 测试 | `tests/test_grid_reference.py` / `test_hedge_efficiency.py` / `test_trader_fingerprint.py` |

## 阶段二产出物（均已入库）

| 模块 | 文件 |
|---|---|
| 宏观持续期 | `reference/duration_phase.py`（phase/phase_basis/phase_confidence/state_durations） |
| 逐资产风控旗 | `reference/red_flag.py`（load_risk_thresholds + assess_red_flags） |
| 版本回滚 | `reference/shared_dir.py`（rollback_to 启用 + `_prune_backups` 保留最近 30 版） |
| 管线集成 | `reference/pipeline.py`（include_duration_phase / include_red_flag / include_stress 占位 / risk_params） |
| 测试 | `tests/test_duration_phase.py` / `test_red_flag.py` / `test_shared_dir.py` / `test_reference_pipeline.py` |

阶段二新字段：`macro_regime.phase / phase_basis / phase_confidence / state_durations` +
`asset.red_flag`（level/triggered_by/approval_required/metrics）。A 读端读完整 JSON 自动带入。

## 阶段三产出物（均已入库）

| 模块 | 文件 |
|---|---|
| 压力模拟器 | `reference/stress_simulator.py`（build_stress_simulator → 5 情景压力损益） |
| 参数网格扫描 | `reference/param_sweep.py`（sweep_spread_grid + round_trip_cost_bps）+ `scripts/run_param_sweep.py` |
| 管线集成 | `reference/pipeline.py`（include_stress 真实现，默认 False 零开销） |
| 情景幅度常量 | `reference/config.py`（STRESS_RATE_UP_BP=0.50 / STRESS_RATE_DOWN_BP=-0.50 / STRESS_REAL_YIELD_UP_BP=0.10 / STRESS_MIN_SAMPLES=5） |
| 测试 | `tests/test_stress_simulator.py` / `test_param_sweep.py` / `test_reference_pipeline.py` |

阶段三新字段：`asset.macro_stress`（`{scenario: {pnl_pct, sample_count, corr, confidence,
basis}}`，5 情景：rate_up_50bp / rate_down_50bp / curve_inverted / real_yield_up /
stagnation（滞胀，rate_up AND real_yield_up 组合））。仅 `--include-stress` 时产出。

## human_machine_compare 编码（2026-08-07 提前，基于合成数据）

| 模块 | 文件 |
|---|---|
| 分析引擎 | `reference/human_machine_compare.py`（parse_human_override_log / b_signal / classify_direction / weight_delta / post_intervention_performance / build_hmc_report / render_hmc_markdown） |
| CLI | `scripts/run_human_machine_compare.py`（`--target-month` / `--human-log` / `--package` / `--no-online`） |
| 合成数据 | `tests/fixtures/human_override_log_synthetic.csv`（72 行，3 策略 × 4 资产 × 4 月）+ `_generate_fixtures.py` |
| 测试 | `tests/test_human_machine_compare.py`（31 个：解析/B信号/方向分类/干预后收益/全链路/无中文策略名） |

- 真实数据联调通过：真实 human_log（4 行）+ 最新决策包 → 报告骨架正确（一致 2 / 背离 2）。
- 口径调整：「macro_stress 无显著压力」= 不看空（非看多）；信号矛盾 → neutral，不硬判。
- reports 产物 `reports/human_machine_compare/{YYYY-MM}_hmc.md` 不入库（gitignore）。

## 冒烟结果（2026-08-07）

1. `run_reference_pipeline.py --dry-run --include-stress`：四件套落盘 `outputs/`，warnings=0；
   14 只资产全部带 `macro_stress`。真实数据 `rate_up_50bp` 样本 3 / `rate_down_50bp` 样本 2
   （<5）→ 正确降级 None + confidence=low（不虚构压力损益）；`curve_inverted` 25 样本、
   `real_yield_up` 25~28 样本、`stagnation` 15~16 样本 → high。全 ASCII 零中文策略名。
2. `scripts/run_param_sweep.py --no-online`：`reports/param_sweep/param_sweep_20260807.csv`
   + `.md` 落盘；14 资产 × 6 乘子 = 84 行，0 缺失；间距越小触发越多、成本越高（单调），
   CSV 全 ASCII，中文名只进 Markdown 摘要。
3. **回归**：`include_stress=False`（默认）决策包 `macro_stress` 保持空 dict，零开销。

## 关键现状

- 共享目录 `D:\FF Project\data\integration\` **已建并通过联调**（2026-08-07）：B `--real`
  触发 `ensure_root()` 兜底建根；A 读端消费 `systemB_ref/20260807/` 并回执 SUCCESS。
- A 侧 `asset_pool.csv` active 14 只，全部本地行情覆盖（含 164824.SZ）。
- A 侧 `strategy_params.json` 存在，`risk_thresholds` 已实际用于 dry-run 风控旗判定。
- **A 侧审核工作台已上线**（2026-08-07，179bf52）：macro_regime.phase / asset.red_flag /
  macro_stress 完整接入展示；红牌拦截验证通过（red 红牌弹窗拦截 + 人工确认）。
- param_sweep 为 B 侧**离线研究工具**：只写 B 本地 `reports/param_sweep/`，不进每日决策包、
  不写共享目录、A 侧无对应消费端（如需 A 消费可另约输出位置）。

## 硬约束（不可违反）

- 单向数据流：B → 共享目录 → A；B1-3 只读 A config/ 文件，绝不写 A、绝不读 `systemA_feedback/`。
- 机器输出零中文策略名（只用 strategy_id）；`approval_policy="REFERENCE_ONLY"` 永不自动 APPROVED。
- 非 dry-run 写入前显式调 `IntegrationDir.ensure_root()`（创建根目录 + WARNING，不崩溃）。
- 全量测试命令：`cd research_engines/qteasy_lab && .venv/Scripts/python.exe -B -m unittest discover -s "D:\Project DPS\tests" -q`

## 下一步（两项目协同节奏）

| 顺序 | 任务 | 负责方 | 触发条件 |
|---|---|---|---|
| 1 | 收集真实干预数据 | A | 日常使用审核工作台，自然累积 `human_override_log` |
| 2 | 运行 human_machine_compare 真实月报 | B | `human_override_log` **≥30 条且覆盖 ≥3 策略**（当前 4 条，预计 1-2 个月） |
| 3 | 策略详情制订 | A | 审核工作台运行稳定 + 策略可行性分析启动后 |
| 4 | 策略参数扫描标准化 | B | A 侧策略详情制订完成，提供需扫描的参数范围 |
| 5 | 因子有效性回溯测试 | B | B 侧数据包连续运行 ≥1 个月，且 A 侧策略详情制订完成 |

- **唯一阻塞项**：等待 A 侧 `human_override_log` 累积至阈值（≥30 条且覆盖 ≥3 策略）。
- A 侧审核工作台已上线（179bf52），B 字段已接入展示；B 无需介入，仅响应 A 侧字段需求。
