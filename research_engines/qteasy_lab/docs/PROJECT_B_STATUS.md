# 项目B · 执行状态

> **更新日期**：2026-08-10
> **分支**：develop


## 一、已完成交付

| 交付项 | 说明 |
|---|---|
| 基础层（M1） | reference/包 + 滚动Beta + 波动率锥 + 共享目录 + 心跳 |
| 阶段一（M2） | 网格参考表 + 宏观对冲效率 + 交易指纹 + 端到端管线 |
| 阶段二（M3） | 宏观持续期 + 逐资产red_flag + 版本回滚 |
| 阶段三（M3） | 压力模拟器 + 参数扫描（param_sweep） |
| human_machine_compare 编码 | 分析引擎 + CLI + 合成数据测试（0f0a7f8） |
| human_machine_compare 设计文档 | docs/human_machine_compare_design.md |
| 联调修复 | --real 漏传 include_stress 已修复（2c10c0b） |
| 策略级别回测引擎 | 轻量事件驱动模拟器 + CLI + 报告三件套（reports/backtest/）+ 47 引擎测试 |
| 回测补充产出 | per-asset 归因 + 网格触网汇总（run_backtest_supplements.py，A 侧确认清单响应）+ Bug#6 网格修复 |
| 宏观监控框架设计文档 | docs/macro_monitoring_framework_design.md（三剑客/全球配置：适配月报/相关性/极端情景，设计不编码） |
| 宏观监控框架实现 | M1 适配月报 / M2 相关性 / M3 极端情景韧性（`reference/macro_monitoring.py` + `scripts/run_macro_monitoring.py`，reports/macro_monitoring/，17 新增测试；触发：A 侧策略详情就绪 NOTICE_20260809） |


## 二、当前待办

| 优先级 | 任务 | 状态 | 触发条件 |
|---|---|---|---|
| 1 | 策略级别回测引擎 | ✅ 已交付（开发 + 端到端验证 + 补充产出） | — |
| 2 | 宏观监控框架实现 | ✅ 已交付（M1/M2/M3 + CLI + 17 测试，reports/macro_monitoring/） | 已触发（A 侧策略详情 NOTICE_20260809 就绪） |
| 3 | 参数扫描扩展 | ⬜ 待启动 | A侧策略详情制订完成 |
| 4 | 因子有效性回溯测试 | ⬜ 待启动 | B数据包连续运行≥1个月 + A侧策略详情 |
| 5 | human_machine_compare 真实月报 | ⬜ 等待触发 | human_override_log ≥30条且≥3策略 |


## 三、当前阻塞

- **A侧 `human_override_log` 数据不足**：当前4条/1策略，目标≥30条/≥3策略，预计1-2个月


## 四、监控清单（等待期内）

| 监控项 | 状态 |
|---|---|
| A侧审核工作台上线 | ✅ 已确认 |
| 连续≥3次 --real SUCCESS回执 | ⬜ 待确认 |
| A侧字段需求反馈 | ⬜ 无新需求 |
| human_override_log 行数 | 4条（持续观察） |


## 五、纪律重申

- 只写 B `reports/`，不写共享目录、不写 A
- 只读 A 的 `config/` 配置 + `data/logs/` 审计日志（`human_override_log` + `actual_trade_ledger`）
- 严禁读取 `systemA_feedback/` 内容做分析依据（M-003）
- 每次 `--real` 运行需带 `--include-stress`（2c10c0b）
- 机器产出全 ASCII，`strategy_id`/`asset_id` 标识，零中文策略名


## 六、当前就绪状态

| 能力 | 状态 |
|---|---|
| human_machine_compare 分析引擎 | ✅ 就绪（可随时运行真实月报，等待数据） |
| 策略级别回测引擎 | ✅ 已就绪（reports/backtest/，6 策略 = 5 OK + 1 SKIPPED） |
| 宏观监控框架 | ✅ 已就绪（reports/macro_monitoring/，M1/M2/M3 月度产出，`run_macro_monitoring.py`） |
| 参数扫描扩展 | ⬜ 等待A侧策略详情 |
| 因子有效性回溯测试 | ⬜ 等待B数据包积累 + A策略详情 |