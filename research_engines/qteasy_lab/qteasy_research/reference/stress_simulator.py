"""压力模拟器（阶段三）：宏观压力情景的历史回放损益。

对每资产，筛历史上真实发生压力情景的月份（历史压力期回放），输出这些月份
该资产的**真实月均收益**（``pnl_pct``）与对利率代理的条件相关性（``corr``），
填充 ``schema.AssetDimensions.macro_stress``（字段已定义，阶段三首次有值）。

**5 个情景**（比 B1-2 对冲效率的 4 情景多一个**滞胀组合**）：
- ``rate_up_50bp``：DGS30 月变化 ≥ +0.50（50bp 强加息，比 rate_up 的 +20bp 更严）
- ``rate_down_50bp``：DGS30 月变化 ≤ -0.50
- ``curve_inverted``：状态含 curve_inverted（期限倒挂）
- ``real_yield_up``：状态含 real_yield_up（实际利率上行，DFII10 月变化 ≥ +0.10）
- ``stagnation``：状态同时含 rate_up **且** real_yield_up（滞胀：利率上行 + 通胀
  上行 → 实际利率上行，本项目首次引入的组合压力情景）

**降级链**：空行情 / 空宏观表 → 不产出；情景无匹配月份 → ``sample_count=0``、
``pnl_pct=None`` + ``confidence="low"``（不虚构压力损益，延续"不把缺失当作中性"）；
``sample_count < STRESS_MIN_SAMPLES`` → 数值置 None + low（与 hedge_efficiency 对齐）。
机器输出零中文策略名。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from qteasy_research.reference.config import (
    STRESS_MIN_SAMPLES,
    STRESS_RATE_DOWN_BP,
    STRESS_RATE_UP_BP,
    STRESS_REAL_YIELD_UP_BP,
)
from qteasy_research.reference.macro_scenarios import scenario_monthly_returns

# 无值用 np.nan（与 hedge_efficiency 一致，JSON 序列化为 null）。
_EMPTY = np.nan

_MACRO_DIR = ("processed", "global_macro")
_DGS30 = "DGS30"
_DFII10 = "DFII10"


def _macro_abs_change(root: Path, series_id: str) -> pd.DataFrame:
    """读宏观序列月末**绝对差**（``month, macro_diff``）。

    注意与 hedge_efficiency._monthly_change（用 pct_change 相对变化）区分：
    压力情景的幅度阈值（如 ±50bp）是利率的绝对变化，须用 ``diff()``
    （与 ``_macro_state`` 的 rate_up 判定一致：绝对差 ≥ +0.20 即 20bp）。
    文件缺失 / 缺列 / 空数据 → 空 DataFrame（不崩溃）。
    """
    path = root.joinpath(*_MACRO_DIR, f"{series_id}.csv")
    if not path.exists():
        return pd.DataFrame(columns=["month", "macro_diff"])
    frame = pd.read_csv(path)
    if not {"observation_date", "value"}.issubset(frame.columns):
        return pd.DataFrame(columns=["month", "macro_diff"])
    frame["observation_date"] = pd.to_datetime(frame["observation_date"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna(subset=["observation_date", "value"])
    if frame.empty:
        return pd.DataFrame(columns=["month", "macro_diff"])
    monthly = frame.set_index("observation_date")["value"].resample("ME").last()
    diff = monthly.diff().dropna()
    return pd.DataFrame({"month": diff.index.strftime("%Y-%m-%d"), "macro_diff": diff.values})


def _state_mask(states: Any, required: tuple[str, ...]) -> bool:
    """states 列表是否同时含 required 全部状态（防御 NaN/非列表）。"""
    return isinstance(states, list) and all(state in states for state in required)


def _scenario_stats(
    monthly: pd.DataFrame,
    macro_change: pd.DataFrame,
    scenario: str,
    basis: str,
    *,
    states_needed: tuple[str, ...] | None = None,
    threshold: float | None = None,
    threshold_dir: str = "ge",
) -> dict[str, Any]:
    """单情景历史回放统计。

    - ``states_needed`` 非 None：按 states 组合筛选（curve_inverted / real_yield_up /
      stagnation）；否则按 ``macro_diff`` 绝对差阈值筛选（rate_up_50bp / rate_down_50bp）。
    - ``threshold_dir``：阈值比较方向，"ge" 为 ≥（rate_up_50bp），"le" 为 ≤
      （rate_down_50bp）。默认 "ge"。
    - ``basis``：corr 使用的利率代理依据（DGS30 / DFII10）。
    """
    base = {
        "pnl_pct": None,
        "sample_count": 0,
        "corr": None,
        "confidence": "low",
        "basis": basis,
    }
    if monthly.empty or macro_change.empty:
        return base
    if states_needed is not None:
        subset = monthly.loc[
            monthly["states"].apply(lambda s: _state_mask(s, states_needed))
        ].copy()
    else:
        subset = monthly.copy()
    merged = subset.merge(macro_change, on="month", how="inner")
    if threshold is not None:
        macro_diff_col = merged["macro_diff"]
        if threshold_dir == "le":
            merged = merged.loc[macro_diff_col <= threshold]
        else:
            merged = merged.loc[macro_diff_col >= threshold]
    sample_count = len(merged)
    base["sample_count"] = sample_count
    if sample_count < STRESS_MIN_SAMPLES:
        # 样本不足：不虚构压力损益。
        return base
    asset_ret = pd.to_numeric(merged["asset_return"], errors="coerce").astype(float)
    macro_diff = pd.to_numeric(merged["macro_diff"], errors="coerce").astype(float)
    base["pnl_pct"] = round(float(asset_ret.mean()) * 100.0, 4)
    base["confidence"] = (
        "high" if sample_count >= 12 else ("medium" if sample_count >= 6 else "low")
    )
    if sample_count >= 2 and macro_diff.std(ddof=0) > 0:
        corr = float(asset_ret.corr(macro_diff))
        base["corr"] = round(corr, 4) if np.isfinite(corr) else None
    return base


def _asset_stress_map(
    frame: pd.DataFrame,
    macro_table: pd.DataFrame,
    dgs30: pd.DataFrame,
    dfii10: pd.DataFrame,
    *,
    rate_up_bp: float,
    rate_down_bp: float,
    real_yield_up_bp: float,
) -> dict[str, dict[str, Any]]:
    """单资产的 5 情景压力损益。空行情 → 空 dict。"""
    if frame.empty or macro_table.empty:
        return {}
    monthly = scenario_monthly_returns(frame, macro_table)
    if monthly.empty:
        return {}
    spec: list[tuple[str, str, dict[str, Any]]] = [
        ("rate_up_50bp", _DGS30, {"threshold": rate_up_bp}),
        ("rate_down_50bp", _DGS30, {"threshold": rate_down_bp, "threshold_dir": "le"}),
        ("curve_inverted", _DGS30, {"states_needed": ("curve_inverted",)}),
        ("real_yield_up", _DFII10, {"states_needed": ("real_yield_up",)}),
        # 滞胀：利率上行 + 实际利率上行同时发生。
        ("stagnation", _DFII10, {"states_needed": ("rate_up", "real_yield_up")}),
    ]
    result: dict[str, dict[str, Any]] = {}
    for scenario, basis, kwargs in spec:
        macro_change = dgs30 if basis == _DGS30 else dfii10
        result[scenario] = _scenario_stats(
            monthly, macro_change, scenario, basis, **kwargs
        )
    return result


def build_stress_simulator(
    assets: pd.DataFrame,
    aligned: dict[str, pd.DataFrame],
    macro_table: pd.DataFrame,
    data_root: str | Path,
    *,
    rate_up_bp: float = STRESS_RATE_UP_BP,
    rate_down_bp: float = STRESS_RATE_DOWN_BP,
    real_yield_up_bp: float = STRESS_REAL_YIELD_UP_BP,
) -> dict[str, dict[str, dict[str, Any]]]:
    """逐资产历史压力期回放损益。

    参数：
        assets: 资产池 DataFrame（含 asset_id），来自 ``read_active_assets``。
        aligned: ``{asset_id: DataFrame(trade_date, close, …)}`` 对齐行情。
        macro_table: ``build_monthly_scenario_table()`` 输出场景表。
        data_root: B 本地数据目录（读 ``processed/global_macro/DGS30.csv`` 等）。

    返回：
        ``{asset_id: {scenario: {"pnl_pct", "sample_count", "corr",
        "confidence", "basis"}}}``。无风险 / 数据不足资产不产出。
    """
    root = Path(data_root)
    dgs30 = _macro_abs_change(root, _DGS30)
    dfii10 = _macro_abs_change(root, _DFII10)
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for _, row in assets.iterrows():
        asset_id = str(row["asset_id"]).strip()
        stress = _asset_stress_map(
            aligned.get(asset_id, pd.DataFrame()),
            macro_table,
            dgs30,
            dfii10,
            rate_up_bp=rate_up_bp,
            rate_down_bp=rate_down_bp,
            real_yield_up_bp=real_yield_up_bp,
        )
        if stress:
            result[asset_id] = stress
    return result


__all__ = ["build_stress_simulator"]
