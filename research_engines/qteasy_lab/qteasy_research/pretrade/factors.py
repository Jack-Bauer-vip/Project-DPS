"""资产短线、中线和长线研究因子。

因子只输出研究状态和评分，不生成交易指令。所有缺失值返回 ``None``，
并同时记录原因，避免把无法计算伪装成中性或零值。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


FACTOR_FORMULA_VERSION = "asset-factors-v1"
HORIZONS = ("short", "medium", "long")
HORIZON_LABELS = {"short": "短线", "medium": "中线", "long": "长线参考"}


def _number(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def _factor(
    name: str,
    category: str,
    horizon: str,
    value: Any,
    *,
    direction: str,
    score: float | None,
    confidence: str,
    formula: str,
    source: str = "本地确定性计算",
    reason: str | None = None,
) -> dict[str, Any]:
    numeric = _number(value)
    return {
        "name": name,
        "category": category,
        "horizon": horizon,
        "value": numeric,
        "state": "unavailable" if numeric is None else ("positive" if direction == "positive" else "negative" if direction == "negative" else "neutral"),
        "direction": direction,
        "score": score,
        "confidence": confidence,
        "formula": formula,
        "source": source,
        "reason": reason or ("数据不足" if numeric is None else ""),
    }


def _score_from_direction(value: float | None, *, positive_when: str = "positive") -> float | None:
    if value is None:
        return None
    if positive_when == "positive":
        return float(np.clip(50 + value * 100, 0, 100))
    if positive_when == "low":
        return float(np.clip(100 - value * 100, 0, 100))
    return 50.0


def _average_score(factors: list[dict[str, Any]]) -> float | None:
    scores = [item["score"] for item in factors if item.get("score") is not None]
    return float(np.mean(scores)) if scores else None


def _state(score: float | None) -> str:
    if score is None:
        return "unavailable"
    if score >= 65:
        return "strong"
    if score <= 35:
        return "weak"
    return "neutral"


def _consistency(factors: list[dict[str, Any]]) -> str:
    directions = [item.get("direction") for item in factors if item.get("value") is not None and item.get("direction") in {"positive", "negative"}]
    if len(directions) < 2:
        return "unavailable"
    positive = directions.count("positive")
    negative = directions.count("negative")
    if positive >= len(directions) * 0.7:
        return "positive"
    if negative >= len(directions) * 0.7:
        return "negative"
    return "mixed"


def _series(frame: pd.DataFrame) -> pd.Series:
    if frame.empty or "close" not in frame:
        return pd.Series(dtype=float)
    data = frame.copy()
    data["trade_date"] = pd.to_datetime(data["trade_date"], errors="coerce")
    data["close"] = pd.to_numeric(data["close"], errors="coerce")
    return data.dropna(subset=["trade_date", "close"]).sort_values("trade_date").set_index("trade_date")["close"]


def _momentum_factor(close: pd.Series, days: int, name: str, horizon: str) -> dict[str, Any]:
    value = _number(close.pct_change(days).iloc[-1]) if len(close) > days else None
    return _factor(name, "momentum", horizon, value, direction="positive" if value is not None and value > 0 else "negative" if value is not None else "neutral", score=_score_from_direction(value), confidence="normal" if len(close) >= days + 1 else "low", formula=f"close[t] / close[t-{days}] - 1")


def analyze_asset_factors(
    code: str,
    frame: pd.DataFrame,
    *,
    quantitative_metrics: dict[str, Any] | None = None,
    benchmark_analysis: dict[str, Any] | None = None,
    horizon: str = "medium",
) -> dict[str, Any]:
    if horizon not in HORIZONS:
        raise ValueError("期限必须是 short、medium 或 long")
    metrics = quantitative_metrics or {}
    benchmark = benchmark_analysis or {}
    close = _series(frame)
    technical = metrics.get("technical_analysis", {})
    indicators = technical.get("indicators", {})
    factors: list[dict[str, Any]] = []

    if horizon == "short":
        factors.append(_momentum_factor(close, 20, "20日动量", horizon))
        macd = indicators.get("macd", {})
        macd_value = _number(macd.get("histogram", {}).get("value"))
        factors.append(_factor("MACD动能", "technical", horizon, macd_value, direction="positive" if macd.get("state") == "bullish" else "negative" if macd.get("state") == "bearish" else "neutral", score=65 if macd.get("state") == "bullish" else 35 if macd.get("state") == "bearish" else 50, confidence="normal" if macd_value is not None else "low", formula="DIF - DEA"))
        rsi = _number(indicators.get("rsi", {}).get("value", {}).get("value"))
        rsi_direction = "positive" if rsi is not None and 50 <= rsi <= 70 else "negative" if rsi is not None and rsi < 30 else "neutral"
        rsi_score = 70 if rsi_direction == "positive" else 30 if rsi_direction == "negative" else 50
        factors.append(_factor("RSI14状态", "technical", horizon, rsi, direction=rsi_direction, score=rsi_score, confidence="normal" if rsi is not None else "low", formula="100 - 100 / (1 + 平均上涨幅度 / 平均下跌幅度)"))
        atr = _number(indicators.get("atr", {}).get("relative_to_price", {}).get("value"))
        factors.append(_factor("ATR短期波动", "risk", horizon, atr, direction="negative" if atr is not None and atr > 0.03 else "positive" if atr is not None else "neutral", score=_score_from_direction(atr, positive_when="low"), confidence="normal" if atr is not None else "low", formula="ATR14 / 最新收盘价"))
        bollinger = _number(indicators.get("bollinger", {}).get("position", {}).get("value"))
        bollinger_direction = "negative" if bollinger is not None and bollinger > 0.95 else "positive" if bollinger is not None and 0.35 <= bollinger <= 0.80 else "neutral"
        factors.append(_factor("Bollinger位置", "technical", horizon, bollinger, direction=bollinger_direction, score=35 if bollinger_direction == "negative" else 65 if bollinger_direction == "positive" else 50, confidence="normal" if bollinger is not None else "low", formula="(close - 下轨) / (上轨 - 下轨)"))

    elif horizon == "medium":
        factors.extend([
            _momentum_factor(close, 63, "63日动量", horizon),
            _momentum_factor(close, 126, "126日动量", horizon),
        ])
        rolling_vol = metrics.get("rolling", {}).get("volatility_63d")
        factors.append(_factor("63日滚动波动", "risk", horizon, rolling_vol, direction="negative" if _number(rolling_vol) is not None and rolling_vol > 0.30 else "positive" if _number(rolling_vol) is not None else "neutral", score=_score_from_direction(_number(rolling_vol), positive_when="low"), confidence="normal" if _number(rolling_vol) is not None else "low", formula="63日收益标准差 × √252"))
        one_year = metrics.get("windows", {}).get("1y", {})
        drawdown = _number(one_year.get("max_drawdown"))
        factors.append(_factor("一年最大回撤", "risk", horizon, drawdown, direction="negative" if drawdown is not None and drawdown < -0.20 else "positive" if drawdown is not None else "neutral", score=float(np.clip(100 + (drawdown or -1) * 100, 0, 100)) if drawdown is not None else None, confidence=one_year.get("confidence", "low"), formula="累计净值 / 历史峰值 - 1"))
        corr = _number(benchmark.get("correlation"))
        factors.append(_factor("基准相关性", "relative", horizon, corr, direction="positive" if corr is not None and corr < 0.50 else "neutral" if corr is not None else "neutral", score=70 if corr is not None and corr < 0.50 else 50 if corr is not None else None, confidence="normal" if corr is not None else "low", formula="标的日收益与基准日收益 Pearson 相关系数"))
        beta = _number(benchmark.get("beta"))
        factors.append(_factor("基准Beta", "relative", horizon, beta, direction="neutral", score=50 if beta is not None else None, confidence="normal" if beta is not None else "low", formula="Cov(标的收益, 基准收益) / Var(基准收益)"))

    else:
        three_year = metrics.get("windows", {}).get("3y", {})
        factors.extend([
            _factor("三年年化收益", "long_term", horizon, three_year.get("annual_return"), direction="positive" if _number(three_year.get("annual_return")) is not None and three_year.get("annual_return") > 0 else "negative" if _number(three_year.get("annual_return")) is not None else "neutral", score=_score_from_direction(_number(three_year.get("annual_return"))), confidence=three_year.get("confidence", "low"), formula="(1 + 区间累计收益)^(252 / 样本数) - 1"),
            _factor("三年年化波动", "long_term", horizon, three_year.get("annual_volatility"), direction="negative" if _number(three_year.get("annual_volatility")) is not None and three_year.get("annual_volatility") > 0.30 else "positive" if _number(three_year.get("annual_volatility")) is not None else "neutral", score=_score_from_direction(_number(three_year.get("annual_volatility")), positive_when="low"), confidence=three_year.get("confidence", "low"), formula="日收益标准差 × √252"),
            _factor("三年最大回撤", "long_term", horizon, three_year.get("max_drawdown"), direction="negative" if _number(three_year.get("max_drawdown")) is not None and three_year.get("max_drawdown") < -0.30 else "positive" if _number(three_year.get("max_drawdown")) is not None else "neutral", score=float(np.clip(100 + (_number(three_year.get("max_drawdown")) or -1) * 100, 0, 100)) if _number(three_year.get("max_drawdown")) is not None else None, confidence=three_year.get("confidence", "low"), formula="累计净值 / 历史峰值 - 1"),
        ])

    score = _average_score(factors)
    return {
        "code": code,
        "horizon": horizon,
        "horizon_label": HORIZON_LABELS[horizon],
        "as_of": metrics.get("quality", {}).get("end_date"),
        "formula_version": FACTOR_FORMULA_VERSION,
        "factors": factors,
        "composite_score": score,
        "state": _state(score),
        "consistency": _consistency(factors),
        "summary": f"{HORIZON_LABELS[horizon]}因子综合评分 {_format_score(score)}，状态 {_state(score)}；该结果用于研究确认，不直接生成交易指令。",
    }


def _format_score(value: float | None) -> str:
    return "不可用" if value is None else f"{value:.1f}/100"
