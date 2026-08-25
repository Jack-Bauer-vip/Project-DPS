# 网格策略研究记录（B 侧）

> 文档日期：2026-08-14 ｜ 归属：项目B（`research_engines/qteasy_lab`）
> 关联代码：`qteasy_research/reference/grid_suggestion.py`（建议引擎）、
> `scripts/init_grid_research_projects.py`（P3 研究项目初始化）。
> 关联文档：`docs/strategy_contract_design.md`（契约基线）、`docs/CURRENT_STATE.md`（项目状态）。

## 1. 研究目的

对 A 侧（`D:\FF Project`）两个网格策略实例 `grid_lh` 与 `grid_scz` 建立 B 侧投前研究
记录，使每个实例与标的在 pretrade 研究体系（`research_store`）中有对应的研究项目、
组合标的上下文与初始笔记，作为后续逐标的投前研究、网格参数复核与历史版本追踪的组织单位。

两个实例均为 **4 个 enabled 标的**（来源：A 侧 `config/grid_config.csv` 的 enabled 行，
与 `strategy_contract.json` 的 `grid` 策略一致）：

| 实例（strategy_id） | 决策规则 | enabled 标的 | 说明 |
|---|---|---|---|
| `grid_lh` | grid | `159985.SZ`、`513520.SH`、`513650.SH`、`515180.SH` | `518880.SH` 停用（黄金换豆粕 2023-07-23） |
| `grid_scz` | grid | `513520.SH`、`513650.SH`、`515180.SH`、`518880.SH` | — |

> 标识纪律：`strategy_id` / `asset_id` / 项目名全 ASCII；`grid_lh` / `grid_scz`
> 是 B 侧与 A 侧共享的唯一策略标识，零中文策略名。

## 2. 方法论

B 侧对网格策略做**参考性投前研究**（`approval_policy="REFERENCE_ONLY"`），不自动改资产池、
组合权重、回测配置，不生成交易指令。研究链路：

```text
A 侧 grid_config.csv（网格参数种子）/ strategy_contract.json（契约）
    ↓ 只读
B 侧建议引擎 grid_suggestion.py（适合度 / 中轴 / 两段步长 / 相关性）
    ↓ 输出
grid_suggestion 包（REFERENCE_ONLY，供人工参考，B 侧 reports/ + 共享目录 systemB_ref/）
    ↓ 组织单位
pretrade 研究项目（P3：每实例 STRATEGY_PORTFOLIO + 每标的 ASSET_PROFILE + 初始记录）
```

研究分层：

1. **适合度（suitability）**：判断标的当前是否适合网格交易（波动处于目标分位、日均振幅
   与档距匹配、趋势漂移小、触发频率适中）。
2. **中轴（anchor）**：网格挂单的参考中枢。
3. **两段步长（regular / edge）**：常规段与边缘段的分层步长建议。
4. **相关性（correlation）**：实例内标的组合的冗余度提示。

缺失数据不虚构：任何分量不可算时置 `None`，suitability 按剩余分量**权重重归一**；
历史不足 20 日（`_MIN_HISTORY`）时输出 `confidence="low"` 且核心字段置 `None`。

## 3. 建议引擎口径（唯一源）

计算口径唯一源 = `contract.shared_config.grid`（A 侧 `strategy_params.json` 的
`"grid"` 段经契约透传）；缺省用代码常量 `_DEFAULT_GRID_PARAMS`。

### 3.1 suitability 权重

```text
score = 0.30·vol_rank + 0.20·amplitude + 0.30·drift + 0.20·trigger_freq
```

| 分量 | 公式（满分 100） | 缺失处理 |
|---|---|---|
| vol_rank | `100 − 100·|vol_rank − 0.60| / 0.50`，clamp [0,100] | `None` |
| amplitude | `100 · (mean_daily_range / suggested_spread) / 0.80`，clamp [0,100] | `None` |
| drift | `100 − 250·(|mean(ret)| / std(ret))`，clamp [0,100] | `None` |
| trigger_freq | `[8, 60]` 次/年 → 100；`[0,8)` 与 `(60,120]` 线性衰减；`>120` → 0 | `None` |

分级：`score ≥ 70 → suitable`；`[45, 70) → marginal`；`< 45 → not_suitable`。

- 缺失分量剔除后，剩余分量按原权重**归一化**再算总分（不把缺失当 0）。
- 全部缺失 → `score=None`、`grade=not_suitable`。

### 3.2 anchor（中轴）

60 交易日窗口，按优先级取第一个可用：

```text
1) 60d VWAP：Σ(close·volume) / Σ(volume)      （有 volume 且量>0）→ basis="vwap_60"
2) SMA60：mean(close)                         （无量）           → basis="sma_60"
3) 60d 中点：(max(high) + min(low)) / 2       （兜底）           → basis="mid_60"
```

保留 3 位小数。

### 3.3 两段步长（regular / edge）

```text
regular_spread = suggested_reference_spread        （grid_reference 已算或现算，60d 年化波动率推导）
edge_spread    = regular × max(默认乘子 2.0, cone_60_p95 / cone_60_p50)
                 且 ≤ 4.0 × regular（封顶）
edge_spread_basis = "cone_60_p95"（锥尾比例放大） 或 "default_multiplier"（无锥数据）
```

档数：

```text
regular_levels_per_side = 3   （常规段每侧档数）
edge_levels_per_side    = 1   （边缘段每侧档数；vol_rank_60d > 0.80 时升为 2）
```

`edge_spread` 只影响边缘段（价格偏离中枢较远时的更宽步长），常规段用 `regular_spread`。

### 3.4 correlation（相关性）

```text
窗口 90 个交易日，两两日收益相关（最小重叠 60 日）。
|corr| > 0.60 → high_corr_pairs；avg_corr ≥ 0.60 → redundancy_note="high"，
avg_corr ≥ 0.40 → "moderate"，否则 "low"。
```

> 阈值常量：`CORR_HIGH_THRESHOLD=0.6`、`CORR_MODERATE_THRESHOLD=0.4`
> （`qteasy_research/reference/config.py`）。

## 4. grid_suggestion 包使用说明

### 4.1 模块入口

```python
from qteasy_research.reference import build_grid_suggestion, build_grid_suggestion_table

suggestion = build_grid_suggestion(
    contract.strategies,        # list[ContractStrategy]（grid 策略）
    aligned,                    # {asset_id: DataFrame(trade_date, close, vol, ...)}
    grid_ref_frame,             # grid_reference_table DataFrame 或 {asset_id: 行 dict}
    contract.shared_grid or {}, # shared_config.grid（缺省用代码默认）
    data_asof,                  # YYYY-MM-DD
)
table = build_grid_suggestion_table(suggestion)   # 扁平 CSV（每标的一行）
```

### 4.2 输出结构

```json
{
  "schema_version": "grid-suggestion-v1",
  "approval_policy": "REFERENCE_ONLY",
  "generated_date": "...",
  "data_asof": "...",
  "strategies": [
    {
      "strategy_id": "grid_lh",
      "assets": [
        {
          "asset_id": "...",
          "suitability_score": 78.5,
          "suitability": "suitable",
          "suitability_breakdown": {"vol_rank_score": ..., "amplitude_score": ...,
                                    "drift_score": ..., "trigger_freq_score": ...},
          "anchor_suggestion": 1.583,
          "anchor_basis": "vwap_60",
          "regular_spread": 0.012,
          "edge_spread": 0.024,
          "edge_spread_basis": "cone_60_p95",
          "regular_levels_per_side": 3,
          "edge_levels_per_side": 1,
          "confidence": "high"
        }
      ],
      "correlation": {"matrix": {...}, "high_corr_pairs": [...],
                      "avg_corr": 0.52, "redundancy_note": "moderate"}
    }
  ]
}
```

### 4.3 运行方式

```powershell
# dry-run 到本地 outputs/（含 grid_suggestion/grid_suggestion.json + table.csv）
.\.venv\Scripts\python.exe -B scripts\run_reference_pipeline.py --dry-run

# 真实发布到共享目录 systemB_ref/<run_id>/grid_suggestion/
.\.venv\Scripts\python.exe -B scripts\run_reference_pipeline.py --real

# 显式跳过
.\.venv\Scripts\python.exe -B scripts\run_reference_pipeline.py --no-grid-suggestion
```

契约缺失时跳过网格建议 + warning（不崩溃）；`include_grid_suggestion=False` 时零产出。

## 5. 与 A 侧 grid_config.csv 的关系

A 侧 `D:\FF Project\config\grid_config.csv` 是网格策略的**参数种子**（含 `anchor_price`、
`regular_spread`、`edge_spread`、`regular_levels_per_side`、`edge_levels_per_side`、
`rating`、`source`、`notes`）。B 侧只读，绝不修改。

| 关系 | 说明 |
|---|---|
| 标的集合 | B 侧 P3 初始化以 grid_config.csv 的 `enabled=1` 行为准（缺省回退 strategy_contract.json 的 `enabled_assets`），不硬编码 |
| 参数种子 | grid_config.csv 的档距/档数/评级是人工或既有确认值，与 B 侧建议引擎的推荐值互为参照；B 侧建议只作 `REFERENCE_ONLY` |
| 契约 | A 侧导出 `strategy_contract.json` 时把 `grid_config.csv` 的内容映射为 `assets[].grid_config` 嵌套字段，B 侧回测引擎按契约解析 |
| 数据流 | `grid_config.csv` →（A 导出）→ `strategy_contract.json` →（B 读）→ `grid_suggestion` 建议 / 回测引擎 / P3 研究项目初始化 |

B 侧初始化脚本按 grid_config.csv 的 enabled 标的建立研究项目；当某实例在 grid_config.csv
中缺失 enabled 行时回退契约，两者皆无则**报错不虚构**。

## 6. P3 研究项目初始化脚本

`scripts/init_grid_research_projects.py` 为两个实例初始化 pretrade 研究项目：

```powershell
# 预览计划（默认，零写入）
.\.venv\Scripts\python.exe -B scripts\init_grid_research_projects.py

# 实际写入 B 本地 research_store
.\.venv\Scripts\python.exe -B scripts\init_grid_research_projects.py --apply
```

产物：

- **每实例一个 `STRATEGY_PORTFOLIO` 项目**：`strategy_name=grid_lh|grid_scz`，
  组合标的上下文 = enabled 标的（`role="grid"`，weight 不虚构为 0）。
- **每个唯一标的的 `ASSET_PROFILE` 项目**：首次出现时创建；已存在（如 `518880.SH`
  已有"黄金"项目）则复用，不覆盖。
- **初始记录**：`grid_pretrade_init` 笔记（来源、标的集合、grid_config 原始行）；
  策略项目→资产项目的资产引用（`role="grid"`）。

幂等与非破坏性：重复运行复用既有项目、不追加重复笔记/引用、只 upsert 缺失的标的
上下文（**不删除**用户手动添加的其他组合标的），不覆盖历史研究版本/快照。
只写 B 本地 `research_store`，不写共享目录、不写 A。

## 7. 安全边界

- 建议引擎与初始化脚本全部 `approval_policy="REFERENCE_ONLY"`，永不自动 APPROVED。
- B 只读 A 的 `config/`（grid_config.csv / strategy_contract.json / asset_pool.csv），
  绝不修改；严禁读取/修改 `systemA_feedback/`。
- 机器产出全 ASCII，`strategy_id` / `asset_id` / 项目名零中文策略名。
- 不把缺失宏观数据当作中性；不自动下单、不改资产池/权重/回测配置。
