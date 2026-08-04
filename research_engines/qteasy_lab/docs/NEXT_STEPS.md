# 后续任务

## P0：恢复全球宏观数据

1. 为 `fetch_global_macro_data.py` 增加 FRED 本地 CSV 导入。
2. 支持 `DGS10.csv`、`DGS2.csv`、`DGS30.csv`、`DFII10.csv`。
3. 对没有 `available_at` 的手动文件降低质量等级并记录警告。
4. 恢复或导入完整 FRED 历史数据。
5. 运行 `notebooks/global_macro_lab.ipynb`。
6. 人工审阅 SPY、TLT、GLD 条件收益表。

## P1：人工确认宏观规则（已完成）

1. ✅ 为当前宏观状态创建规则 `DRAFT`（12 条，覆盖全部有数据的资产×状态）。
2. ✅ 记录样本起止日期、样本数、置信度（五档离散 modifier + 样本联动）。
3. ✅ 样本 ≥60 且非中性的 5 条已人工确认 `APPROVED`；样本 <60 的保持 `DRAFT/CANDIDATE`。
4. ✅ 启用 SPY/TLT/GLD 研究配置（enabled=1, ENABLED）。
5. ✅ 运行 GlobalEtfEngine 生成真实数据评分（`data/global_etf_values/`）。
6. ✅ 检查 DGS30/DGS10 代理（rate_proxy=DGS30，无降级警告）、支持/冲突因子与数据质量。

### 待办（样本积累后）

- 利率状态（rate_up/rate_down）样本约 2027 年前后达到 60 个月，届时人工复核后再 APPROVED。
- 当前状态三元组因缺 rate_up/curve_normal 的 APPROVED 规则而返回 `PARTIAL`，属预期安全行为。

## P2：桌面端全球 ETF 页面（已完成）

新增 `GlobalEtfPage`（desktop/global_etf_page.py），展示：

- ✅ 数据状态和数据截至日期（各序列截至日 + 质量等级）；
- ✅ 当前宏观状态（宏观状态枚举 + 引擎状态）；
- ✅ DGS30/DGS10 代理及精度提示（利率代理 + 降级警告）；
- ✅ SPY/TLT/GLD 基础评分和宏观修正（评分表格）；
- ✅ 支持因子与冲突因子（评分表格列）；
- ✅ 条件收益表（Notebook 产出 CSV）；
- ✅ 研究规则状态（DRAFT/APPROVED 明细）；
- ✅ 重新计算（后台线程 worker）与查看 Notebook 结果。

页面接线进主窗口导航（"全球ETF宏观"），`data_root` 默认取 `store_root.parent/data`。

## P3：研究资产和交易资产映射

## P3：研究资产和交易资产映射

后续再增加：

- 研究资产与交易资产映射；
- 汇率差异；
- 管理费和交易成本；
- 跟踪误差和折溢价；
- 海外交易时差和休市风险。

仍不得自动生成交易指令或自动修改组合权重。
