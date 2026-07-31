"""可版本化的单资产技术指标与合成评分。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd


FORMULA_VERSION = "technical-v1"


@dataclass(frozen=True)
class TechnicalIndicatorConfig:
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    rsi_period: int = 14
    atr_period: int = 14
    adx_period: int = 14
    bollinger_period: int = 20
    bollinger_std: float = 2.0

    def __post_init__(self) -> None:
        for name in (
            "macd_fast", "macd_slow", "macd_signal", "rsi_period",
            "atr_period", "adx_period", "bollinger_period",
        ):
            if int(getattr(self, name)) < 2:
                raise ValueError(f"{name} must be >= 2")
        if self.macd_fast >= self.macd_slow:
            raise ValueError("macd_fast must be smaller than macd_slow")
        if self.bollinger_std <= 0:
            raise ValueError("bollinger_std must be positive")

    @classmethod
    def from_dict(cls, values: dict[str, Any] | None = None) -> "TechnicalIndicatorConfig":
        allowed = set(cls.__dataclass_fields__)
        payload = {key: value for key, value in (values or {}).items() if key in allowed}
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def _metric(value: Any, *, reason: str | None = None, confidence: str = "normal") -> dict[str, Any]:
    clean = _safe(value)
    return {
        "value": clean,
        "reason": reason if clean is None else None,
        "confidence": confidence if clean is not None else "low",
    }


def _state(value: float | None, *, high: float = 65, low: float = 35) -> str:
    if value is None:
        return "unavailable"
    if value >= high:
        return "strong"
    if value <= low:
        return "weak"
    return "neutral"


def _bounded_score(value: float | None, scale: float) -> float | None:
    if value is None:
        return None
    return float(np.clip(50.0 + 50.0 * np.tanh(value / scale), 0.0, 100.0))


def _weighted_score(parts: list[tuple[float | None, float]]) -> float | None:
    available = [(value, weight) for value, weight in parts if value is not None]
    if not available:
        return None
    total_weight = sum(weight for _, weight in available)
    return float(sum(value * weight for value, weight in available) / total_weight)


def _prepare(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"trade_date", "close"}
    if frame.empty or not required.issubset(frame.columns):
        return pd.DataFrame()
    work = frame.copy()
    work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce")
    work["close"] = pd.to_numeric(work["close"], errors="coerce")
    for column in ("high", "low", "amount"):
        if column in work:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    work = work.dropna(subset=["trade_date", "close"])
    work = work[work["close"] > 0]
    return work.sort_values("trade_date").drop_duplicates("trade_date", keep="last").reset_index(drop=True)


def _latest(series: pd.Series) -> float | None:
    if series.empty or pd.isna(series.iloc[-1]):
        return None
    return _safe(series.iloc[-1])


def _relative_returns(frame: pd.DataFrame, benchmark: pd.DataFrame | None) -> dict[str, float | None]:
    if benchmark is None or benchmark.empty:
        return {}
    asset = frame[["trade_date", "close"]].rename(columns={"close": "asset"})
    bench = _prepare(benchmark)[["trade_date", "close"]].rename(columns={"close": "benchmark"})
    joined = asset.merge(bench, on="trade_date", how="inner").sort_values("trade_date")
    if len(joined) < 2:
        return {}
    result: dict[str, float | None] = {}
    for days in (21, 63, 126, 252):
        if len(joined) <= days:
            result[f"{days}d"] = None
            continue
        asset_return = joined["asset"].iloc[-1] / joined["asset"].iloc[-days - 1] - 1
        benchmark_return = joined["benchmark"].iloc[-1] / joined["benchmark"].iloc[-days - 1] - 1
        result[f"{days}d"] = _safe(asset_return - benchmark_return)
    return result


def analyze_technical_indicators(
    frame: pd.DataFrame,
    benchmark_frame: pd.DataFrame | None = None,
    config: TechnicalIndicatorConfig | None = None,
    risk_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculate technical indicators using data available at the latest date only."""
    cfg = config or TechnicalIndicatorConfig()
    work = _prepare(frame)
    result: dict[str, Any] = {
        "formula_version": FORMULA_VERSION,
        "config": cfg.to_dict(),
        "as_of": work["trade_date"].max().strftime("%Y-%m-%d") if not work.empty else None,
        "sample_days": max(len(work) - 1, 0),
        "indicators": {},
        "relative_strength": _relative_returns(work, benchmark_frame) if not work.empty else {},
        "composite_scores": {},
    }
    if work.empty:
        for name in ("macd", "rsi", "atr", "adx", "bollinger"):
            result["indicators"][name] = {"state": "unavailable", "reason": "missing trade_date or close"}
        return result

    close = work["close"]
    returns = close.pct_change().dropna()

    fast = close.ewm(span=cfg.macd_fast, adjust=False, min_periods=cfg.macd_fast).mean()
    slow = close.ewm(span=cfg.macd_slow, adjust=False, min_periods=cfg.macd_slow).mean()
    dif = fast - slow
    dea = dif.ewm(span=cfg.macd_signal, adjust=False, min_periods=cfg.macd_signal).mean()
    histogram = 2 * (dif - dea)
    macd_value = _latest(histogram)
    macd_previous = _safe(histogram.iloc[-2]) if len(histogram) > 1 and pd.notna(histogram.iloc[-2]) else None
    result["indicators"]["macd"] = {
        "dif": _metric(_latest(dif), reason="insufficient history"),
        "dea": _metric(_latest(dea), reason="insufficient history"),
        "histogram": _metric(macd_value, reason="insufficient history"),
        "histogram_change": _metric(macd_value - macd_previous if macd_value is not None and macd_previous is not None else None, reason="insufficient history"),
        "state": "bullish" if _latest(dif) is not None and _latest(dea) is not None and _latest(dif) > _latest(dea) else "bearish" if _latest(dif) is not None and _latest(dea) is not None else "unavailable",
    }

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / cfg.rsi_period, adjust=False, min_periods=cfg.rsi_period).mean()
    avg_loss = loss.ewm(alpha=1 / cfg.rsi_period, adjust=False, min_periods=cfg.rsi_period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi_value = _latest(rsi)
    if rsi_value is None and _latest(avg_gain) is not None and _latest(avg_loss) == 0:
        rsi_value = 100.0
    result["indicators"]["rsi"] = {
        "value": _metric(rsi_value, reason="insufficient history"),
        "state": "overbought" if rsi_value is not None and rsi_value >= 70 else "oversold" if rsi_value is not None and rsi_value <= 30 else "neutral" if rsi_value is not None else "unavailable",
    }

    high = work["high"] if "high" in work else close
    low = work["low"] if "low" in work else close
    previous_close = close.shift(1)
    true_range = pd.concat([
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs(),
    ], axis=1).max(axis=1)
    atr = true_range.ewm(alpha=1 / cfg.atr_period, adjust=False, min_periods=cfg.atr_period).mean()
    atr_value = _latest(atr)
    close_value = _latest(close)
    atr_pct = atr_value / close_value if atr_value is not None and close_value else None
    result["indicators"]["atr"] = {
        "value": _metric(atr_value, reason="insufficient history"),
        "relative_to_price": _metric(atr_pct, reason="insufficient history"),
        "state": "high" if atr_pct is not None and atr_pct >= 0.04 else "low" if atr_pct is not None and atr_pct <= 0.015 else "normal" if atr_pct is not None else "unavailable",
    }

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    atr_safe = atr.replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / cfg.adx_period, adjust=False, min_periods=cfg.adx_period).mean() / atr_safe
    minus_di = 100 * minus_dm.ewm(alpha=1 / cfg.adx_period, adjust=False, min_periods=cfg.adx_period).mean() / atr_safe
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / cfg.adx_period, adjust=False, min_periods=cfg.adx_period).mean()
    adx_value = _latest(adx)
    plus_value = _latest(plus_di)
    minus_value = _latest(minus_di)
    result["indicators"]["adx"] = {
        "value": _metric(adx_value, reason="insufficient history"),
        "plus_di": _metric(plus_value, reason="insufficient history"),
        "minus_di": _metric(minus_value, reason="insufficient history"),
        "state": "strong_trend" if adx_value is not None and adx_value >= 25 else "weak_trend" if adx_value is not None else "unavailable",
        "direction": "up" if plus_value is not None and minus_value is not None and plus_value > minus_value else "down" if plus_value is not None and minus_value is not None else "unavailable",
    }

    middle = close.rolling(cfg.bollinger_period, min_periods=cfg.bollinger_period).mean()
    deviation = close.rolling(cfg.bollinger_period, min_periods=cfg.bollinger_period).std(ddof=0)
    upper = middle + cfg.bollinger_std * deviation
    lower = middle - cfg.bollinger_std * deviation
    middle_value = _latest(middle)
    upper_value = _latest(upper)
    lower_value = _latest(lower)
    band_width = (upper_value - lower_value) / middle_value if upper_value is not None and lower_value is not None and middle_value else None
    band_position = (close_value - lower_value) / (upper_value - lower_value) if close_value is not None and upper_value is not None and lower_value is not None and upper_value != lower_value else None
    result["indicators"]["bollinger"] = {
        "middle": _metric(middle_value, reason="insufficient history"),
        "upper": _metric(upper_value, reason="insufficient history"),
        "lower": _metric(lower_value, reason="insufficient history"),
        "band_width": _metric(band_width, reason="insufficient history"),
        "position": _metric(band_position, reason="insufficient history"),
        "state": "above_upper" if band_position is not None and band_position > 1 else "below_lower" if band_position is not None and band_position < 0 else "inside_band" if band_position is not None else "unavailable",
    }

    ma60 = _latest(close.rolling(60, min_periods=60).mean())
    ma120 = _latest(close.rolling(120, min_periods=120).mean())
    ma60_previous = _safe(close.rolling(60, min_periods=60).mean().iloc[-6]) if len(close) >= 65 else None
    ma120_previous = _safe(close.rolling(120, min_periods=120).mean().iloc[-6]) if len(close) >= 125 else None
    trend_parts = [
        (50 + 50 * np.sign(close_value - ma60), 0.15) if close_value is not None and ma60 is not None else (None, 0.15),
        (50 + 50 * np.sign(close_value - ma120), 0.15) if close_value is not None and ma120 is not None else (None, 0.15),
        (50 + 50 * np.sign(ma60 - ma60_previous), 0.15) if ma60 is not None and ma60_previous is not None else (None, 0.15),
        (50 + 50 * np.sign(ma120 - ma120_previous), 0.10) if ma120 is not None and ma120_previous is not None else (None, 0.10),
        (100.0 if result["indicators"]["macd"]["state"] == "bullish" else 0.0 if result["indicators"]["macd"]["state"] == "bearish" else None, 0.20),
        (100.0 if result["indicators"]["adx"]["direction"] == "up" and (adx_value or 0) >= 20 else 0.0 if result["indicators"]["adx"]["direction"] == "down" and (adx_value or 0) >= 20 else 50.0 if adx_value is not None else None, 0.25),
    ]
    trend_score = _weighted_score(trend_parts)
    relative = result["relative_strength"]
    momentum_parts = [
        (_bounded_score(_safe(close_value / work["close"].iloc[-22] - 1) if len(work) > 21 else None, 0.10), 0.30),
        (_bounded_score(_safe(close_value / work["close"].iloc[-64] - 1) if len(work) > 63 else None, 0.15), 0.30),
        (_bounded_score(_safe(close_value / work["close"].iloc[-127] - 1) if len(work) > 126 else None, 0.25), 0.25),
        (_bounded_score(relative.get("63d"), 0.10), 0.15),
    ]
    momentum_score = _weighted_score(momentum_parts)
    if rsi_value is not None:
        momentum_score = _weighted_score([(momentum_score, 0.8), (_bounded_score(rsi_value - 50, 20), 0.2)])
    risk = risk_metrics or {}
    annual_vol = _safe(returns.std(ddof=1) * np.sqrt(252)) if len(returns) >= 2 else None
    mdd = risk.get("max_drawdown")
    if mdd is None:
        mdd = risk.get("windows", {}).get("1y", {}).get("max_drawdown")
    es95 = risk.get("es95")
    risk_score = _weighted_score([
        (_bounded_score(annual_vol, 0.25), 0.30),
        (_bounded_score(atr_pct, 0.03), 0.25),
        (_bounded_score(abs(mdd) if mdd is not None else None, 0.30), 0.25),
        (_bounded_score(abs(es95) if es95 is not None else None, 0.15), 0.20),
    ])
    agreement = None
    if trend_score is not None and momentum_score is not None and risk_score is not None:
        if trend_score >= 60 and momentum_score >= 60 and risk_score <= 60:
            agreement = "positive"
        elif trend_score <= 40 and momentum_score <= 40 and risk_score >= 50:
            agreement = "negative"
        else:
            agreement = "mixed"
    result["composite_scores"] = {
        "trend": {"value": _safe(trend_score), "state": _state(trend_score)},
        "momentum": {"value": _safe(momentum_score), "state": _state(momentum_score)},
        "risk": {"value": _safe(risk_score), "state": "high" if risk_score is not None and risk_score >= 65 else "low" if risk_score is not None and risk_score <= 35 else "medium" if risk_score is not None else "unavailable"},
        "agreement": {"state": agreement or "unavailable"},
    }
    return result
