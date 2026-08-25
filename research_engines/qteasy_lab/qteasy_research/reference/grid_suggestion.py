"""网格建议引擎（P1-B）：适合度 / 中轴 / 两段步长 / 相关性配置建议。

对每个启用标的输出四类**仅供人工参考**的网格配置建议
（``approval_policy="REFERENCE_ONLY"``）：永不自动 APPROVED、不改资产池 /
组合权重 / 回测配置。

计算口径唯一源 = ``contract.shared_config.grid``（A 侧 ``strategy_params.json``
新增 ``"grid"`` 段，经契约 ``shared_config.grid`` 透传到 B）；缺省用代码常量
默认值（``_DEFAULT_GRID_PARAMS``）。

四类建议：
1. suitability（适合度）：60d 窗口四分量（vol_rank / amplitude / drift /
   trigger_freq）加权总分 → suitable / marginal / not_suitable。
2. anchor_suggestion（中轴）：60d VWAP（有 volume）→ SMA60（无量）→
   60d 高低中点；basis 记录来源。
3. 两段步长：regular_spread = suggested_reference_spread；edge_spread =
   regular × max(默认乘子, cone_60_p95/p50)，上限 4×regular。
4. correlation：每策略启用标的集合 90d 日收益两两相关，输出 high_corr_pairs /
   avg_corr / redundancy_note。

机器产出全 ASCII，零中文策略名。
"""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pandas as pd

from qteasy_research.reference.config import (
    CORR_HIGH_THRESHOLD,
    CORR_MODERATE_THRESHOLD,
    EDGE_SPREAD_MULTIPLIER_DEFAULT,
    EDGE_SPREAD_MAX_MULTIPLE,
    GRID_SUGGESTION_SCHEMA_VERSION,
    MARGINAL,
    SUITABLE,
)
from qteasy_research.reference.grid_metrics import (
    adx,
    anchor_stability,
    atr_pct,
    ema_series,
    fib_levels,
    market_regime,
    swing_levels,
    weekly_dynamic_anchor,
)
from qteasy_research.reference.metadata import today_iso
from qteasy_research.reference.param_sweep import _grid_triggers
from qteasy_research.reference.portfolio_analysis import correlation_matrix
from qteasy_research.reference.volatility_cone import (
    current_vol_rank,
    suggest_reference_spread,
)

# 年化系数（日频），与 volatility_cone._TRADING_DAYS / param_sweep 一致。
_TRADING_DAYS = 252.0
# trigger_freq 回放所需最小历史天数（与 param_sweep._MIN_HISTORY 一致）。
_MIN_HISTORY = 20

# 缺省网格建议参数（contract.shared_config.grid 覆盖；缺失用代码默认值）。
_DEFAULT_GRID_PARAMS: dict[str, Any] = {
    # suitability 权重（四分量）。
    "suitability_weights": {"vol_rank": 0.30, "amplitude": 0.20, "drift": 0.30, "trigger_freq": 0.20},
    # vol_rank 目标分位（0.6 附近给满分）。
    "vol_rank_target": 0.6,
    # trigger_freq 目标带 [low, high]；[0,low) 与 (high, max] 线性衰减，超过 max 记 0。
    "trigger_freq_low": 8.0,
    "trigger_freq_high": 60.0,
    "trigger_freq_max": 120.0,
    # amplitude 饱和系数：mean_daily_range / suggested_spread 达该值 → amp_score=100。
    "amp_score_saturation": 0.8,
    # 两段步长：edge_spread = regular × max(multiplier, cone_p95/p50)，上限 max_multiple×regular。
    "edge_spread_multiplier": EDGE_SPREAD_MULTIPLIER_DEFAULT,
    "edge_spread_max_multiple": EDGE_SPREAD_MAX_MULTIPLE,
    # 档数：常规段 3 档、边缘段 1 档；vol_rank 超阈时边缘档升 2。
    "regular_levels_per_side": 3,
    "edge_levels_per_side": 1,
    "vol_rank_edge_threshold": 0.8,
    "edge_levels_high_vol": 2,
    # 间距参考三元组（B1）：default = clamp(atr20_pct × spacing_multiplier,
    # spacing_floor, spacing_cap)；min=default×0.7、max=default×1.5。
    "spacing_multiplier": 1.5,
    "spacing_floor": 0.025,
    "spacing_cap": 0.05,
    # 锚点升级：多周期综合主锚（几何均值）+ 参考列表 + 稳定性评分。
    "anchor_windows": (20, 60, 90, 120),  # 各窗口锚点（vwap→sma→mid 优先级）
    "min_anchor_days": 10,                # 单窗口最少历史天数（不足跳过该窗口）
    "ema_anchor_span": 120,               # dynamic_ema 参考锚 EMA 跨度
    "swing_window": 200,                  # swing_mid 摆动点回看 K 线数
    "weekly_anchor_lookback": 20,         # weekly_dynamic 参考锚回看交易日数
    # 间距成本硬约束：cost_floor = max(2×fee_rate + 2×min_commission/amount_per_grid
    # + slippage_buffer, spread_floor_min)；clamp 后低于下限提到下限并标注生效。
    "min_commission": 5.0,       # 单笔最低佣金（元）
    "slippage_buffer": 0.0003,   # 滑点缓冲（价格比例）
    "spread_floor_min": 0.0015,  # 间距绝对下限（0.15%）
    # 相关性。
    "corr_high_threshold": CORR_HIGH_THRESHOLD,
    "corr_moderate_threshold": CORR_MODERATE_THRESHOLD,
    "corr_min_overlap": 60,
    "corr_window_days": 90,
    # 适合度/中轴窗口。
    "window_days": 60,
    # 分级阈值。
    "suitable": SUITABLE,
    "marginal": MARGINAL,
    # 理论收益（REFERENCE_ONLY 补充参考；短期 amount_per_grid 默认，
    # 长期参数化列待办，衔接资金管理大模块 P0）。
    "fee_rate": 0.0005,          # 单边费率（往返双边 = 2×fee_rate）
    "amount_per_grid": 6000.0,   # 单份金额
    "theory_max_w": 1.0,         # K 库路径长度模型系数 w
    # 网格占用本金口径：满仓档数（与 grid_recommendation 推荐包 9 档全占一致），
    # theory_max 金额 = 0.8×w×σ×n_annual×(amount_per_grid×levels_total)。
    "levels_total": 9,
}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _grid_params(shared_grid: dict | None) -> dict[str, Any]:
    """合并 ``contract.shared_config.grid`` 与代码默认值（文件值为准）。"""
    params = {k: (dict(v) if isinstance(v, dict) else v) for k, v in _DEFAULT_GRID_PARAMS.items()}
    if isinstance(shared_grid, dict):
        weights = shared_grid.get("suitability_weights")
        if isinstance(weights, dict):
            params["suitability_weights"] = {**params["suitability_weights"], **weights}
        for key, value in shared_grid.items():
            if key == "suitability_weights":
                continue
            if value is not None:
                params[key] = value
    return params


def _grid_reference_index(grid_reference: Any) -> dict[str, dict[str, Any]]:
    """把 grid_reference 归一化为 ``{asset_id: 行 dict}``。

    支持 DataFrame（grid_reference_table）或 ``{asset_id: 值/行 dict}``；
    缺失 → 空 dict（调用方从行情自行计算）。
    """
    result: dict[str, dict[str, Any]] = {}
    if grid_reference is None:
        return result
    if isinstance(grid_reference, pd.DataFrame):
        if "asset_id" not in grid_reference.columns:
            return result
        for _, row in grid_reference.iterrows():
            result[str(row["asset_id"]).strip()] = row.to_dict()
        return result
    if isinstance(grid_reference, dict):
        for asset_id, value in grid_reference.items():
            if isinstance(value, dict):
                result[str(asset_id).strip()] = dict(value)
            elif value is not None:
                result[str(asset_id).strip()] = {"suggested_reference_spread": value}
    return result


def _asset_window(
    frame: pd.DataFrame,
    window_days: int,
    data_asof: str | None,
) -> pd.DataFrame:
    """取截止 data_asof 的最近 window_days 个交易日（升序）。缺失/空 → 空帧。"""
    if frame is None or frame.empty or "close" not in frame.columns:
        return pd.DataFrame()
    sub = frame.copy()
    sub["trade_date"] = pd.to_datetime(sub["trade_date"], errors="coerce")
    for column in ("close", "high", "low", "open", "vol", "volume", "amount"):
        if column in sub.columns:
            sub[column] = pd.to_numeric(sub[column], errors="coerce")
    sub = sub.dropna(subset=["trade_date", "close"])
    sub = sub[sub["close"] > 0].sort_values("trade_date")
    if data_asof:
        try:
            asof = pd.Timestamp(data_asof)
            sub = sub[sub["trade_date"] <= asof]
        except Exception:
            pass
    return sub.tail(window_days).reset_index(drop=True)


def _asset_returns(frame: pd.DataFrame) -> pd.Series:
    if frame is None or frame.empty or "close" not in frame.columns:
        return pd.Series(dtype=float)
    return frame["close"].pct_change().replace([np.inf, -np.inf], np.nan).dropna()


def _vol_60(frame: pd.DataFrame) -> tuple[float | None, float | None]:
    """当前 60d 年化波动率与 vol_rank；历史不足 → (None, None)。"""
    returns = _asset_returns(frame)
    if returns.empty:
        return None, None
    vol = returns.rolling(60, min_periods=max(60 // 2, 2)).std()
    if vol.empty or pd.isna(vol.iloc[-1]):
        return None, None
    annualized = float(vol.iloc[-1]) * np.sqrt(_TRADING_DAYS)
    rank = current_vol_rank(returns, 60)
    return annualized, rank


def _daily_range(frame: pd.DataFrame) -> float | None:
    """日均真实波幅：mean((high-low)/close)；无 high/low 用 mean(|close/open-1|)；
    再无 open 用 mean(|daily_ret|)（B侧假设，缺失不虚构为 0）。"""
    if frame is None or frame.empty or "close" not in frame.columns:
        return None
    close = frame["close"]
    if "high" in frame.columns and "low" in frame.columns:
        hi = frame["high"]
        lo = frame["low"]
        mask = close.notna() & hi.notna() & lo.notna() & close.gt(0)
        if mask.any():
            return float(((hi[mask] - lo[mask]) / close[mask]).mean())
    if "open" in frame.columns:
        opn = frame["open"]
        mask = close.notna() & opn.notna() & opn.gt(0)
        if mask.any():
            return float((close[mask] / opn[mask] - 1.0).abs().mean())
    returns = _asset_returns(frame)
    if not returns.empty:
        return float(returns.abs().mean())
    return None


def _cost_floor(params: dict[str, Any]) -> float:
    """成本硬约束下限（V2）：``max(2×fee_rate + 2×min_commission/amount_per_grid
    + slippage_buffer, spread_floor_min)``（PDF 公式 g_min ≈ 2c_v + 2F/(Q·P) + 2s + buffer）。

    低价 ETF / 较小资金量 / 过小订单会把最低佣金占比（2F/(Q·P)）推高，
    成本下限随之提高。
    """
    fee = float(params.get("fee_rate", 0.0005))
    min_comm = float(params.get("min_commission", 5.0))
    notional = float(params.get("amount_per_grid", 6000.0))
    buffer = float(params.get("slippage_buffer", 0.0003))
    floor_min = float(params.get("spread_floor_min", 0.0015))
    comm_ratio = (2.0 * min_comm / notional) if notional > 0 else 0.0
    return max(2.0 * fee + comm_ratio + buffer, floor_min)


def _spread_suggestion(
    frame: pd.DataFrame,
    params: dict[str, Any],
) -> dict[str, Any]:
    """ATR 主公式 + 四象限制度调节 + 成本硬约束（V2）。

    ``spread = clamp(atr20_pct × spacing_multiplier × regime_mult, cost_floor, spacing_cap)``；
    计算值低于 cost_floor 时提到下限，并 ``cost_constraint_applied=True``。
    波动率法（volatility_cone，旧口径）保留为 ``spread_alternatives.volatility_method``。
    历史不足（ATR 不可算）→ spread=None + confidence 降级（缺失不虚构）。
    """
    empty = {
        "spread": None, "basis": None, "regime": "unknown", "adx": None,
        "atr_pct": None, "cost_floor": None, "cost_constraint_applied": False,
        "cost_constraint_note": None, "alternatives": {},
    }
    if frame is None or frame.empty or "close" not in frame.columns:
        return empty
    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    high = pd.to_numeric(frame["high"], errors="coerce").dropna() if "high" in frame.columns else None
    low = pd.to_numeric(frame["low"], errors="coerce").dropna() if "low" in frame.columns else None
    atr = atr_pct(close, high, low, period=20)
    adx14 = adx(high, low, close, period=14) if high is not None and low is not None else None
    regime, regime_mult = market_regime(adx14, atr)
    cost_floor = _cost_floor(params)
    cap = float(params.get("spacing_cap", 0.05))
    multiplier = float(params.get("spacing_multiplier", 1.5))
    if atr is None:
        return {
            **empty,
            "cost_floor": round(cost_floor, 4),
            "adx": round(adx14, 2) if adx14 is not None else None,
        }
    raw = atr * multiplier * (regime_mult if regime_mult is not None else 1.0)
    spread = _clamp(raw, cost_floor, cap)
    cost_applied = raw < cost_floor
    vol_60, rank_60 = _vol_60(frame)
    alt_vol = suggest_reference_spread(vol_60, rank_60)
    return {
        "spread": round(spread, 4),
        "basis": "atr20_regime_cost_floor" if cost_applied else "atr20_regime",
        "regime": regime,
        "adx": round(adx14, 2) if adx14 is not None else None,
        "atr_pct": round(atr, 6),
        "cost_floor": round(cost_floor, 4),
        "cost_constraint_applied": cost_applied,
        "cost_constraint_note": f"cost_floor={round(cost_floor, 4)}" if cost_applied else None,
        "alternatives": {"volatility_method": alt_vol},
    }


def _suggested_spread(
    frame: pd.DataFrame,
    grid_row: dict[str, Any],
    params: dict[str, Any],
) -> float | None:
    """regular_spread 兼容入口（float）：返回 ATR 主公式建议值。

    V2 主公式不再依赖 grid_reference 的 ``suggested_reference_spread``
    （该旧口径降级为 ``spread_alternatives.volatility_method``）。
    """
    del grid_row
    return _spread_suggestion(frame, params)["spread"]


def _spacing_reference(
    frame: pd.DataFrame,
    grid_row: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    """间距参考三元组 default/min/max（V2，随日更，基于 ATR 主公式）。

    ``default`` = ATR 主公式建议值；``min = max(default×0.7, cost_floor)``（成本
    硬约束兜底）；``max = default×1.5``（破网上限）。历史不足（ATR 不可算）
    → 三元组 None + ``confidence="low"``。
    """
    del grid_row
    info = _spread_suggestion(frame, params)
    default = info["spread"]
    if default is None:
        return {"default": None, "min": None, "max": None, "basis": None, "confidence": "low"}
    min_spread = round(float(default) * 0.7, 4)
    if info.get("cost_floor"):
        min_spread = max(min_spread, round(float(info["cost_floor"]), 4))
    return {
        "default": round(float(default), 4),
        "min": round(float(min_spread), 4),
        "max": round(float(default) * 1.5, 4),
        "basis": info["basis"],
        "confidence": "high",
    }


def _trigger_freq_score(annualized: float, params: dict[str, Any]) -> float:
    """[low, high]→100；[0,low) 与 (high,max] 线性衰减；超过 max → 0。"""
    low = float(params["trigger_freq_low"])
    high = float(params["trigger_freq_high"])
    max_freq = float(params["trigger_freq_max"])
    if low <= annualized <= high:
        return 100.0
    if annualized < low:
        return _clamp(100.0 * annualized / low, 0.0, 100.0)
    if annualized >= max_freq:
        return 0.0
    return _clamp(100.0 * (max_freq - annualized) / (max_freq - high), 0.0, 100.0)


def _annualized_single_side_triggers(frame: pd.DataFrame, spread: float) -> float:
    """年化单边网格触发次数（与 suitability.trigger_freq 同口径）。

    回放间距 ``spread`` 网格统计档位变化次数（买或卖，单边），折算年化；
    历史不足 / spread 非法 → 0.0（与历史行为一致，不改变 suitability 结果）。
    抽公共函数供理论收益模块复用，避免两处口径漂移。
    """
    if frame is None or frame.empty or "close" not in frame.columns:
        return 0.0
    close = frame["close"]
    n = len(close)
    years = n / _TRADING_DAYS
    if years <= 0 or spread is None or spread <= 0:
        return 0.0
    return float(_grid_triggers(close, spread) / years)


# ---------------------------------------------------------------------------
# suitability（适合度）
# ---------------------------------------------------------------------------

def _compute_suitability(
    frame: pd.DataFrame,
    grid_row: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    """四分量 + 加权总分 + 分级。缺失分量不虚构：None 分量剔除后按剩余权重重归一。"""
    returns = _asset_returns(frame)
    n = len(frame)
    years = n / _TRADING_DAYS if n > 0 else 0.0

    # 1) vol_rank
    vol_rank = _to_float(grid_row.get("vol_rank_60d"))
    if vol_rank is None:
        vol_rank = current_vol_rank(returns, params["window_days"]) if len(returns) > 1 else None
    if vol_rank is None:
        vol_rank_score: float | None = None
    else:
        vol_rank_score = _clamp(
            100.0 - 100.0 * abs(vol_rank - float(params["vol_rank_target"])) / 0.5,
            0.0,
            100.0,
        )

    # 2) amplitude
    mean_daily_range = _daily_range(frame)
    suggested = _suggested_spread(frame, grid_row, params)
    if mean_daily_range is None or suggested is None or suggested <= 0:
        amp_score: float | None = None
    else:
        amp_score = _clamp(
            100.0 * (mean_daily_range / suggested) / float(params["amp_score_saturation"]),
            0.0,
            100.0,
        )

    # 3) drift
    if returns.empty or float(returns.std()) <= 0:
        drift_score: float | None = None
    else:
        drift = abs(float(returns.mean())) / float(returns.std())
        drift_score = _clamp(100.0 - 250.0 * drift, 0.0, 100.0)

    # 4) trigger_freq
    if suggested is not None and suggested > 0 and n >= _MIN_HISTORY and years > 0:
        annualized = _annualized_single_side_triggers(frame, suggested)
        trigger_score: float | None = _trigger_freq_score(annualized, params)
    else:
        trigger_score = None

    weights = params["suitability_weights"]
    pairs = [
        (vol_rank_score, float(weights["vol_rank"])),
        (amp_score, float(weights["amplitude"])),
        (drift_score, float(weights["drift"])),
        (trigger_score, float(weights["trigger_freq"])),
    ]
    available = [(s, w) for s, w in pairs if s is not None]
    total_weight = sum(w for _, w in available)
    if total_weight > 0:
        score = sum(s * w for s, w in available) / total_weight
    else:
        score = None

    if score is None:
        grade = "not_suitable"
    elif score >= float(params["suitable"]):
        grade = "suitable"
    elif score >= float(params["marginal"]):
        grade = "marginal"
    else:
        grade = "not_suitable"

    return {
        "score": round(score, 2) if score is not None else None,
        "grade": grade,
        "breakdown": {
            "vol_rank_score": round(vol_rank_score, 2) if vol_rank_score is not None else None,
            "amplitude_score": round(amp_score, 2) if amp_score is not None else None,
            "drift_score": round(drift_score, 2) if drift_score is not None else None,
            "trigger_freq_score": round(trigger_score, 2) if trigger_score is not None else None,
        },
    }


# ---------------------------------------------------------------------------
# anchor_suggestion（中轴）
# ---------------------------------------------------------------------------

def _window_anchor(sub: pd.DataFrame) -> tuple[float | None, str | None]:
    """单窗口锚点：VWAP（有量）→ SMA（无量）→ 高低中点（与旧 _anchor_suggestion 同优先级）。"""
    if sub is None or sub.empty or "close" not in sub.columns:
        return None, None
    close = sub["close"]
    vol_col = next((c for c in ("vol", "volume") if c in sub.columns), None)
    if vol_col is not None:
        volume = pd.to_numeric(sub[vol_col], errors="coerce")
        mask = volume.notna() & volume.gt(0) & close.notna()
        if mask.any():
            return float((close[mask] * volume[mask]).sum() / float(volume[mask].sum())), "vwap"
    if len(close) > 0:
        return float(close.mean()), "sma"
    if "high" in sub.columns and "low" in sub.columns:
        hi = pd.to_numeric(sub["high"], errors="coerce").dropna()
        lo = pd.to_numeric(sub["low"], errors="coerce").dropna()
        if len(hi) > 0 and len(lo) > 0:
            return float((hi.max() + lo.min()) / 2), "mid"
    return None, None


def _anchor_suggestion(
    frame: pd.DataFrame,
    params: dict[str, Any],
) -> dict[str, Any]:
    """多周期综合主锚（V2）+ 各窗口来源 + 稳定性评分 + 参考锚点列表。

    - 主锚 = 各窗口（20/60/90/120）锚点值的**几何均值**（几何均值适合相对价格
      网格）；每窗口优先级 VWAP（有量）→ SMA → 高低中点；至少 3 个有效窗口
      才出主锚，否则 None + confidence 降级。
    - ``anchor_sources``: {method: value} 各窗口来源值。
    - ``anchor_stability``: 变异系数评分（anchor_stability）。
    - ``anchor_references``: weekly_dynamic（周频动态几何均值）/ dynamic_ema
      （EMA120 现值，静态近似；成交硬重置语义在回测评估器中表达）/
      swing_mid（200K 摆动中点）/ fib_mid_618（最近摆动 0.618 回撤）。
    """
    empty = {"anchor": None, "basis": None, "sources": {}, "stability": None, "references": []}
    if frame is None or frame.empty or "close" not in frame.columns:
        return empty
    windows = tuple(int(w) for w in params.get("anchor_windows", (20, 60, 90, 120)))
    min_days = int(params.get("min_anchor_days", 10))
    sources: dict[str, float] = {}
    for w in windows:
        sub = frame.tail(w)
        if len(sub) < min_days:
            continue
        value, kind = _window_anchor(sub)
        if value is None:
            continue
        sources[f"{kind}_{w}"] = round(float(value), 3)
    anchor: float | None = None
    basis: str | None = None
    if len(sources) >= 3:
        logs = [math.log(v) for v in sources.values() if v > 0]
        if len(logs) >= 3:
            anchor = round(float(math.exp(sum(logs) / len(logs))), 3)
            basis = f"geomean_of_{len(logs)}_sources"
    stability = anchor_stability(list(sources.values()))
    references: list[dict[str, Any]] = []
    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    close = close[close > 0]
    if len(close) >= 3:
        lookback = int(params.get("weekly_anchor_lookback", 20))
        wd = weekly_dynamic_anchor(close.tail(lookback), prev_anchor=None)
        if wd["anchor"] is not None:
            references.append({
                "method": "weekly_dynamic",
                "value": wd["anchor"],
                "basis": "weekly_dynamic_geomean",
            })
        span = int(params.get("ema_anchor_span", 120))
        ema = ema_series(close, span=span)
        if not ema.empty:
            references.append({
                "method": "dynamic_ema",
                "value": round(float(ema.iloc[-1]), 3),
                "basis": f"ema_{span}_static_approx",
            })
    if "high" in frame.columns and "low" in frame.columns:
        swing_high, swing_low, swing_mid = swing_levels(
            frame["high"], frame["low"], window=int(params.get("swing_window", 200))
        )
        if swing_mid is not None:
            references.append({
                "method": "swing_mid",
                "value": round(float(swing_mid), 3),
                "basis": f"swing_window_{params.get('swing_window', 200)}",
            })
            if swing_high is not None and swing_low is not None:
                fib_mid = round(float(swing_low) + 0.618 * (float(swing_high) - float(swing_low)), 3)
                references.append({
                    "method": "fib_mid_618",
                    "value": fib_mid,
                    "basis": "fib_retracement_618",
                })
    return {
        "anchor": anchor,
        "basis": basis,
        "sources": sources,
        "stability": {"score": stability.get("score"), "grade": stability.get("grade")},
        "references": references,
    }


# ---------------------------------------------------------------------------
# 两段步长
# ---------------------------------------------------------------------------

def _spreads(
    frame: pd.DataFrame,
    grid_row: dict[str, Any],
    params: dict[str, Any],
) -> tuple[float | None, float | None, str | None, dict[str, Any]]:
    """regular_spread(ATR 主公式) + edge_spread + edge_spread_basis + 间距信息 dict。

    edge_spread = regular × max(默认乘子, cone_60_p95/p50)，上限 4×regular。
    """
    spread_info = _spread_suggestion(frame, params)
    regular = spread_info["spread"]
    if regular is None:
        return None, None, None, spread_info
    regular = round(float(regular), 4)
    cone_p95 = _to_float(grid_row.get("cone_60_p95"))
    cone_p50 = _to_float(grid_row.get("cone_60_p50"))
    default_mult = float(params["edge_spread_multiplier"])
    if cone_p95 is not None and cone_p50 is not None and cone_p50 > 0:
        edge_mult = max(default_mult, cone_p95 / cone_p50)
        basis = "cone_60_p95"
    else:
        edge_mult = default_mult
        basis = "default_multiplier"
    edge_mult = min(edge_mult, float(params["edge_spread_max_multiple"]))
    edge = round(float(regular) * edge_mult, 4)
    return regular, edge, basis, spread_info


def _levels(grid_row: dict[str, Any], params: dict[str, Any]) -> tuple[int, int]:
    """regular/edge 档数；vol_rank_60d > 阈时边缘档升 2。"""
    regular_levels = int(params["regular_levels_per_side"])
    edge_levels = int(params["edge_levels_per_side"])
    vol_rank = _to_float(grid_row.get("vol_rank_60d"))
    if vol_rank is not None and vol_rank > float(params["vol_rank_edge_threshold"]):
        edge_levels = int(params["edge_levels_high_vol"])
    return regular_levels, edge_levels


# ---------------------------------------------------------------------------
# correlation（相关性配置）
# ---------------------------------------------------------------------------

def _close_panel(
    market_data: dict[str, pd.DataFrame],
    asset_ids: list[str],
    window_days: int,
    data_asof: str | None,
) -> pd.DataFrame:
    """组装 date x asset close 面板（各自独立日期，缺失 NaN 不虚构）。"""
    panel = pd.DataFrame()
    for asset_id in asset_ids:
        frame = market_data.get(asset_id)
        if frame is None or frame.empty or "close" not in frame.columns:
            continue
        sub = frame.copy()
        sub["trade_date"] = pd.to_datetime(sub["trade_date"], errors="coerce")
        sub["close"] = pd.to_numeric(sub["close"], errors="coerce")
        sub = sub.dropna(subset=["trade_date", "close"])
        sub = sub[sub["close"] > 0].sort_values("trade_date")
        if data_asof:
            try:
                sub = sub[pd.to_datetime(sub["trade_date"]) <= pd.Timestamp(data_asof)]
            except Exception:
                pass
        sub = sub.tail(window_days)
        series = sub.set_index("trade_date")["close"]
        panel[asset_id] = series
    return panel.sort_index()


def _correlation(
    strategy: Any,
    market_data: dict[str, pd.DataFrame],
    params: dict[str, Any],
    data_asof: str | None,
) -> dict[str, Any]:
    """每策略启用标的集合 90d 日收益两两相关。"""
    enabled = list(strategy.enabled_assets)
    panel = _close_panel(market_data, enabled, int(params["corr_window_days"]), data_asof)
    if panel.shape[1] < 2 or panel.shape[0] < 2:
        return {"matrix": {}, "high_corr_pairs": [], "avg_corr": None, "redundancy_note": "low"}
    corr = correlation_matrix(panel, min_overlap=int(params["corr_min_overlap"]))
    assets = list(corr.columns)
    high_threshold = float(params["corr_high_threshold"])
    high_corr_pairs: list[dict[str, Any]] = []
    corr_values: list[float] = []
    for i in range(len(assets)):
        for j in range(i + 1, len(assets)):
            value = corr.iloc[i, j]
            if pd.isna(value):
                continue
            corr_values.append(float(value))
            if abs(float(value)) > high_threshold:
                high_corr_pairs.append({
                    "asset_a": assets[i],
                    "asset_b": assets[j],
                    "corr": round(float(value), 4),
                })
    avg_corr = float(np.mean(corr_values)) if corr_values else None
    if avg_corr is None:
        redundancy_note = "low"
    elif avg_corr >= high_threshold:
        redundancy_note = "high"
    elif avg_corr >= float(params["corr_moderate_threshold"]):
        redundancy_note = "moderate"
    else:
        redundancy_note = "low"
    matrix: dict[str, dict[str, Any]] = {}
    for asset_a in assets:
        matrix[asset_a] = {}
        for asset_b in assets:
            value = corr.loc[asset_a, asset_b]
            matrix[asset_a][asset_b] = round(float(value), 4) if not pd.isna(value) else None
    return {
        "matrix": matrix,
        "high_corr_pairs": high_corr_pairs,
        "avg_corr": round(avg_corr, 4) if avg_corr is not None else None,
        "redundancy_note": redundancy_note,
    }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def build_grid_suggestion(
    strategy_assets: list[Any],
    market_data: dict[str, pd.DataFrame],
    grid_reference: pd.DataFrame | dict[str, Any] | None,
    shared_grid: dict[str, Any] | None,
    data_asof: str,
) -> dict[str, Any]:
    """构建网格建议包。

    参数：
        strategy_assets: ``ContractStrategy`` 列表（来自 A 侧策略契约）。
        market_data: ``{asset_id: DataFrame(trade_date, close, [vol, high, low, open])}``。
        grid_reference: grid_reference_table DataFrame 或 ``{asset_id: 行 dict}``；
            None 时从行情自行计算。
        shared_grid: ``contract.shared_config.grid``（A 侧 "grid" 段；缺省用代码常量）。
        data_asof: 数据截止日（``YYYY-MM-DD``）。

    返回：
        ``{schema_version, approval_policy, generated_date, data_asof,
        strategies: [{strategy_id, assets: [...], correlation: {...}}]}``。
    """
    params = _grid_params(shared_grid)
    window_days = int(params["window_days"])
    ref_by_asset = _grid_reference_index(grid_reference)
    # 延迟导入避免循环依赖（grid_theoretical_profit 顶部导入本模块辅助函数）。
    from qteasy_research.reference.grid_theoretical_profit import (
        compute_asset_theoretical_profit,
        compute_asset_theoretical_profit_actual,
    )
    amount_per_grid = float(params.get("amount_per_grid", 6000.0))
    fee_rate = float(params.get("fee_rate", 0.0005))
    theory_max_w = float(params.get("theory_max_w", 1.0))
    levels_total = int(params.get("levels_total", 9))

    def _actual_grid_tp(asset: Any, asset_id: str, window: pd.DataFrame):
        """按 A 权威 grid_config 计算实际口径理论收益；gc 缺失 → 函数内降级。"""
        gc = getattr(asset, "grid_config", None)
        return compute_asset_theoretical_profit_actual(
            asset_id,
            gc.regular_spread if gc is not None else None,
            gc.regular_levels_per_side if gc is not None else None,
            gc.edge_levels_per_side if gc is not None else None,
            window,
            amount_per_grid, fee_rate, theory_max_w,
        )

    strategies_out: list[dict[str, Any]] = []
    for strategy in strategy_assets:
        if not getattr(strategy, "enabled", True):
            continue
        # 只对网格资本桶策略产出网格建议。契约 strategies[] 已含 capital_bucket
        # 字段（strategy_master 列，A 侧导出），优先按 ``capital_bucket=="grid"``
        # 过滤，非网格策略（barbell/global_allocation/three_musketeers 等）不出现、
        # 不参与 assets_out；字段缺失/未配置（旧契约或测试 fixture 不传）时回退
        # ``decision_rule=="grid"``（grid_lh/grid_scz 的 decision_rule 均为 grid，
        # 不硬编码 strategy_id）。
        bucket = getattr(strategy, "capital_bucket", None)
        if bucket is not None:
            if bucket != "grid":
                continue
        elif strategy.decision_rule != "grid":
            continue
        assets_out: list[dict[str, Any]] = []
        for asset in strategy.assets:
            if not asset.enabled:
                continue
            asset_id = asset.asset_id
            frame = market_data.get(asset_id, pd.DataFrame())
            window = _asset_window(frame, window_days, data_asof)
            max_anchor_window = max(
                int(w) for w in params.get("anchor_windows", (20, 60, 90, 120))
            )
            anchor_window = _asset_window(
                frame, max(window_days, max_anchor_window), data_asof
            )
            grid_row = ref_by_asset.get(asset_id, {})
            if window.empty or len(window) < _MIN_HISTORY:
                assets_out.append({
                    "asset_id": asset_id,
                    "suitability_score": None,
                    "suitability": "not_suitable",
                    "suitability_breakdown": {
                        "vol_rank_score": None,
                        "amplitude_score": None,
                        "drift_score": None,
                        "trigger_freq_score": None,
                    },
                    "anchor_suggestion": None,
                    "anchor_basis": None,
                    "anchor_sources": {},
                    "anchor_stability_score": None,
                    "anchor_stability_grade": None,
                    "anchor_references": [],
                    "regular_spread": None,
                    "edge_spread": None,
                    "edge_spread_basis": None,
                    "spread_basis": None,
                    "spread_regime": {"regime": "unknown", "adx": None, "atr_pct": None},
                    "spread_alternatives": {},
                    "cost_constraint_applied": False,
                    "cost_constraint_note": None,
                    "regular_levels_per_side": None,
                    "edge_levels_per_side": None,
                    "spacing_reference": _spacing_reference(window, grid_row, params),
                    "theoretical_profit": compute_asset_theoretical_profit(
                        asset_id, None, window, amount_per_grid, fee_rate, theory_max_w,
                        levels_total=levels_total,
                    ),
                    "theoretical_profit_actual": _actual_grid_tp(
                        asset, asset_id, window,
                    ),
                    "confidence": "low",
                })
                continue
            suitability = _compute_suitability(window, grid_row, params)
            anchor_info = _anchor_suggestion(anchor_window, params)
            regular_spread, edge_spread, edge_basis, spread_info = _spreads(window, grid_row, params)
            regular_levels, edge_levels = _levels(grid_row, params)
            stability = anchor_info["stability"] or {}
            assets_out.append({
                "asset_id": asset_id,
                "suitability_score": suitability["score"],
                "suitability": suitability["grade"],
                "suitability_breakdown": suitability["breakdown"],
                "anchor_suggestion": anchor_info["anchor"],
                "anchor_basis": anchor_info["basis"],
                "anchor_sources": anchor_info["sources"],
                "anchor_stability_score": stability.get("score"),
                "anchor_stability_grade": stability.get("grade"),
                "anchor_references": anchor_info["references"],
                "regular_spread": regular_spread,
                "edge_spread": edge_spread,
                "edge_spread_basis": edge_basis,
                "spread_basis": spread_info.get("basis"),
                "spread_regime": {
                    "regime": spread_info.get("regime"),
                    "adx": spread_info.get("adx"),
                    "atr_pct": spread_info.get("atr_pct"),
                },
                "spread_alternatives": spread_info.get("alternatives"),
                "cost_constraint_applied": spread_info.get("cost_constraint_applied"),
                "cost_constraint_note": spread_info.get("cost_constraint_note"),
                "regular_levels_per_side": regular_levels,
                "edge_levels_per_side": edge_levels,
                "spacing_reference": _spacing_reference(window, grid_row, params),
                "theoretical_profit": compute_asset_theoretical_profit(
                    asset_id, regular_spread, window, amount_per_grid, fee_rate, theory_max_w,
                    levels_total=levels_total,
                ),
                "theoretical_profit_actual": _actual_grid_tp(
                    asset, asset_id, window,
                ),
                "confidence": "high" if len(window) >= window_days else "low",
            })
        strategies_out.append({
            "strategy_id": strategy.strategy_id,
            "assets": assets_out,
            "correlation": _correlation(strategy, market_data, params, data_asof),
        })

    return {
        "schema_version": GRID_SUGGESTION_SCHEMA_VERSION,
        "approval_policy": "REFERENCE_ONLY",
        "generated_date": today_iso(),
        "data_asof": data_asof,
        "theoretical_profit_params": {
            "fee_rate": fee_rate,
            "amount_per_grid": amount_per_grid,
            "theory_max_w": theory_max_w,
            "levels_total": levels_total,
            "basis": "accounting + path_theory",
            "assumptions": [
                "round_trip = single_side_grid_triggers / 2",
                "theory_max = 0.8 * w * sigma_annual * n_annual * (amount_per_grid * levels_total)",
                "cost_breakeven: regular_spread > 2 * fee_rate",
            ],
        },
        "strategies": strategies_out,
    }


def _jstr(value: Any) -> str | None:
    """dict/list → 紧凑 JSON 字符串（CSV 列可读）；None 保持 None。"""
    if value is None:
        return None
    return json.dumps(value, sort_keys=True)


def build_grid_suggestion_table(suggestion: dict[str, Any]) -> pd.DataFrame:
    """扁平 CSV 行（每标的一行，A 侧好渲染）。"""
    columns = [
        "strategy_id",
        "asset_id",
        "suitability_score",
        "suitability",
        "vol_rank_score",
        "amplitude_score",
        "drift_score",
        "trigger_freq_score",
        "anchor_suggestion",
        "anchor_basis",
        "anchor_sources",
        "anchor_stability_score",
        "anchor_stability_grade",
        "anchor_references",
        "regular_spread",
        "edge_spread",
        "edge_spread_basis",
        "spread_basis",
        "spread_regime",
        "spread_alternatives",
        "cost_constraint_applied",
        "cost_constraint_note",
        "regular_levels_per_side",
        "edge_levels_per_side",
        "spacing_reference_default",
        "spacing_reference_min",
        "spacing_reference_max",
        "confidence",
        "theory_actual_degraded",
        "theory_actual_round_profit",
        "theory_actual_n_annual",
        "theory_actual_annual_accounting_profit",
        "theory_actual_theory_max",
        "theory_actual_cost_breakeven",
        "theory_actual_levels_total",
    ]
    rows: list[dict[str, Any]] = []
    for strategy in suggestion.get("strategies", []):
        for asset in strategy.get("assets", []):
            breakdown = asset.get("suitability_breakdown", {})
            spacing = asset.get("spacing_reference", {}) or {}
            tp_actual = asset.get("theoretical_profit_actual") or {}
            rows.append({
                "strategy_id": strategy.get("strategy_id"),
                "asset_id": asset.get("asset_id"),
                "suitability_score": asset.get("suitability_score"),
                "suitability": asset.get("suitability"),
                "vol_rank_score": breakdown.get("vol_rank_score"),
                "amplitude_score": breakdown.get("amplitude_score"),
                "drift_score": breakdown.get("drift_score"),
                "trigger_freq_score": breakdown.get("trigger_freq_score"),
                "anchor_suggestion": asset.get("anchor_suggestion"),
                "anchor_basis": asset.get("anchor_basis"),
                "anchor_sources": _jstr(asset.get("anchor_sources")),
                "anchor_stability_score": asset.get("anchor_stability_score"),
                "anchor_stability_grade": asset.get("anchor_stability_grade"),
                "anchor_references": _jstr(asset.get("anchor_references")),
                "regular_spread": asset.get("regular_spread"),
                "edge_spread": asset.get("edge_spread"),
                "edge_spread_basis": asset.get("edge_spread_basis"),
                "spread_basis": asset.get("spread_basis"),
                "spread_regime": _jstr(asset.get("spread_regime")),
                "spread_alternatives": _jstr(asset.get("spread_alternatives")),
                "cost_constraint_applied": asset.get("cost_constraint_applied"),
                "cost_constraint_note": asset.get("cost_constraint_note"),
                "regular_levels_per_side": asset.get("regular_levels_per_side"),
                "edge_levels_per_side": asset.get("edge_levels_per_side"),
                "spacing_reference_default": spacing.get("default"),
                "spacing_reference_min": spacing.get("min"),
                "spacing_reference_max": spacing.get("max"),
                "confidence": asset.get("confidence"),
                "theory_actual_degraded": tp_actual.get("degraded"),
                "theory_actual_round_profit": tp_actual.get("round_profit"),
                "theory_actual_n_annual": tp_actual.get("n_annual"),
                "theory_actual_annual_accounting_profit": tp_actual.get("annual_accounting_profit"),
                "theory_actual_theory_max": tp_actual.get("theory_max"),
                "theory_actual_cost_breakeven": tp_actual.get("cost_breakeven"),
                "theory_actual_levels_total": tp_actual.get("actual_levels_total"),
            })
    return pd.DataFrame(rows, columns=columns)


__all__ = [
    "build_grid_suggestion",
    "build_grid_suggestion_table",
]
