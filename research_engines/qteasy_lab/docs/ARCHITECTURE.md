# 系统架构与边界

## A 股和单资产研究链路

```text
AKShare
    ↓ 失败或字段不足
第三方 Tushare API
    ↓
本地 CSV / SQLite 数据快照
    ↓
SQLite 因子配置 + Parquet 因子值
    ↓
qteasy_research.pretrade.factor_scoring
    ↓
资产评分、研究报告和桌面端展示
```

## 全球 ETF 宏观链路

```text
FRED / Yahoo Finance
    ↓
data/processed/global_macro/*.csv
    ↓
notebooks/global_macro_lab.ipynb
    ↓
人工审阅条件收益
    ↓
global_etf_macro_rule（DRAFT/APPROVED）
    ↓
GlobalEtfEngine
    ↓
SPY/TLT/GLD 宏观匹配评分
```

## 现有回测主流程范围

现有回测主流程包括：

```text
qteasy_research/backtesting/
qteasy_research/strategies/
run_backtest.py
```

后续研究功能不得重构这条数据流。回测、资产池、策略配置和研究评分之间保持边界。

## 投前研究流程范围

```text
qteasy_research/pretrade/projects.py
qteasy_research/pretrade/orchestrator.py
qteasy_research/pretrade/storage.py
qteasy_research/pretrade/factors.py
qteasy_research/pretrade/factor_scoring.py
qteasy_research/pretrade/data_manager.py
```

## 引擎隔离规则

- `GlobalEtfEngine` 不导入 A 股 `factor_scoring.py`。
- A 股因子值位于 `research_store/factor_values/`，全球 ETF 数据位于 `data/processed/global_macro/` 和 `data/global_etf_values/`。
- 两类模块可以共享 `research.sqlite3` 文件，但使用不同表。
- 全球宏观缺失不能转换成 1.0 或其他中性评分。
- 研究资产和实际交易资产分开记录，不强行将 TLT 映射为 A 股替代品。
- 系统不会自动修改资产池、组合权重或生成交易指令。
