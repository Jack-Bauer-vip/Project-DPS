# qteasy_lab：ETF/股票研究与回测工作台

这是一个基于 qteasy 的个人投资研究项目，包含：

- ETF/股票投前研究；
- 资产档案和研究项目版本管理；
- A 股因子研究与评分；
- 全球 ETF 宏观匹配研究；
- 既有策略回测和报告输出。

## 交接资料

后续 AI 请先阅读：

1. [AI_HANDOFF.md](AI_HANDOFF.md)；
2. [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)；
3. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)；
4. [docs/DECISION_LOG.md](docs/DECISION_LOG.md)；
5. [docs/NEXT_STEPS.md](docs/NEXT_STEPS.md)。

## 环境

```text
Python 3.11+
Windows
qteasy 2.6+
PySide6（桌面端可选）
```

项目目录：

```powershell
cd D:\Project DPS\research_engines\qteasy_lab
```

## 安装依赖

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

桌面端依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[desktop]"
```

## 回测

```powershell
.\.venv\Scripts\python.exe -X utf8 run_backtest.py
.\.venv\Scripts\python.exe -X utf8 run_backtest.py --strategy risk_parity
.\.venv\Scripts\python.exe -X utf8 run_backtest.py --strategy momentum --lookback 126 --top-n 3
.\.venv\Scripts\python.exe -X utf8 run_backtest.py --compare
```

回测主流程位于：

```text
qteasy_research/backtesting/
qteasy_research/strategies/
run_backtest.py
```

## 单资产投前研究

```powershell
.\.venv\Scripts\python.exe -B -m qteasy_research.pretrade research 518880.SH --offline --provider none
```

研究结果保存到：

```text
research_store/
```

研究版本包含 Markdown、JSON、PNG，并可导出 HTML、PDF 和 ZIP。

## A 股因子评分

因子配置保存于 SQLite，因子值保存于 Parquet：

```text
research_store/research.sqlite3
research_store/factor_values/
```

当前生产评分器不增加 KDJ，不自动生成交易指令，也不会自动修改资产池或回测策略。

## 全球 ETF 宏观研究

数据抓取：

```powershell
.\.venv\Scripts\python.exe -B scripts/fetch_global_macro_data.py `
  --start 2018-01-01 --end 2026-08-03
```

条件收益 Notebook：

```powershell
.\.venv\Scripts\python.exe -B scripts/create_global_macro_notebook.py
```

核心引擎：

```python
from qteasy_research.core import GlobalEtfEngine

engine = GlobalEtfEngine(
    data_root="data",
    store_root="research_store",
)
result = engine.calculate_scores(
    target_date="2026-08-03",
    assets=["SPY", "TLT", "GLD"],
    persist=False,
)
```

规则未人工批准或 FRED 数据缺失时，结果会是 `PARTIAL`，最终分数为 `null`，不会把缺失数据当作中性。

## 桌面端

```powershell
.\.venv\Scripts\python.exe -B -m qteasy_research.desktop
```

也可以使用：

```text
start_desktop.bat
启动投前研究桌面版.bat
```

## 测试

必须从本目录执行：

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover `
  -s "D:\Project DPS\tests" -q
```

当前验收结果：

```text
Ran 60 tests
OK
```

## 数据和安全

Tushare 等服务通过环境变量配置：

```powershell
$env:TUSHARE_TOKEN="你的Token"
$env:TUSHARE_API_URL="https://ts.gyzcloud.top/api"
```

不要把 Token、API Key、SQLite、完整行情、日志、报告和 `.venv` 提交到 Git。详细数据源说明见 [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)。
