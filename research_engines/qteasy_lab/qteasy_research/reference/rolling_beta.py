"""滚动 Beta：资产相对基准收益的多窗口 beta 时间序列。

纯逻辑实现；``pretrade/metrics.py`` 提供同名薄封装，避免 pretrade 反向
依赖 reference 包。行情清洗逻辑与 ``metrics.analyze_benchmark`` 保持一致
（sort / drop_duplicate / dropna）。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from qteasy_research.reference.config import ROLLING_BETA_WINDOWS


def _clean_close(frame: pd.DataFrame) -> pd.Series:
    """把 trade_date/close 行情清洗为以日期为索引的收盘价序列。"""
    if frame is None or frame.empty or not {"trade_date", "close"}.issubset(frame.columns):
        return pd.Series(dtype="float64")
    cleaned = frame.copy()
    cleaned["trade_date"] = pd.to_datetime(cleaned["trade_date"], errors="coerce")
    cleaned["close"] = pd.to_numeric(cleaned["close"], errors="coerce")
    cleaned = cleaned.dropna(subset=["trade_date", "close"])
    cleaned = cleaned.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    cleaned = cleaned[cleaned["close"] > 0]
    return cleaned.set_index("trade_date")["close"]


def _aligned_returns(
    asset_returns: pd.Series,
    benchmark_returns: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    """对齐公共索引并 dropna，返回 (asset, benchmark) 收益序列。"""
    frame = pd.concat(
        [asset_returns.rename("asset"), benchmark_returns.rename("benchmark")], axis=1
    ).dropna()
    if frame.empty:
        return pd.Series(dtype="float64"), pd.Series(dtype="float64")
    return frame["asset"], frame["benchmark"]


def _safe_float(value: Any) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return float(value)


def rolling_beta(
    asset_returns: pd.Series,
    benchmark_returns: pd.Series,
    windows: tuple[int, ...] = ROLLING_BETA_WINDOWS,
) -> dict[str, pd.Series]:
    """返回 ``{window: Series(beta 时间序列)}``；var==0 的窗口 beta=NaN。"""
    asset, bench = _aligned_returns(asset_returns, benchmark_returns)
    result: dict[str, pd.Series] = {}
    for window in windows:
        min_periods = max(window // 2, 2)
        cov = asset.rolling(window, min_periods=min_periods).cov(bench)
        var = bench.rolling(window, min_periods=min_periods).var()
        beta = cov / var
        # var==0（基准无波动）时置 NaN，避免除零后误判 beta=inf。
        beta = beta.where(var > 0, np.nan)
        result[str(window)] = beta
    return result


def rolling_beta_summary(
    asset_frame: pd.DataFrame,
    benchmark_frame: pd.DataFrame,
    windows: tuple[int, ...] = ROLLING_BETA_WINDOWS,
) -> dict[str, Any]:
    """资产/基准行情（trade_date/close）→ 各窗口 latest beta + 重叠天数。"""
    asset = _clean_close(asset_frame)
    bench = _clean_close(benchmark_frame)
    if asset.empty or bench.empty:
        return {"available": False, "reason": "标的或基准行情缺失"}
    returns = pd.concat([asset.rename("asset"), bench.rename("benchmark")], axis=1)
    returns = returns.pct_change().dropna()
    if len(returns) < 2:
        return {"available": False, "reason": "标的与基准没有足够重叠交易日"}
    series = rolling_beta(returns["asset"], returns["benchmark"], windows)
    latest: dict[str, float | None] = {}
    start: dict[str, str | None] = {}
    for window, beta_series in series.items():
        valid = beta_series.dropna()
        latest[window] = _safe_float(valid.iloc[-1]) if not valid.empty else None
        first = beta_series.first_valid_index()
        start[window] = first.date().isoformat() if first is not None else None
    return {
        "available": True,
        "overlap_days": int(len(returns)),
        "data_asof": asset.index.max().date().isoformat(),
        "series_start": start,
        "latest_beta": latest,
    }


def multi_benchmark_beta(
    asset_frame: pd.DataFrame,
    benchmark_frames: dict[str, pd.DataFrame],
    windows: tuple[int, ...] = ROLLING_BETA_WINDOWS,
) -> dict[str, dict[str, Any]]:
    """对多个基准（dict: name → 行情帧）输出 rolling_beta_summary。

    返回 ``{benchmark_name: summary}``；基准缺失的 summary 带 available=False。
    """
    result: dict[str, dict[str, Any]] = {}
    for name, bench_frame in benchmark_frames.items():
        result[name] = rolling_beta_summary(asset_frame, bench_frame, windows)
    return result
