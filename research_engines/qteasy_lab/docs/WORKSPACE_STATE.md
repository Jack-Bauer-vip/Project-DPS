# 工作区状态快照

> 保存于：2026-08-07 ｜ 阶段一（M2）已完成；阶段二（M3）已完成并提交。

## 当前里程碑

- **阶段二（M3）已完成并提交**：宏观状态持续期（duration_phase）+ 逐资产红/橙/黄风控旗
  （red_flag）+ 版本回滚启用（rollback_to + backup 修剪）。
- **全量测试 277 tests OK（skipped=2）**（阶段一 243 + 阶段二新增 34）。
- **git 状态**：`develop` 分支，阶段二已提交（feat: 阶段二…）。

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

## 冒烟结果（2026-08-07）

1. `run_reference_pipeline.py --dry-run`：四件套落盘 `outputs/`，warnings=0。
2. **决策包新字段**：`macro_regime.phase=early`（basis=rate_up，conf=low，真实数据 rate_up 刚起 1 月）；
   14 只资产中 8 只带 `red_flag`（red/orange/yellow 各档），全部 `approval_required=true`，
   全 ASCII 零中文策略名。
3. **回滚联调**：真实写 → 篡改 `decision_ref_package.json` 数值字段（非删 .ready）→
   `rollback_to` → 数值恢复、`verify_run ok=True`。
4. 修复 `hedge_efficiency._scenario_row` 对 NaN states 的潜在崩溃（资产月份超出场景表范围时
   `scenario in float` 抛 TypeError），作防御性修复入库。

## 关键现状

- 共享目录 `D:\FF Project\data\integration\` **尚未创建**（A 侧未建）→ 真实写入
  `--real` 待 A 建目录后启用；`run_reference_pipeline --real` 进入前显式 `ensure_root()` 兜底。
- A 侧 `asset_pool.csv` active 14 只，全部本地行情覆盖（含 164824.SZ）。
- A 侧 `strategy_params.json` 存在，`risk_thresholds` 已实际用于 dry-run 风控旗判定。
- 待办：**通知 A 侧**决策包新增 `macro_regime.phase` 与 `asset.red_flag` 字段（供审核工作台设计）。

## 硬约束（不可违反）

- 单向数据流：B → 共享目录 → A；B1-3 只读 A config/ 文件，绝不写 A、绝不读 `systemA_feedback/`。
- 机器输出零中文策略名（只用 strategy_id）；`approval_policy="REFERENCE_ONLY"` 永不自动 APPROVED。
- 非 dry-run 写入前显式调 `IntegrationDir.ensure_root()`（创建根目录 + WARNING，不崩溃）。
- 全量测试命令：`cd research_engines/qteasy_lab && .venv/Scripts/python.exe -B -m unittest discover -s "D:\Project DPS\tests" -q`

## 下一步（阶段三另行立项）

PROJECT_AUDIT 6.2 剩余模块：压力模拟器（pipeline 已留 `include_stress=False` 占位）、
参数网格扫描、人机对比月报（依赖 human_override_log 累积至少 3 个月）。
