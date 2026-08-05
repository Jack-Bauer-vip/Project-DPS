"""交易资产信息抓取：输入交易资产代码，返回名称、管理费、折溢价、跟踪误差。

桌面端"交易资产映射"表单在输入代码后调用本模块自动回填字段。数据来源分层：

- 名称 / 管理费：优先本地 `fund_basic.csv`（`name` / `m_fee`），无则回退
  Tushare `fund_basic` API（联网，需 TUSHARE_TOKEN）。
- 折溢价：AKShare `fund_etf_spot_em` 的 `基金折价率`（场内 ETF 实时，联网）。
- 跟踪误差：从本地 `fund_daily.csv`（基金日收益）与对应基准指数日收益的
  年化标准差计算；本地无对应基准指数时返回 None，提示人工填写。

所有联网抓取均 try/except 容错：失败时不抛异常，返回已获取的部分字段并
在 notes 中说明。本模块不自动写入数据库，只返回抓取结果供 UI 回填确认。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# 本地基金基础资料 CSV 的列名（fund_basic.csv，Tushare fund_basic 口径）
_FUND_BASIC_COLUMNS = {
    "ts_code": "ts_code",
    "name": "name",
    "m_fee": "m_fee",
    "benchmark": "benchmark",
}
# AKShare fund_etf_spot_em 的列名
_SPOT_COLUMN_MAP = {
    "代码": "code",
    "名称": "name",
    "基金折价率": "premium_discount",
    "最新价": "latest_price",
}


class TradeAssetFetchResult:
    """交易资产信息抓取结果（不自动落库，仅供回填确认）。"""

    def __init__(
        self,
        *,
        code: str,
        name: str | None = None,
        management_fee: float | None = None,
        premium_discount: float | None = None,
        tracking_error: float | None = None,
        notes: list[str] | None = None,
    ) -> None:
        self.code = code
        self.name = name
        self.management_fee = management_fee
        self.premium_discount = premium_discount
        self.tracking_error = tracking_error
        self.notes = notes or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "management_fee": self.management_fee,
            "premium_discount": self.premium_discount,
            "tracking_error": self.tracking_error,
            "notes": self.notes,
        }

    @property
    def filled(self) -> dict[str, Any]:
        """已成功抓取到的字段（供 UI 只回填非空值）。"""
        return {
            key: value
            for key, value in self.to_dict().items()
            if key not in ("code", "notes") and value is not None
        }


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str)
    except Exception:
        return pd.DataFrame()


def fetch_local_fund_basic(code: str, data_root: str | Path) -> dict[str, Any] | None:
    """从本地 fund_basic.csv 查名称与管理费。返回 None 表示本地无该基金。"""
    frame = _read_csv(Path(data_root) / "fund_basic.csv")
    if frame.empty or "ts_code" not in frame:
        return None
    match = frame[frame["ts_code"].astype(str).str.upper() == code.upper()]
    if match.empty:
        return None
    row = match.iloc[0]
    result: dict[str, Any] = {}
    if "name" in row and pd.notna(row.get("name")):
        result["name"] = str(row["name"])
    if "m_fee" in row and pd.notna(row.get("m_fee")):
        try:
            result["management_fee"] = float(row["m_fee"])
        except (TypeError, ValueError):
            pass
    if "benchmark" in row and pd.notna(row.get("benchmark")):
        result["benchmark"] = str(row["benchmark"])
    return result or None


def fetch_tushare_fund_basic(code: str) -> dict[str, Any] | None:
    """从 Tushare fund_basic API 查名称与管理费（联网，需 TUSHARE_TOKEN）。"""
    try:
        import os

        import tushare as ts
    except ImportError:
        return None
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token:
        return None
    try:
        api = ts.pro_api(token)
        base_url = os.getenv("TUSHARE_API_URL", "").strip()
        if base_url:
            api._DataApi__http_url = base_url
        frame = api.fund_basic(ts_code=code)
    except Exception:
        return None
    if frame is None or frame.empty:
        return None
    row = frame.iloc[0]
    result: dict[str, Any] = {}
    if "name" in row and pd.notna(row.get("name")):
        result["name"] = str(row["name"])
    if "m_fee" in row and pd.notna(row.get("m_fee")):
        try:
            result["management_fee"] = float(row["m_fee"])
        except (TypeError, ValueError):
            pass
    if "benchmark" in row and pd.notna(row.get("benchmark")):
        result["benchmark"] = str(row["benchmark"])
    return result or None


def fetch_premium_discount_akshare(code: str) -> float | None:
    """从 AKShare fund_etf_spot_em 获取场内 ETF 的基金折价率（%）。

    返回折溢价百分比（如 -7.5 表示折价 7.5%）。仅场内 ETF 适用；失败返回 None。
    """
    try:
        import akshare as ak
    except ImportError:
        return None
    try:
        frame = ak.fund_etf_spot_em()
    except Exception:
        return None
    if frame is None or frame.empty or "代码" not in frame:
        return None
    numeric = code.split(".", 1)[0]
    match = frame[frame["代码"].astype(str) == numeric]
    if match.empty or "基金折价率" not in match:
        return None
    value = match.iloc[0]["基金折价率"]
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_tracking_error(
    code: str,
    data_root: str | Path,
    *,
    benchmark_code: str | None = None,
    window_days: int = 252,
) -> float | None:
    """从本地基金/指数日收益计算年化跟踪误差（%）。

    跟踪误差 = 年化（基金日收益 − 基准指数日收益）的标准差。
    需要：
      - 基金日线（fund_daily.csv，字段 ts_code/trade_date/close）；
      - 基准指数日线（index_daily.csv，benchmark_code 对应代码）。
    基准指数代码未知或数据不足时返回 None（提示人工填写）。

    参数：
        code：基金代码（如 513500.SH）。
        data_root：数据根目录（含 fund_daily.csv / index_daily.csv）。
        benchmark_code：基准指数代码（如 000300.SH）；为 None 时尝试从
            fund_basic.csv 的 benchmark 文本解析，解析失败返回 None。
        window_days：跟踪误差计算窗口（默认近一年 252 个交易日）。
    """
    fund_frame = _read_csv(Path(data_root) / "fund_daily.csv")
    if fund_frame.empty or "ts_code" not in fund_frame:
        return None
    fund = _to_daily_returns(fund_frame, code)
    if fund is None:
        return None

    if benchmark_code is None:
        benchmark_code = _resolve_benchmark_code(code, data_root)
    if benchmark_code is None:
        return None

    index_frame = _read_csv(Path(data_root) / "index_daily.csv")
    if index_frame.empty or "ts_code" not in index_frame:
        return None
    index = _to_daily_returns(index_frame, benchmark_code)
    if index is None:
        return None

    # 对齐日期并合并，取窗口内的差值标准差
    merged = pd.concat(
        [fund.rename("fund"), index.rename("index")],
        axis=1,
        join="inner",
    ).dropna().tail(window_days)
    if len(merged) < 20:
        return None
    diff = merged["fund"] - merged["index"]
    annualized = float(diff.std(ddof=1) * np.sqrt(252) * 100)
    return round(annualized, 2)


def _to_daily_returns(frame: pd.DataFrame, code: str) -> pd.Series | None:
    """从行情帧提取某代码的日收益率序列（按 trade_date 排序）。"""
    sub = frame[frame["ts_code"].astype(str).str.upper() == code.upper()]
    if sub.empty or "close" not in sub or "trade_date" not in sub:
        return None
    series = pd.to_numeric(sub["close"], errors="coerce").dropna()
    if series.empty:
        return None
    return series.pct_change().dropna()


def _resolve_benchmark_code(code: str, data_root: str | Path) -> str | None:
    """尝试从 fund_basic.csv 的 benchmark 文本解析出本地指数代码。

    仅能识别中文字样 + 本地存在的指数代码（000300.SH/000852.SH/000905.SH）。
    无法可靠解析（如 QDII 的境外基准）时返回 None。
    """
    frame = _read_csv(Path(data_root) / "fund_basic.csv")
    if frame.empty or "ts_code" not in frame or "benchmark" not in frame:
        return None
    match = frame[frame["ts_code"].astype(str).str.upper() == code.upper()]
    if match.empty:
        return None
    benchmark_text = str(match.iloc[0].get("benchmark") or "")
    if not benchmark_text:
        return None
    # 本地存在的指数代码 → 中文名映射（精简）
    index_basic = _read_csv(Path(data_root) / "index_basic.csv")
    if index_basic.empty or "ts_code" not in index_basic or "name" not in index_basic:
        return None
    for _, row in index_basic.iterrows():
        index_code = str(row["ts_code"])
        index_name = str(row.get("name") or "")
        # 基准文本里出现指数名称（去掉"指数""收益率"等后缀再匹配）
        needle = index_name.replace("指数", "").replace("(全收益)", "")
        if needle and needle in benchmark_text:
            # 仅返回本地有日线的指数
            if _has_index_daily(index_code, data_root):
                return index_code
    return None


def _has_index_daily(index_code: str, data_root: str | Path) -> bool:
    frame = _read_csv(Path(data_root) / "index_daily.csv")
    if frame.empty or "ts_code" not in frame:
        return False
    return not frame[frame["ts_code"].astype(str) == index_code].empty


def fetch_trade_asset_info(
    code: str,
    data_root: str | Path,
) -> TradeAssetFetchResult:
    """抓取交易资产信息（名称/管理费/折溢价/跟踪误差）。

    分层容错：本地 fund_basic 优先；折溢价用 AKShare 在线；跟踪误差尝试本地
    计算。所有失败均不抛异常，写入 notes 说明。
    """
    result = TradeAssetFetchResult(code=code)
    normalized = code.strip().upper()

    # 名称 + 管理费：本地 CSV 优先，Tushare 回退
    local = fetch_local_fund_basic(normalized, data_root)
    if local:
        result.name = local.get("name")
        result.management_fee = local.get("management_fee")
    else:
        result.notes.append("本地 fund_basic.csv 无该基金基础资料")
        remote = fetch_tushare_fund_basic(normalized)
        if remote:
            result.name = remote.get("name")
            result.management_fee = remote.get("management_fee")
        else:
            result.notes.append("Tushare 未返回基础资料（需 TUSHARE_TOKEN 且联网）")

    # 折溢价：AKShare 场内 ETF 实时
    result.premium_discount = fetch_premium_discount_akshare(normalized)
    if result.premium_discount is None:
        result.notes.append("AKShare 未获取到折溢价（仅场内 ETF 支持）")

    # 跟踪误差：本地基金日收益 vs 基准指数
    result.tracking_error = compute_tracking_error(normalized, data_root)
    if result.tracking_error is None:
        result.notes.append("无法计算跟踪误差（本地缺基准指数数据），请人工填写")

    return result
