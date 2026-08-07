"""逐资产风控红/橙/黄建议（阶段二 M3）。

只读系统A ``config/strategy_params.json`` 的 ``risk_thresholds`` 阈值，对每个资产
独立比较其当前回撤 / 波动率，产出红/橙/黄建议，``approval_required=true``
（供人工审核工作台展示预警，永不自动 APPROVED）。

**指标口径镜像系统A**（``D:\\FF Project\\src\\indicators.py`` + ``risk.py``，防交叉
验证产生噪音）：
- 回撤 = ``close / close.rolling(60, min_periods=20).max() - 1``（近 60 日高点当前回撤，
  非全程 max_drawdown）—— indicators.py:62 的 drawdown60。
- 波动率 = ``simple_return.rolling(20, min_periods=10).std()`` **日频未年化**
  —— indicators.py:48；故 ``volatility_yellow=0.035`` 是日频口径。
- 判定与 risk.py:152-156 等价：回撤三档优先（red/orange/yellow），回撤为绿时
  波动率超阈触发黄。
- 类型→波动阈值（镜像 risk.py:210-226 ``_volatility_yellow_threshold``）：bond/货币/fixed
  →0.015，stock/股票→0.035，etf/lof/fund→0.045，默认 0.035。

无风险资产不产出（上层填 None，节省决策包体积）；全部字段英文（零中文策略名）。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from qteasy_research.reference.config import SYSTEM_A_RISK_PARAMS

# 与系统A risk_thresholds 对齐的兜底默认值（仅用于缺失项，其余保留文件值）。
_DEFAULT_THRESHOLDS: dict[str, float] = {
    "red_drawdown": -0.18,
    "orange_drawdown": -0.12,
    "yellow_drawdown": -0.07,
    "volatility_yellow": 0.035,
    "volatility_yellow_etf": 0.045,
    "volatility_yellow_stock": 0.035,
    "volatility_yellow_bond_etf": 0.015,
}

# 镜像 A indicators.py 参数。
_DRAWDOWN_WINDOW = 60
_DRAWDOWN_MIN_PERIODS = 20
_VOL_WINDOW = 20
_VOL_MIN_PERIODS = 10

# 风险等级（英文，机器输出零中文）。
_LEVELS = ("red", "orange", "yellow")


def load_risk_thresholds(path: str | Path | None = None) -> tuple[dict[str, float] | None, list[str]]:
    """只读 A ``strategy_params.json`` 的 ``risk_thresholds``。

    返回 ``(阈值 dict, warnings)``：
    - 文件 / JSON 解析失败 / ``risk_thresholds`` 缺失 → ``(None, [warning])``；
    - 单项缺失 → **仅该缺失项**用默认值兜底 + warning，其余已配置项保留文件值
      （容错且不丢失用户配置）。

    参数：
        path: 显式路径（测试注入用）；None 用 ``config.SYSTEM_A_RISK_PARAMS``。
    """
    target = Path(path) if path is not None else SYSTEM_A_RISK_PARAMS
    warnings: list[str] = []
    if not target.exists():
        return None, [f"系统A风控参数不存在：{target}"]
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, [f"系统A风控参数解析失败：{type(exc).__name__}: {exc}"]
    raw = payload.get("risk_thresholds") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return None, [f"strategy_params.json 缺少 risk_thresholds 段落：{target}"]

    merged: dict[str, float] = {}
    for key, default in _DEFAULT_THRESHOLDS.items():
        value = raw.get(key, default)
        try:
            merged[key] = float(value)
        except (TypeError, ValueError):
            merged[key] = default
            warnings.append(
                f"risk_thresholds.{key} 值无法解析（{value!r}），使用默认值 {default}"
            )
        if key not in raw:
            warnings.append(f"risk_thresholds 缺少 {key}，使用默认值 {default}")
    return merged, warnings


def assess_red_flags(
    assets: pd.DataFrame,
    aligned: dict[str, pd.DataFrame],
    thresholds: dict[str, float] | None,
    asset_type_map: dict[str, str] | None = None,
) -> dict[str, dict]:
    """逐资产产风控旗，**仅返回有风险资产**（red/orange/yellow）。

    无风险 / 行情缺失资产不出现在结果（上层据此填 None）。类型映射默认取
    ``assets`` 的 ``type`` 列，可用 ``asset_type_map`` 覆盖（测试隔离）。

    返回：``{asset_id: {"level", "triggered_by", "approval_required", "metrics"}}``。
    """
    if thresholds is None or assets.empty:
        return {}
    type_map = asset_type_map if asset_type_map is not None else {
        str(row["asset_id"]).strip(): str(row.get("type") or "")
        for _, row in assets.iterrows()
    }
    flags: dict[str, dict] = {}
    for _, row in assets.iterrows():
        asset_id = str(row["asset_id"]).strip()
        frame = aligned.get(asset_id, pd.DataFrame())
        flag = _flag_for_asset(
            frame, thresholds, type_map.get(asset_id, "")
        )
        if flag is not None:
            flags[asset_id] = flag
    return flags


def _flag_for_asset(
    frame: pd.DataFrame,
    thresholds: dict[str, float],
    asset_type: str,
) -> dict | None:
    """单资产风险判断：回撤三档优先，回撤为绿时波动超阈触发黄。

    返回 None 表示无风险（不产出）。
    """
    if frame is None or frame.empty or "close" not in frame.columns:
        return None
    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    returns = close.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    drawdown = _drawdown_60d(close)
    vol = _vol_20d_daily(returns)
    vol_yellow = _vol_yellow_for_type(thresholds, asset_type)

    level = "green"
    triggered: list[str] = []
    if drawdown is not None:
        if drawdown <= thresholds["red_drawdown"]:
            level, triggered = "red", ["drawdown_60d"]
        elif drawdown <= thresholds["orange_drawdown"]:
            level, triggered = "orange", ["drawdown_60d"]
        elif drawdown <= thresholds["yellow_drawdown"]:
            level, triggered = "yellow", ["drawdown_60d"]
    if level == "green" and vol is not None and vol >= vol_yellow:
        level, triggered = "yellow", ["volatility_20d"]
    if level == "green":
        return None
    return {
        "level": level,
        "triggered_by": triggered,
        "approval_required": True,
        "metrics": {
            "drawdown_60d": round(drawdown, 4) if drawdown is not None else None,
            "volatility_20d": round(vol, 4) if vol is not None else None,
        },
    }


def _drawdown_60d(close: pd.Series) -> float | None:
    """近 60 日高点当前回撤（镜像 A indicators.py drawdown60）。"""
    peak = close.rolling(_DRAWDOWN_WINDOW, min_periods=_DRAWDOWN_MIN_PERIODS).max()
    if peak.empty or pd.isna(peak.iloc[-1]):
        return None
    return float(close.iloc[-1] / peak.iloc[-1] - 1.0)


def _vol_20d_daily(returns: pd.Series) -> float | None:
    """日频 20 日波动率（镜像 A indicators.py volatility20，未年化）。"""
    if len(returns) < _VOL_MIN_PERIODS:
        return None
    value = returns.rolling(_VOL_WINDOW, min_periods=_VOL_MIN_PERIODS).std()
    if pd.isna(value.iloc[-1]):
        return None
    return float(value.iloc[-1])


def _vol_yellow_for_type(thresholds: dict[str, float], asset_type: str) -> float:
    """类型→波动黄色阈值（镜像 A risk.py _volatility_yellow_threshold）。"""
    t = str(asset_type or "").lower()
    if any(keyword in t for keyword in ("bond", "货币", "fixed")):
        return float(thresholds.get("volatility_yellow_bond_etf", 0.015))
    if "stock" in t or "股票" in t:
        return float(thresholds.get("volatility_yellow_stock", 0.035))
    if any(keyword in t for keyword in ("etf", "lof", "fund")):
        return float(thresholds.get("volatility_yellow_etf", 0.045))
    return float(thresholds.get("volatility_yellow", 0.035))


__all__ = [
    "assess_red_flags",
    "load_risk_thresholds",
]
