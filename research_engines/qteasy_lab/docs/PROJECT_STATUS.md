# 项目状态

更新时间：2026-08-04

## 版本状态

- 分支：`develop`
- 最近提交：`d89ae15`（基线收尾）、`ced2476`、`4bf7ace`、`beedd3f`、`8c6c539`、`e9f5a13`、`74a9b4e`
- 工作区：干净（全部工作成果已入库）。

## 验证结果

从 `qteasy_lab` 目录执行全量离线测试：

```text
Ran 118 tests
OK
```

包括 GlobalEtfEngine 5 项离线 fixture、FRED 本地导入 15 项、宏观规则 15 项、映射 19 项、桌面端页面/映射 UI 9 项等。

## 已完成

### A 股投前研究（基线）

- 单资产 ETF/股票投前研究、研究项目、研究周期和历史版本；
- 资产档案、动态数据、因子分析和组合引用；
- Markdown、HTML、PDF、ZIP 报告导出；
- PySide6 桌面端；
- AKShare、第三方 Tushare、本地 CSV/快照回退；
- SQLite 数据管理和 Parquet 因子数据；
- 独立因子研究页面和 A 股因子评分器；
- 因子缺失、日期越界、资产类型错误等结构化诊断；
- `momentum_60d`、`momentum_120d`、低波动和流动性因子骨架。

### 全球 ETF 宏观（P0-P3）

- FRED DGS10/DGS2/DGS30/DFII10 数据恢复（API 下载，2003 起，质量 A）；
- SPY/TLT/GLD 数据窗口统一 2003 起，条件收益样本全部 ≥24 个月；
- `fetch_global_macro_data.py` 支持 FRED 本地 CSV 导入 + 网络回退；
- Notebook 产出条件收益表与候选 modifier；
- 五档离散 modifier 建议 + 样本联动（<24 REFERENCE_ONLY / 24-59 CANDIDATE / ≥60 APPROVED）；
- 12 条 DRAFT 宏观规则，其中 5 条（real_yield 状态）已人工确认 APPROVED；
- SPY/TLT/GLD 研究配置已启用；
- GlobalEtfEngine 真实评分，输出落 `data/global_etf_values/`；
- 桌面端全球 ETF 宏观研究页面（`GlobalEtfPage`）；
- 研究资产↔交易资产映射数据模型 + 桌面端映射配置 UI。

## 未完成

- 利率状态（rate_up/rate_down）样本 <60 个月，仍为 DRAFT/CANDIDATE，尚未 APPROVED（等样本积累到 60，约 2027 年再人工复核）；
- 交易资产换算逻辑（汇率差异、管理费和交易成本的应用）；
- 宏观规则版本比较和规则审核 UI。

## 当前运行状态

SPY、TLT、GLD 研究资产已启用，5 条 APPROVED 宏观规则已就绪。当前宏观状态三元组为 `rate_up + curve_normal + real_yield_up`：real_yield_up 有 APPROVED 规则，但 rate_up（样本 52<60，DRAFT）与 curve_normal（无数据，不落库）缺 APPROVED，故 GlobalEtfEngine 返回 `PARTIAL`（base_score 可算，macro_modifier/final_score 为空，warnings 点名缺失规则）。这是预期的安全行为，不是失败。

## 关键决策记录

见 `docs/DECISION_LOG.md`：五档离散 modifier、样本联动、无数据状态不落库、rate_up 不破例、数据窗口统一 2003 起、接受 PARTIAL 为常态。
