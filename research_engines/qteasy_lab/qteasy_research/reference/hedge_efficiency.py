"""宏观对冲效率（B1-2）：四情景 × 每资产的月度收益对利率代理的条件相关。

long 格式输出，供系统A人工评估各资产在宏观压力情景（rate_up / rate_down /
curve_inverted / real_yield_up）下的对冲效率与同向/背离概率。

- ``macro_ret``：DGS30 月变化（TLT 代理）与 DFII10 月变化（实际利率），
  均取月末值 pct_change。
- ``hedge_efficiency = 1 - min(|conditional_corr|, 1)``：越接近 1 表示与
  利率压力背离越好（对冲有效）。
- **降级策略**：``sample_count < 5`` 数值列置空 ``NaN``（parquet 写 null，与 CSV 的
  ``""`` 语义区分）+ ``confidence="low"``；``macro_unavailable`` 月份不参与
  （``build_monthly_scenario_table`` 已标记）。
机器输出零中文策略名。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from qteasy_research.reference.macro_scenarios import scenario_monthly_returns

# parquet 空值用 NaN（pyarrow 可写 null）；CSV 侧（grid）才用空字符串。
_EMPTY = np.nan
# 最小样本数：低于该值降级为 low（审核资料 5.2.2）。
_MIN_SAMPLES = 5

# 四情景 → 使用的利率代理序列（rate 系用 DGS30，real_yield 系用 DFII10）。
_SCENARIO_MACRO: dict[str, str] = {
    "rate_up": "DGS30",
    "rate_down": "DGS30",
    "curve_inverted": "DGS30",
    "real_yield_up": "DFII10",
}
_SCENARIOS: tuple[str, ...] = ("rate_up", "rate_down", "curve_inverted", "real_yield_up")

_COLUMNS = [
    "asset_id",
    "scenario",
    "sample_count",
    "conditional_corr",
    "hedge_efficiency",
    "stress_down_probability",
    "co_movement_probability",
    "avg_monthly_pnl_pct",
    "confidence",
    "data_quality",
]


def _monthly_change(path: Path) -> pd.DataFrame:
    """读宏观序列（observation_date/value），返回月末值 pct_change 的 ``month, macro_ret``。

    缺失文件 / 缺列 / 空数据 → 空 DataFrame（不崩溃，参与行自然被剔除）。
    """
    if not path.exists():
        return pd.DataFrame(columns=["month", "macro_ret"])
    frame = pd.read_csv(path)
    if not {"observation_date", "value"}.issubset(frame.columns):
        return pd.DataFrame(columns=["month", "macro_ret"])
    frame["observation_date"] = pd.to_datetime(frame["observation_date"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna(subset=["observation_date", "value"])
    if frame.empty:
        return pd.DataFrame(columns=["month", "macro_ret"])
    monthly = frame.set_index("observation_date")["value"].resample("ME").last()
    change = monthly.pct_change().dropna()
    return pd.DataFrame({"month": change.index.strftime("%Y-%m-%d"), "macro_ret": change.values})


def _quality_of(frame: pd.DataFrame) -> str:
    """数据质量等级：本地→A，在线补齐→B，空帧→D（与 report_pool_gaps 对齐）。"""
    if frame.empty:
        return "D"
    if "source" in frame.columns and frame["source"].iloc[-1] == "online":
        return "B"
    return "A"


def _asset_monthly(frame: pd.DataFrame, macro_table: pd.DataFrame) -> pd.DataFrame:
    """资产月收益与场景对齐，并剔除 ``macro_unavailable`` 月份。"""
    merged = scenario_monthly_returns(frame, macro_table)
    if merged.empty or macro_table.empty:
        return merged
    unavailable = set(
        macro_table.loc[macro_table["macro_unavailable"] == True, "month"].astype(str)  # noqa: E712
    )
    return merged.loc[~merged["month"].astype(str).isin(unavailable)].reset_index(drop=True)


def build_hedge_efficiency(
    data_root: str | Path,
    assets: pd.DataFrame,
    aligned: dict[str, pd.DataFrame],
    macro_table: pd.DataFrame,
) -> pd.DataFrame:
    """构建宏观对冲效率表（long 格式，四情景 × 每资产一行）。

    参数：
        data_root: B 本地数据目录（读 ``processed/global_macro/DGS30.csv`` 等）。
        assets: 资产池 DataFrame（含 asset_id），来自 ``read_active_assets``。
        aligned: ``{asset_id: DataFrame(trade_date, close, …)}`` 对齐行情。
        macro_table: ``build_monthly_scenario_table()`` 输出的场景表。

    返回列：
        ``asset_id``、``scenario``、``sample_count``、``conditional_corr``、
        ``hedge_efficiency``、``stress_down_probability``、
        ``co_movement_probability``、``avg_monthly_pnl_pct``、
        ``confidence``、``data_quality``。
    """
    if assets.empty or macro_table.empty:
        return pd.DataFrame(columns=_COLUMNS)
    root = Path(data_root)
    macro_frames = {
        name: _monthly_change(root / "processed" / "global_macro" / f"{name}.csv")
        for name in set(_SCENARIO_MACRO.values())
    }
    rows: list[dict[str, object]] = []
    for _, asset_row in assets.iterrows():
        asset_id = str(asset_row["asset_id"]).strip()
        frame = aligned.get(asset_id, pd.DataFrame())
        data_quality = _quality_of(frame)
        monthly = _asset_monthly(frame, macro_table)
        for scenario in _SCENARIOS:
            rows.append(
                _scenario_row(
                    asset_id,
                    scenario,
                    monthly,
                    macro_frames[_SCENARIO_MACRO[scenario]],
                    data_quality,
                )
            )
    return pd.DataFrame(rows, columns=_COLUMNS)


def _scenario_row(
    asset_id: str,
    scenario: str,
    monthly: pd.DataFrame,
    macro_change: pd.DataFrame,
    data_quality: str,
) -> dict[str, object]:
    """单情景行：默认全空 + low，样本足够才填数值。"""
    base: dict[str, object] = {
        "asset_id": asset_id,
        "scenario": scenario,
        "sample_count": 0,
        "conditional_corr": _EMPTY,
        "hedge_efficiency": _EMPTY,
        "stress_down_probability": _EMPTY,
        "co_movement_probability": _EMPTY,
        "avg_monthly_pnl_pct": _EMPTY,
        "confidence": "low",
        "data_quality": data_quality,
    }
    if monthly.empty or macro_change.empty:
        return base
    # 仅保留包含该情景的月份（states 为该月宏观状态列表）。
    has_scenario = monthly["states"].apply(lambda states: scenario in (states or []))
    subset = monthly.loc[has_scenario].copy()
    if subset.empty:
        return base
    merged = subset.merge(macro_change, on="month", how="inner")
    sample_count = len(merged)
    base["sample_count"] = sample_count
    if sample_count < _MIN_SAMPLES:
        return base
    asset_ret = pd.to_numeric(merged["asset_return"], errors="coerce").astype(float)
    macro_ret = pd.to_numeric(merged["macro_ret"], errors="coerce").astype(float)
    if macro_ret.std(ddof=0) == 0:
        # 利率代理样本内无变化，相关性无法定义 → 降级。
        return base
    corr = float(asset_ret.corr(macro_ret))
    if not np.isfinite(corr):
        return base
    base["conditional_corr"] = round(corr, 4)
    base["hedge_efficiency"] = round(1.0 - min(abs(corr), 1.0), 4)
    base["stress_down_probability"] = round(float((macro_ret < 0).mean()), 4)
    co_movement = float((np.sign(asset_ret) * np.sign(macro_ret) > 0).mean())
    base["co_movement_probability"] = round(co_movement, 4)
    base["avg_monthly_pnl_pct"] = round(float(asset_ret.mean()) * 100.0, 4)
    base["confidence"] = "high"
    return base
