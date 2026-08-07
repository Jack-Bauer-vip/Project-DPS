# 项目B · 执行状态

> **更新日期**：2026-08-07
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


## 二、当前待办

| 优先级 | 任务 | 状态 | 触发条件 |
|---|---|---|---|
| 1 | 策略级别回测引擎 | ⬜ 待启动 | A侧 `strategy_contract.json` 就绪 |
| 2 | 参数扫描扩展 | ⬜ 待启动 | A侧策略详情制订完成 |
| 3 | 因子有效性回溯测试 | ⬜ 待启动 | B数据包连续运行≥1个月 + A侧策略详情 |
| 4 | human_machine_compare 真实月报 | ⬜ 等待触发 | human_override_log ≥30条且≥3策略 |


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
| 策略级别回测引擎 | ⬜ 等待A侧契约 |
| 参数扫描扩展 | ⬜ 等待A侧策略详情 |
| 因子有效性回溯测试 | ⬜ 等待B数据包积累 + A策略详情 |