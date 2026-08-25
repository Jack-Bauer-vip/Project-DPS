# B 端网格锚点 / 间距计算方案升级 + 网格回测评估（V2）

> 版本：2026-08-24 | 状态：**阶段一已落地（schema v2 已发包）**
> 决策依据：用户方向（锚点四方向 / 间距五方向）+ 联网调研（固定 vs 浮动锚 180 组回测等）+ Codex 周频动态锚研究 PDF（2026-08-24）。
> 本文件与 `grid_strategy_research.md` 并列；`grid_suggestion.py` 计算口径唯一源仍 = A 侧 `contract.shared_config.grid`。

---

## 一、背景与目标

原网格建议（`grid_suggestion.py` V1）的锚点与间距偏简单：

- **锚点**：单窗口 60d，VWAP→SMA→中点 三级回退，无稳定性评估、无参考锚列表。
- **间距**：`grid_reference` 或 `volatility_cone.suggest_reference_spread`（60d 波动率 × 3.0 × 分档乘子），无 ATR 直接驱动、无市场制度调节、无最低佣金成本硬约束。

目标：按「多周期综合主锚 + 参考列表 + 稳定性评分」与「ATR 驱动 + 制度调节 + 成本硬约束」两条主线升级，并新增**网格回测评估器**（独立于 `backtest_engine`），用于对比不同锚点/间距参数组合的绩效。

## 二、现状摸底（已核实，2026-08-24）

| 模块 | 原行为 | 本次改动 |
|---|---|---|
| `reference/grid_metrics.py` | 无（指标库缺失） | **新建**：ATR/EMA/ADX/四象限/swing/fib/周频动态锚/稳定性 |
| `reference/grid_suggestion.py` | 锚 60d 单窗口；间距 cone 波动率法 | 锚改多周期几何均值；间距改 ATR+制度+成本 |
| `reference/config.py` | `grid-suggestion-v1` | schema → `grid-suggestion-v2` |
| `reference/grid_backtest_eval.py` | 无 | **新建**：独立回测评估器 |
| `reference/asset_pool.py` | 对齐帧丢 OHLC | 保留 open/high/low 透传（ATR/ADX 需要） |
| `reference/grid_recommendation.py` | 调用 V1 私有函数签名 | 同步 V2 签名 + 新字段 |

## 三、联网调研与 Codex 研究结论（关键证据）

1. **固定锚 vs 浮动锚（成交硬重置）无普遍最优**（180 组回测）：震荡市固定锚收益更高（+14.13% vs +11.22%）、交易更多；单边下跌浮动锚少亏；强单边趋势两者都跑输 buy-and-hold。→ 锚点必须「综合主锚 + 参考列表 + 回测对比」，不固化单一锚。
2. **ATR×乘子 × 市场制度四象限调节**是间距自适应主流做法（GridMaster ATR14×1.5；Smart ATR 四象限 ADX>25 判趋势、ATR% 判波幅，乘子 0.7~1.3）。
3. **成本硬约束**：间距 ≥ 2×费率 + 滑点缓冲，ETF 建议 ≥0.15%；低价 ETF/小资金/小订单推高最低佣金占比。
4. **Codex PDF（510300/159915 周度动态锚，22 日样本）**：
   - 锚 = 几何均值 `M_w = exp(Σ w_i·ln C_{w-i})`（适合相对价格网格）+ **滞后阈值 h**（`|M̂/M_{w-1}−1|<h` 锚不动）+ **单周最大移动 u**（`M=M_{w-1}·exp(clip(ln(M̂/M_{w-1}),−u,+u))`）。
   - 周频纪律：周五收盘计算、下周一生效；锚变化 ≠ 强制调仓。
   - **A 股回测必须建模**：T+1、涨跌停（510300=10%/159915=20%）、最小 100 份、佣金=max(成交额×0.03%, 5 元)、无印花税、日线路径近似（高开→高→低→收 / 低开→低→高→收）。
   - 成本下限 `g_min ≈ 2c_v + 2F/(Q·P) + 2s + buffer`。
   - 常规间隔 × 极端倍数敏感性（1.0/1.5/2.0/3.0）：常规步长对成交/费用影响 > 极端倍数 → 极端层是尾部控制参数。

## 四、锚点方案（V2）

### 4.1 主锚：多周期综合锚（几何均值）

- 窗口：`anchor_windows = (20, 60, 90, 120)`；各窗口沿用原优先级：有量 → VWAP，无量 → SMA，再 → 中点（`_window_anchor`）。
- **主锚 = 各窗口有效锚点值的几何均值**（PDF 证据：几何均值适合相对价格网格；grid 是乘性结构）。有效源 ≥3 才出主锚，否则 `None` + `confidence="low"`（缺失不虚构）。
- `anchor_basis = "geomean_of_N_sources"`，N = 实际有效源数。

### 4.2 稳定性评分

`anchor_stability(sources)`：变异系数 cv（std/mean）+ 可选主锚滚动波动惩罚 → 0–100 分，等级 `high ≥70 / medium ≥45 / low`。

### 4.3 参考锚列表（仅供人工判断，不自动替换主锚）

| method | 依据 |
|---|---|
| `weekly_dynamic` | 周频动态锚：几何均值 + 滞后阈值 h=0.5% + 单周最大移动 u=3%（PDF 方案，`weekly_dynamic_anchor`） |
| `dynamic_ema` | 慢衰减 EMA(120) 静态近似（数据不足时诚实标注） |
| `swing_mid` | 摆动点检测（200d 极值中点，`swing_levels`） |
| `fib_mid_618` | 摆动高低点间 61.8% 回撤位（`fib_levels`） |

swing/fib 依赖 200d 窗口与 high/low；数据不足或 high/low 缺失时对应参考锚不出现（诚实降级）。

## 五、间距方案（V2）

### 5.1 主公式

```
spread = clamp(ATR20_pct × spacing_multiplier × regime_mult, cost_floor, spacing_cap)
  ATR20_pct      = ATR(20) / 最新 close（grid_metrics.atr_pct；high/low 缺失退化 |Δclose|）
  spacing_multiplier = shared_config.grid.spacing_multiplier（缺省 1.5）
  regime_mult    = market_regime(ADX14, ATR20_pct) 四象限（缺省 1.0 当 unknown）
  cost_floor     = max(2×fee_rate + 2×min_commission/amount_per_grid + slippage_buffer,
                       spread_floor_min)
  spacing_cap    = shared_config.grid.spacing_cap（缺省 0.05）
```

### 5.2 市场制度四象限（`grid_metrics.market_regime`）

| regime | 判定 | 乘子 |
|---|---|---|
| `trending_highvol` | ADX>25 且 ATR%≥1.0% | 1.2 |
| `trending_lowvol`  | ADX>25 且 ATR%<1.0% | 0.9 |
| `choppy_highvol`   | ADX≤25 且 ATR%≥1.0% | 1.3 |
| `choppy_lowvol`    | ADX≤25 且 ATR%<1.0% | 0.7 |

`spread_basis`：未触成本地板 → `atr20_regime`；触地板 → `atr20_regime_cost_floor` 且 `cost_constraint_applied=True` + `cost_constraint_note="cost_floor=0.00xx"`。

### 5.3 成本硬约束

- `min_commission=5.0`、`slippage_buffer=0.0003`、`spread_floor_min=0.0015`。
- `_cost_floor`：`max(2×fee_rate + 2×min_commission/amount_per_grid + slippage_buffer, spread_floor_min)`（PDF 公式 g_min）。
- 计算值 < cost_floor → 提到 cost_floor 并标记。

### 5.4 旧口径保留

`volatility_cone.suggest_reference_spread`（波动率法）降级为 `spread_alternatives.volatility_method` 参考项，不参与主公式。

## 六、schema v2 新增字段（`grid-suggestion-v2`）

**asset 级**：

| 字段 | 含义 |
|---|---|
| `anchor_sources` | `{vwap_20:…, vwap_60:…, sma_60:…}` 各窗口各源值 |
| `anchor_stability_score` / `anchor_stability_grade` | 稳定性评分与等级 |
| `anchor_references` | `[{method, value, basis}]` 参考锚列表 |
| `spread_basis` | `atr20_regime` / `atr20_regime_cost_floor` |
| `spread_regime` | `{regime, adx, atr_pct}` |
| `spread_alternatives` | `{volatility_method: …}` |
| `cost_constraint_applied` / `cost_constraint_note` | 成本地板是否生效及说明 |

**表（CSV）**：新增对应列（`anchor_sources` 等 dict 存紧凑 JSON 字符串）。A 侧 `read_grid_suggestion` 宽松读取，零破坏（已验证 SUCCESS + 字段透传）。

## 七、网格回测评估器（`grid_backtest_eval.py`）

**独立实现，不改 `backtest_engine`**：A 股约束在评估器内自建回放；数值口径（`round_lot`/佣金）镜像复制，不 import 依赖。对日更/发包/既有回测流程零影响。

### 7.1 回放模型（Day 级）

- **锚策略 ×3**：`fixed`（首日几何均值，全程固定）/ `weekly_dynamic`（周频动态锚：几何均值+h=0.5%+u=3%，周五算、周一生效）/ `floating`（成交后锚重置到成交档位价）。
- **间距乘子 ×6**：0.5/0.75/1.0/1.25/1.5/2.0；**极端倍数 ×4**：1.0/1.5/2.0/3.0；档数 常规4+极端2（PDF 基线）。
- **档位簿记**：每侧每档价格穿越时最多成交一次；价格回到锚另一侧后重置（可再触发）。floating 成交即重置双侧重排。
- **A 股交易约束**：T+1（当日买入次日可卖，统计阻塞次数）、涨跌停（`--limit`，默认 10%、创业板 20%）、100 份取整、佣金=max(成交额×0.03%, 5 元)、无印花税。
- **日线路径近似**：高开→高→低→收（先卖后买）/ 低开→低→高→收（先买后卖），按路径段穿越判定限价单成交。
- **周度调参**：周五收盘算原始锚，周一开盘生效；锚变化 ≠ 调仓。

### 7.2 指标（每组合）

净收益 / 年化 / 最大回撤 / 成交笔数（买/卖）/ 佣金总额 / **平均资产权重 / 平均库存偏离（相对 50%）** / T+1 阻塞 / 涨跌停阻塞 / 年化换手 / 盈亏比；同口径 50/50 buy-and-hold 基准（含佣金）→ 超额收益。

### 7.3 输出与入口

`reports/grid_backtest_eval/{run}/`：summary CSV + 每锚敏感性矩阵 CSV + markdown 报告（B 本地，不进共享目录）。

```
python -m qteasy_research.reference.grid_backtest_eval --asset 513520.SH [--limit 0.10] [--anchor ...] [--spread-mults ...] [--extreme-mults ...]
```

### 7.4 实测结论（513520.SH，1739 交易日，2026-08-24）

| 锚策略 | 最佳组合（spread×extreme） | 净收益 | 最大回撤 | vs 基准超额 |
|---|---|---|---|---|
| fixed | 2.0×3.0 | +18.2% | -12.7% | -46.1% |
| weekly_dynamic | 2.0×1.5 | **+56.6%** | -19.8% | **-7.7%** |
| floating | 2.0×1.0 | +28.4% | -15.4% | -35.8% |
| **buy-and-hold（50/50）** | — | +64.3% | — | 基准 |

- **周频动态锚明显优于固定/浮动锚**：样本内 ATR 主公式（spread_mult≈2.0）+ 周度调锚最贴近 50% 目标暴露（avg_asset_weight 0.48–0.52），超额亏损最小——与 PDF「周频动态锚控制暴露而非增加利润」结论一致。
- **诚实呈现**：本样本内三锚策略均未跑赢同口径 buy-and-hold（网格改变路径、不消除方向性风险）；样本为 A 池标的日线、非逐笔，短样本最优参数不可固化。
- 极端倍数对结果影响小于间距乘子（PDF 印证：常规步长是收益优化第一杠杆，极端层是尾部控制参数）。

## 八、数据层修复记录

`asset_pool.align_pool_price_history` 原丢弃 open/high/low，导致 grid 锚/间距的 ATR、ADX、摆动点全退化。已修复：`_normalize_price_frame` 保留 OHLC 透传（缺失源降级 None）。修复后 ADX 四象限在真实发包中生效（实测 513520.SH `choppy_highvol`×1.3、518880.SH `trending_highvol`×1.2）。

## 九、三阶段路径

- **阶段一（本轮已交付）**：多周期综合锚（几何均值）+ 稳定性 + 参考列表（含周频动态锚）；ATR 间距 + 制度四象限 + 成本下限；schema v2；回测评估器（fixed/weekly_dynamic/floating × 间距乘子 × 极端倍数，含 T+1/涨跌停/最低佣金/路径近似/敏感性）。
- **阶段二**：用评估器在 A 股 ETF 上实证（含 159915 创业板 20%）「固定 vs 周频动态 vs 浮动」，消融实验（动态锚/波动自适应/库存修正分开）、分状态滚动样本外（牛/熊/震荡）→ 证据决定是否升主锚；间距分位 p50/p75/p90 对比。
- **阶段三**：摆动点/斐波那契锚正式化（swing_mid 升主候选）、库存修正锚（`M*=M·exp(−γ·I)`）、ATR 逐日自适应、参数优化仪表盘（对比不同锚×间距回测）。

## 十、可解释性与诚实呈现（REFERENCE_ONLY）

- 每个建议值携带 `basis`/`spread_basis`/`cost_constraint_note` 计算依据；`confidence` 区分数据充足度。
- `approval_policy="REFERENCE_ONLY"` 永不自动 APPROVED；不改资产池/组合权重/回测配置。
- 数据不足（ADX 缺 high/low、swing 缺 200d 窗口、ATR 缺历史）→ 诚实降级 unknown/None/缺失，不把缺失当中性。
- 回测报告标注样本期、费用假设、日内路径近似局限；短样本最优参数不固化。
