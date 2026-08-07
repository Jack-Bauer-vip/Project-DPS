# 工作区状态快照

> 保存于：2026-08-07 ｜ 阶段一（M2）已完成并提交。

## 当前里程碑

- **阶段一（M2）已完成并提交**：B1-1 网格参考表 / B1-2 宏观对冲效率 / B1-3 交易指纹 /
  端到端管线 / 接口契约。
- **全量测试 243 tests OK（skipped=2）**。
- **git 状态**：`develop` 分支，阶段一已提交（feat: 阶段一…）。

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

## 冒烟结果（2026-08-07）

1. `run_reference_pipeline.py --dry-run`：四件套落盘 `outputs/`
   （grid_reference_table.csv / macro_hedge_efficiency.parquet / decision_ref_package.json /
   assets_metadata.csv），元数据头正确，warnings=0，心跳 `b_heartbeat.json` 已写。
2. 真实 A 流水指纹：`reports/trader_fingerprint/2026-08-07_fingerprint.md`
   （640 行、6 策略、报告全 ASCII 零中文；human_log_rows=2 表明 A 侧 A1-3 已建）。
3. 接口契约：`outputs/README.md` 生成，与 PROJECT_AUDIT 5.4 六条一致。

## 关键现状

- 共享目录 `D:\FF Project\data\integration\` **尚未创建**（A 侧未建）→ 真实写入
  `--real` 待 A 建目录后启用；`run_reference_pipeline --real` 进入前显式 `ensure_root()` 兜底。
- A 侧 `asset_pool.csv` active 14 只，全部本地行情覆盖（含 164824.SZ）。
- A 侧 `human_override_log.csv` 已存在（A1-3 落地，2 行）；`manual_override.csv` 仍空表。

## 硬约束（不可违反）

- 单向数据流：B → 共享目录 → A；B1-3 只读 A config/ 文件，绝不写 A、绝不读 `systemA_feedback/`。
- 机器输出零中文策略名（只用 strategy_id）；`approval_policy="REFERENCE_ONLY"` 永不自动 APPROVED。
- 非 dry-run 写入前显式调 `IntegrationDir.ensure_root()`（创建根目录 + WARNING，不崩溃）。
- 全量测试命令：`cd research_engines/qteasy_lab && .venv/Scripts/python.exe -B -m unittest discover -s "D:\Project DPS\tests" -q`

## 下一步（阶段二骨架）

阶段二 / 三另行立项（PROJECT_AUDIT 6.2）：压力模拟器、宏观状态持续期、红黄橙风控建议、
参数网格扫描、人机对比月报、版本回滚 `rollback_to()` 启用。
