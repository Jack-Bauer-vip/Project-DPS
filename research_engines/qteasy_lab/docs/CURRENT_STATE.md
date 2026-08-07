# 项目B 当前状态（CURRENT_STATE）

> 更新于：2026-08-07 ｜ 阶段三收尾 + 联调准备

## 当前进度

| 阶段 | 内容 | 状态 |
|------|------|------|
| 基础层（M1） | `reference/` 包 + 滚动 Beta + 共享目录 + 心跳 | ✅ 已完成（提交 4c8d81a） |
| 阶段一（M2） | B1-1 网格参考表 / B1-2 宏观对冲效率 / B1-3 交易指纹 / 端到端管线 / 接口契约 | ✅ 已完成（提交 fe48091） |
| 阶段二（M3） | 宏观持续期 duration_phase + 逐资产 red_flag + 版本回滚启用 | ✅ 已完成（提交 0ac463a） |
| 阶段三（M3） | stress_simulator + param_sweep | ✅ 已完成（提交 d30cd72，验收通过） |
| 阶段三（延后项） | human_machine_compare（人机对比月报） | ⏳ 延后至 2026 年 11 月后评估 |

**全量测试：300 tests OK（skipped=2）**（阶段二 277 + 阶段三新增 23）。

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

## 联调准备（共享目录就绪后执行）

当前共享目录 `D:\FF Project\data\integration\` **尚未创建**（A 侧未建），无需立即执行真实写入。
待 A 侧创建后按以下步骤验证：

1. 运行 `scripts/run_reference_pipeline.py --include-stress --real`（非 dry-run）。
2. 确认 `systemB_ref/{YYYYMMDD}/` 目录结构完整，含 `.ready` + `package.json` + 全部 5 个产出文件
   （`decision_ref_package.json` / `assets_metadata.csv` / `grid_reference_table.csv` /
   `macro_hedge_efficiency.parquet` / `b_heartbeat.json`）。
3. 确认 `b_heartbeat.json` 已更新（`last_seen` 为当前时间）。
4. 到 A 侧确认 `integration_reader.read_decision_package()` 可读取并回写 SUCCESS 回执
   （`systemA_feedback/consumed_{日期}.json`）。
5. 若 `read_decision_package()` 返回 None，检查 A 侧日志，定位问题后反馈。

## 纪律重申（不可违反）

- **B 只写** `systemB_ref/` + `b_heartbeat.json` + `manifest.json`。
- **B 只读** A 的 `config/` 配置文件（仅 trader_fingerprint 模块离线分析）。
- **B 严禁**读取 `systemA_feedback/` 内容作分析依据。
- **B 严禁**修改 `systemA_feedback/` 中的任何文件。
- 机器产出全 ASCII，零中文策略名；`approval_policy="REFERENCE_ONLY"` 永不自动 APPROVED。

## 下一步

- **联调（M3）**：等 A 建共享目录 + 读端就绪后执行上述验证流程。
- **human_machine_compare**：等 A 侧 `human_override_log` 积累 ≥3 个月（2026 年 11 月后评估）。
