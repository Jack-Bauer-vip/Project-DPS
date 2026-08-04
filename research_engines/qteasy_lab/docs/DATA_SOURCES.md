# 数据源与本地数据

## A 股和 ETF

当前直连顺序：

```text
AKShare
→ 第三方 Tushare API
→ 本地 CSV / SQLite 研究快照
```

Tushare 使用环境变量：

```powershell
$env:TUSHARE_TOKEN="你的Token"
$env:TUSHARE_API_URL="https://ts.gyzcloud.top/api"
```

Token 不得写入源码、文档、日志、报告或 Git。

## 全球宏观和海外 ETF

数据源配置见：

```text
data/raw/global_data_sources.json
```

当前使用：

| 数据 | 来源 | 频率 | 用途 |
|---|---|---|---|
| DGS10 | FRED | 日频 | 10 年期名义利率、DGS30 回退 |
| DGS2 | FRED | 日频 | 期限利差 |
| DGS30 | FRED | 日频 | TLT 首选利率代理 |
| DFII10 | FRED | 日频 | 10 年期实际利率 |
| SPY | Yahoo Finance | 日频 | 股票研究资产 |
| TLT | Yahoo Finance | 日频 | 长债研究资产 |
| GLD | Yahoo Finance | 日频 | 黄金研究资产 |

抓取脚本：

```text
scripts/fetch_global_macro_data.py
```

标准化数据保存于：

```text
data/processed/global_macro/
```

原始响应保存于：

```text
data/raw/global_macro/
```

## FRED 当前状态与备选路径

FRED 直连（API 或 graph URL）可能因网络出口超时而失败。`fetch_global_macro_data.py` 已实现**本地 CSV 优先 + 网络回退**：

- 检测到 `data/raw/global_macro/{series_id}.csv` 时，优先从文件导入（`import_mode=local_csv`）；
- 文件不存在且未指定 `--local-only` 时，才走网络抓取（`import_mode=network`）；
- `--prefer-network` 强制联网（用于生成新数据快照，不删除原始文件）；`--local-only` 离线确定性运行（缺失即失败），两者互斥。

### 手动下载并导入 FRED CSV

1. 在 FRED 系列页面（如 `https://fred.stlouisfed.org/series/DGS10/`）下载 CSV，或使用 graph 链接 `https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10`。
2. 将文件放入：

   ```text
   data/raw/global_macro/DGS10.csv
   data/raw/global_macro/DGS2.csv
   data/raw/global_macro/DGS30.csv
   data/raw/global_macro/DFII10.csv
   ```

3. 运行导入（仅 FRED 系列）：

   ```powershell
   cd D:\Project DPS\research_engines\qteasy_lab
   .\.venv\Scripts\python.exe -B scripts\fetch_global_macro_data.py --series DGS10 DGS2 DGS30 DFII10
   ```

### 最低字段与质量分档

最低字段：

```text
observation_date
value
```

`value` 列也可以是系列名（FRED graph CSV 的值列表头是 `DGS10` 等）。日期列支持 `observation_date` / `DATE` / `date`。质量等级完全由 `available_at` 档位推导，不信任文件自带的质量列：

| 文件内容 | available_at 处理 | 质量等级 | 警告 |
|---|---|---|---|
| 含 `available_at` 列 | 原样使用 | 配置值（A） | 无 |
| 仅含 `realtime_start` 列 | 从 `realtime_start` 派生 | B | 记录降级警告 |
| 都没有 | 按下个工作日合成 | C | 记录降级警告 |

没有 `available_at` 的手动文件一律降级并记录警告，不能无提示地视为官方实时快照。导入后核对 `data/processed/global_macro/manifest.json`：DGS* 结果应为 `import_mode=local_csv`，并带有 `warnings` 说明。

## 不提交 Git 的运行产物

```text
.venv/
research_store/
data/raw/
data/processed/
data/global_etf_values/
*.sqlite3
日志、报告、图表和完整行情 CSV
```
