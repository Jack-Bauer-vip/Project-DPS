# 工作区状态快照

> 保存于：2026-08-07 ｜ 阶段一（M2）已完成；阶段二（M3）已完成；阶段三（M3）已完成。

## 当前里程碑

- **阶段三（M3）已完成**：压力模拟器（stress_simulator，填充 `macro_stress`）+
  参数网格扫描（param_sweep，独立 CLI）。
- **全量测试 300 tests OK（skipped=2）**（阶段二 277 + 阶段三新增 23）。
- **git 状态**：`develop` 分支，阶段二已提交（0ac463a），阶段三待提交。

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

- 共享目录 `D:\FF Project\data\integration\` **尚未创建**（A 侧未建）→ 真实写入
  `--real` 待 A 建目录后启用；`run_reference_pipeline --real` 进入前显式 `ensure_root()` 兜底。
- A 侧 `asset_pool.csv` active 14 只，全部本地行情覆盖（含 164824.SZ）。
- A 侧 `strategy_params.json` 存在，`risk_thresholds` 已实际用于 dry-run 风控旗判定。
- param_sweep 为 B 侧**离线研究工具**：只写 B 本地 `reports/param_sweep/`，不进每日决策包、
  不写共享目录、A 侧无对应消费端（如需 A 消费可另约输出位置）。
- 待办：**通知 A 侧**决策包新增 `asset.macro_stress` 字段（仅 `--include-stress` 时产出；
  强加息 ≥50bp 月真实样本稀少，多数资产该情景会降级 low）。

## 硬约束（不可违反）

- 单向数据流：B → 共享目录 → A；B1-3 只读 A config/ 文件，绝不写 A、绝不读 `systemA_feedback/`。
- 机器输出零中文策略名（只用 strategy_id）；`approval_policy="REFERENCE_ONLY"` 永不自动 APPROVED。
- 非 dry-run 写入前显式调 `IntegrationDir.ensure_root()`（创建根目录 + WARNING，不崩溃）。
- 全量测试命令：`cd research_engines/qteasy_lab && .venv/Scripts/python.exe -B -m unittest discover -s "D:\Project DPS\tests" -q`

## 下一步

- `human_machine_compare` **真实月报**：触发条件 `human_override_log` 达 **≥30 条且覆盖 ≥3 策略**
  （约 2026-11 后评估）→ 直接运行 CLI 产出，无需再编码（分析引擎已提前落地）。
- 联调已通过（2026-08-07）：A 已消费 `systemB_ref/20260807/` 并回执 SUCCESS；
  等待 A 侧 `read_with_audit()` 生产接线 + 审核工作台设计。
