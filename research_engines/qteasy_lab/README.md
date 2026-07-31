# qteasy_lab — ETF 量化研究与策略回测工作台

基于 **qteasy 2.6.0** 的个人 ETF 全球资产配置研究与回测系统。

## 目录结构

```
qteasy_lab/
├── qteasy_research/          # Python 包 — 可复用的研究框架
│   ├── strategies/           # 策略库
│   │   ├── base.py           #   策略基类
│   │   ├── equal_weight.py   #   等权策略
│   │   ├── risk_parity.py    #   风险平价策略
│   │   ├── inverse_vol.py    #   波动率倒数加权策略
│   │   └── momentum.py       #   动量策略
│   ├── backtesting/          # 回测引擎
│   │   ├── engine.py         #   回测运行器 BacktestEngine
│   │   ├── metrics.py        #   绩效指标计算
│   │   └── reporter.py       #   回测报告生成
│   ├── data/                 # 数据工具
│   │   ├── loader.py         #   数据加载与验证
│   │   └── downloader.py     #   批量数据下载
│   └── config.py             # qteasy 配置管理
├── scripts/                  # 原始数据流水线脚本（向后兼容）
├── data/                     # CSV 数据文件
├── logs/                     # 系统日志和交易日志
├── run_backtest.py           # 通用回测入口
├── requirements.txt          # 依赖清单
└── pyproject.toml             # Python 包配置
```

## 快速开始

### 1. 设置环境

```powershell
# 激活虚拟环境
cd research_engines\qteasy_lab
.venv\Scripts\activate

# 设置 Tushare Token（用您自己的 Token）
set TUSHARE_TOKEN=your_token_here
set TUSHARE_API_URL=https://api2.tushare.org
```

### 2. 运行回测

```powershell
# 等权策略（默认）
python -X utf8 run_backtest.py

# 风险平价策略
python -X utf8 run_backtest.py --strategy risk_parity

# 动量策略（回溯126日，选前3名）
python -X utf8 run_backtest.py --strategy momentum --lookback 126 --top-n 3

# 对比多个策略
python -X utf8 run_backtest.py --compare
```

> **关于 `-X utf8`**：Windows 中文环境下需要此参数确保回测报告正常显示。

### 3. 以代码方式使用

```python
from qteasy_research.backtesting.engine import BacktestEngine, BacktestConfig
from qteasy_research.strategies.equal_weight import EqualWeightStrategy
from qteasy_research.strategies.risk_parity import RiskParityStrategy

# 创建策略
strategy = RiskParityStrategy(window_length=60)

# 配置回测
config = BacktestConfig(
    strategy=strategy,
    asset_pool=[
        "518880.SH",  # 黄金ETF
        "159941.SZ",  # 纳指ETF
        "513050.SH",  # 中概互联
        "513520.SH",  # 日经ETF
        "512890.SH",  # 红利低波
    ],
    cash=100_000,
    start="20190801",
)

# 运行回测
engine = BacktestEngine(config)
result = engine.run()
```

## 可用策略

| 策略 | 说明 | 默认参数 |
|------|------|----------|
| `equal_weight` | 等权配置（基准策略） | window_length=2 |
| `risk_parity` | 风险平价（等风险贡献） | window_length=60 |
| `inverse_vol` | 波动率倒数加权 | window_length=60, min_vol=0.05 |
| `momentum` | 动量策略（选 Top N） | lookback=126, top_n=3, method="return" |

## 环境要求

- Python 3.11+
- qteasy 2.6.0（在 .venv 中已安装）
- Tushare 账号和 Token
- 操作系统：Windows（已处理中文编码问题）

## 注意

- **ETF 回测补丁**：已通过脚本 09_patch 修改 qteasy 源码，使场内 ETF 可以读取 fund_daily 价格
- **数据更新**：运行 scripts/05_download_market_data.py 可更新数据
- **TA-lib 可选**：未安装 TA-lib 不影响本模块的自定义策略

## ETF/股票自动投前研究

首期已提供本地优先的投前研究 API 和 CLI。它独立读取 `data/fund_daily.csv`、
`data/fund_basic.csv`、`data/index_daily.csv` 等数据，不会修改正式资产池或现有回测配置。

```powershell
cd research_engines\qteasy_lab
python -B -m qteasy_research.pretrade research 518880.SH --offline --provider none
```

研究产物默认写入 `research_store/runs/<run_id>/`：

- `report.md`：Markdown 投前研究报告
- `result.json`：结构化结果和研究阶段
- `charts/`：归一化价格和回撤图
- `research.sqlite3`：研究任务、阶段和证据索引

可选模型：

```powershell
python -B -m qteasy_research.pretrade research 518880.SH --provider ollama --model deepseek-r1:14b
$env:DEEPSEEK_API_KEY = "your-key"
python -B -m qteasy_research.pretrade research 518880.SH --provider deepseek --model deepseek-chat
```

模型输出必须带有可核验来源 URL；无法联网或来源不完整时，系统仍会保留确定性定量报告并标注缺失项。
### 持久化研究项目

研究项目会复用历史缓存，并将每次刷新保存为独立版本：

```powershell
python -B -m qteasy_research.pretrade project create "黄金 ETF 观察" 518880.SH
python -B -m qteasy_research.pretrade project list
python -B -m qteasy_research.pretrade research 518880.SH --project-id <project_id> --update-policy refresh --offline
```

项目数据位于 `research_store/projects/<project_id>/versions/`，公共数据快照位于 `research_store/cache/`。项目关闭后只读，重新打开后继续研究会生成新版本。

### 桌面研究工作台

桌面端使用 PySide6，可选安装：

```powershell
pip install -e ".[desktop]"
python -B -m qteasy_research.desktop
```

桌面端首版提供项目列表、新建项目、研究版本生成、研究笔记、人工确认结论、报告查看和本地存储目录设置。API Key 仍建议通过环境变量配置，不写入普通项目文件。
### 一级资产档案与二级策略项目

项目现在分为两级：

- `ASSET_PROFILE`：单个 ETF/股票的长期基础档案，可被多个策略项目复用。
- `STRATEGY_PORTFOLIO`：策略或组合研究项目，通过资产档案引用固定基础研究版本。

命令行创建二级策略项目并关联资产档案：

```powershell
python -B -m qteasy_research.pretrade project create "黄金防御组合" PORTFOLIO `
  --project-type STRATEGY_PORTFOLIO `
  --strategy-name risk_parity
```

桌面端“研究项目”页面现在可以按“资产档案 / 策略组合”筛选；创建策略项目后，在项目详情中输入资产档案代码即可关联。
