# 项目状态

更新时间：2026-08-04

## 版本状态

- 分支：`develop`
- 最近提交：`975b8f3`、`b6f80f1`
- 工作区：存在未提交的研究系统升级代码和测试，属于当前项目工作成果。
- 不允许通过 `git reset`、`git clean`、`checkout --` 或其他方式丢弃这些修改。

## 验证结果

从 `qteasy_lab` 目录执行全量离线测试：

```text
Ran 60 tests
OK
```

其中包括 5 项 GlobalEtfEngine 离线 fixture 测试。它们验证了 DGS30 优先、DGS10 回退、宏观规则缺失、未来数据过滤和 A 股目录隔离，但没有连接真实 FRED 数据。

## 已完成

- 单资产 ETF/股票投前研究；
- 研究项目、研究周期和历史版本；
- 资产档案、动态数据、因子分析和组合引用；
- Markdown、HTML、PDF、ZIP 报告导出；
- PySide6 桌面端；
- AKShare、第三方 Tushare、本地 CSV/快照回退；
- SQLite 数据管理和 Parquet 因子数据；
- 独立因子研究页面和 A 股因子评分器；
- 因子缺失、日期越界、资产类型错误等结构化诊断；
- `momentum_60d`、`momentum_120d`、低波动和流动性因子骨架；
- 全球 ETF 数据抓取脚本和条件收益 Notebook；
- GlobalEtfEngine 初版及全球 ETF 专用 SQLite 表；
- SPY、TLT、GLD Yahoo Finance 数据抓取。

## 已完成（本轮更新）

- FRED DGS10、DGS2、DGS30、DFII10 数据恢复（API 下载，2003 起，质量 A）；
- SPY/TLT/GLD 资产数据窗口扩至 2003（Yahoo 抓取），条件收益样本全部 ≥24 个月；
- 五档离散 modifier 建议函数 `suggest_modifier_from_condition_returns`；
- 12 条 DRAFT 宏观规则写入 `global_etf_macro_rule`（含样本起止、置信度）；
- 其中 5 条 APPROVED（real_yield_up/down 状态，样本 ≥60 且非中性）；
- SPY/TLT/GLD 研究配置已启用（enabled=1, status=ENABLED）；
- 引擎真实评分可运行，输出落 `data/global_etf_values/`。

## 未完成

- 利率状态（rate_up/rate_down）样本 <60 个月，仍为 DRAFT/CANDIDATE，尚未 APPROVED（等样本积累到 60，约 2027 年再人工复核）；
- GlobalEtfEngine 桌面端页面（P2）；
- 全球研究资产与交易资产映射（P3）；
- 宏观规则版本比较和规则审核 UI。

## 当前运行状态

SPY、TLT、GLD 研究资产已启用，5 条 APPROVED 宏观规则已就绪。当前宏观状态三元组为 `rate_up + curve_normal + real_yield_up`：real_yield_up 有 APPROVED 规则，但 rate_up（样本 52<60，DRAFT）与 curve_normal（无数据，不落库）缺 APPROVED，故 GlobalEtfEngine 返回 `PARTIAL`（base_score 可算，macro_modifier/final_score 为空，warnings 点名缺失规则）。这是预期的安全行为，不是失败。
