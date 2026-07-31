"""独立于 qteasy 回测执行器的投前定量指标。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


WINDOWS = {
    "1m": 21,
    "3m": 63,
    "6m": 126,
    "1y": 252,
    "3y": 756,
}


def _safe_float(value: Any) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return float(value)


def _total_return(returns: pd.Series) -> float | None:
    if returns.empty:
        return None
    return _safe_float((1.0 + returns).prod() - 1.0)


def _annual_return(returns: pd.Series) -> float | None:
    if len(returns) < 60:
        return None
    total = _total_return(returns)
    years = len(returns) / 252.0
    if total is None or years <= 0 or 1.0 + total <= 0:
        return None
    return _safe_float((1.0 + total) ** (1.0 / years) - 1.0)


def _drawdown(returns: pd.Series) -> tuple[float | None, int | None, int | None]:
    if returns.empty:
        return None, None, None
    curve = (1.0 + returns).cumprod()
    peak = curve.cummax()
    drawdowns = curve / peak - 1.0
    trough = int(drawdowns.to_numpy().argmin())
    max_dd = float(drawdowns.iloc[trough])
    recovery = None
    for index in range(trough, len(drawdowns)):
        if drawdowns.iloc[index] >= 0:
            recovery = index - trough
            break
    return _safe_float(max_dd), recovery, trough


def _window(returns: pd.Series, days: int) -> dict[str, Any]:
    sample = returns.tail(days)
    total = _total_return(sample)
    annual = _annual_return(sample)
    volatility = _safe_float(sample.std(ddof=1) * np.sqrt(252)) if len(sample) >= 2 else None
    max_dd, recovery, _ = _drawdown(sample)
    sharpe = _safe_float(annual / volatility) if annual is not None and volatility else None
    downside = sample[sample < 0]
    downside_vol = _safe_float(downside.std(ddof=1) * np.sqrt(252)) if len(downside) >= 2 else None
    sortino = _safe_float(annual / downside_vol) if annual is not None and downside_vol else None
    calmar = _safe_float(annual / abs(max_dd)) if annual is not None and max_dd else None
    return {
        "sample_days": int(len(sample)),
        "total_return": total,
        "annual_return": annual,
        "annual_volatility": volatility,
        "max_drawdown": max_dd,
        "drawdown_recovery_days": recovery,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "confidence": "low" if len(sample) < 120 else ("medium" if len(sample) < 252 else "normal"),
    }


def _quality(frame: pd.DataFrame) -> dict[str, Any]:
    dates = pd.to_datetime(frame["trade_date"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    returns = close.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    return {
        "rows": int(len(frame)),
        "start_date": dates.min().strftime("%Y-%m-%d") if dates.notna().any() else None,
        "end_date": dates.max().strftime("%Y-%m-%d") if dates.notna().any() else None,
        "missing_close": int(close.isna().sum()),
        "duplicate_dates": int(dates.duplicated().sum()),
        "non_positive_close": int((close <= 0).sum()),
        "large_daily_moves": int((returns.abs() > 0.2).sum()),
        "sample_days": int(len(returns)),
        "annual_metrics_allowed": bool(len(returns) >= 60),
        "stable_annual_judgment": bool(len(returns) >= 252),
    }


def analyze_price_history(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty or not {"trade_date", "close"}.issubset(frame.columns):
        raise ValueError("行情必须包含 trade_date 和 close")
    frame = frame.copy().sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["trade_date", "close"])
    frame = frame[frame["close"] > 0]
    frame = frame.set_index("trade_date")
    returns = frame["close"].pct_change().replace([np.inf, -np.inf], np.nan).dropna()

    max_dd, recovery, _ = _drawdown(returns)
    monthly = frame["close"].resample("ME").last().pct_change().dropna()
    latest = frame["close"].iloc[-1]
    ma = {
        f"ma{days}": _safe_float(frame["close"].rolling(days).mean().iloc[-1])
        for days in (20, 60, 120, 250)
    }
    rolling = {
        f"return_{days}d": _safe_float(frame["close"].pct_change(days).iloc[-1])
        for days in (20, 60, 120, 252)
    }
    rolling.update({
        f"volatility_{days}d": _safe_float(returns.rolling(days).std().iloc[-1] * np.sqrt(252))
        for days in (20, 60, 120, 252)
    })
    q = _quality(frame.reset_index())
    return {
        "quality": q,
        "windows": {name: _window(returns, days) for name, days in WINDOWS.items()},
        "since_inception": _window(returns, len(returns)),
        "max_drawdown": max_dd,
        "drawdown_recovery_days": recovery,
        "var95": _safe_float(returns.quantile(0.05)),
        "es95": _safe_float(returns[returns <= returns.quantile(0.05)].mean()),
        "win_rate_daily": _safe_float((returns > 0).mean()),
        "win_rate_monthly": _safe_float((monthly > 0).mean()),
        "best_day": _safe_float(returns.max()),
        "worst_day": _safe_float(returns.min()),
        "best_month": _safe_float(monthly.max()),
        "worst_month": _safe_float(monthly.min()),
        "latest_close": _safe_float(latest),
        "moving_averages": ma,
        "rolling": rolling,
        "liquidity": {
            "average_amount": _safe_float(frame["amount"].mean()) if "amount" in frame else None,
            "median_amount": _safe_float(frame["amount"].median()) if "amount" in frame else None,
            "latest_amount": _safe_float(frame["amount"].iloc[-1]) if "amount" in frame else None,
            "average_volume": _safe_float(frame["vol"].mean()) if "vol" in frame else None,
        },
        "series": {
            "dates": [date.strftime("%Y-%m-%d") for date in frame.index],
            "close": [float(value) for value in frame["close"]],
            "returns": [float(value) for value in returns],
            "drawdown": [float(value) for value in ((frame["close"] / frame["close"].cummax()) - 1.0)],
        },
    }


def analyze_benchmark(asset_frame: pd.DataFrame, benchmark_frame: pd.DataFrame) -> dict[str, Any]:
    if asset_frame.empty or benchmark_frame.empty:
        return {"available": False, "reason": "标的或基准行情缺失"}
    asset = asset_frame[["trade_date", "close"]].copy()
    bench = benchmark_frame[["trade_date", "close"]].copy()
    asset["trade_date"] = pd.to_datetime(asset["trade_date"])
    bench["trade_date"] = pd.to_datetime(bench["trade_date"])
    asset = asset.set_index("trade_date")["close"].rename("asset")
    bench = bench.set_index("trade_date")["close"].rename("benchmark")
    joined = pd.concat([asset, bench], axis=1).dropna()
    returns = joined.pct_change().dropna()
    if len(returns) < 2:
        return {"available": False, "reason": "标的与基准没有足够重叠交易日"}
    a, b = returns["asset"], returns["benchmark"]
    beta = _safe_float(a.cov(b) / b.var()) if b.var() else None
    alpha = _safe_float((1 + a).prod() ** (252 / len(a)) - 1 - ((beta or 0) * ((1 + b).prod() ** (252 / len(b)) - 1)))
    excess = a - b
    stress = b <= b.quantile(0.05)
    return {
        "available": True,
        "overlap_days": int(len(returns)),
        "total_return": _total_return(a),
        "benchmark_total_return": _total_return(b),
        # 超额收益应比较两条累计净值，而不是把逐日简单差收益连乘。
        "excess_total_return": _safe_float((1.0 + a).prod() - (1.0 + b).prod()),
        "beta": beta,
        "alpha": alpha,
        "information_ratio": _safe_float(excess.mean() / excess.std(ddof=1) * np.sqrt(252)) if excess.std(ddof=1) else None,
        "correlation": _safe_float(a.corr(b)),
        "stress_correlation": _safe_float(a[stress].corr(b[stress])) if stress.sum() >= 2 else None,
        "tracking_difference": None,
        "tracking_error": None,
    }
