"""交易指纹（B1-3）：静态离线分析系统A交易流水与人工干预档案。

**性质**：只读 A 的 ``config/actual_trade_ledger.csv`` + ``config/manual_override.csv``；
A 若提供 ``data/logs/human_override_log.csv``（A1-3 规划）则一并纳入，缺失则跳过
不报错。**不写共享目录、不写 A 任何文件、不改变两系统运行。**

**纪律**：报告只用 ``strategy_id`` 标识符，措辞全部面向行为统计，中文策略名零出现
（``render_fingerprint_markdown`` 输出全 ASCII 以保证零中文）；严禁读取共享目录
``systemA_feedback/`` 中的 A 反馈文件作为分析依据（避免逻辑循环）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from qteasy_research.reference.config import (
    SYSTEM_A_HUMAN_OVERRIDE_LOG,
    SYSTEM_A_MANUAL_OVERRIDE,
    SYSTEM_A_TRADE_LEDGER,
    TRADER_FINGERPRINT_DIR,
)
from qteasy_research.reference.metadata import today_iso

# 合规交易侧：现金调整行（side==CASH）不参与成交统计。
_TRADE_SIDES = ("BUY", "SELL")
# 需人工确认的台账状态（非 CONFIRMED 进入审计）。
_CONFIRMED = "CONFIRMED"


def _read_ledger(path: str | object) -> pd.DataFrame:
    """读 A 交易台账（兼容 BOM 头），返回原始列。"""
    ledger = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    required = {"trade_date", "strategy_id", "side", "amount", "confirm_status"}
    missing = required.difference(ledger.columns)
    if missing:
        raise ValueError(f"actual_trade_ledger.csv 缺少字段：{sorted(missing)}")
    ledger["trade_date"] = pd.to_datetime(ledger["trade_date"], errors="coerce")
    ledger["amount"] = pd.to_numeric(ledger["amount"], errors="coerce").fillna(0.0)
    return ledger


def _read_overrides(path: str | object) -> pd.DataFrame:
    """读 A 人工干预表；缺失或缺列返回空 DataFrame。"""
    if path is None or not Path(path).exists():
        return pd.DataFrame()
    override = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    if override.empty:
        return override
    for column in ("date", "asset_id", "override_action", "expires_on"):
        if column not in override.columns:
            override[column] = None
    return override


def _read_human_log(path: str | object) -> pd.DataFrame | None:
    """读 A 人工干预审计日志；缺失返回 None（不报错，报告标 N/A）。"""
    if path is None or not Path(path).exists():
        return None
    return pd.read_csv(path, encoding="utf-8-sig", dtype=str)


def _strategy_fingerprint(trades: pd.DataFrame, cash_rows: int) -> dict:
    """逐策略交易行为统计（输入为含 BUY/SELL 的成交行）。"""
    trade_count = len(trades)
    buy_count = int((trades["side"] == "BUY").sum())
    sell_count = int((trades["side"] == "SELL").sum())
    total_amount = float(trades["amount"].sum())
    return {
        "trade_count": trade_count,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "cash_count": cash_rows,
        "buy_ratio": round(buy_count / trade_count, 4) if trade_count else None,
        "sell_ratio": round(sell_count / trade_count, 4) if trade_count else None,
        "cash_ratio": round(cash_rows / (trade_count + cash_rows), 4) if (trade_count + cash_rows) else None,
        "total_amount": round(total_amount, 2),
        "avg_amount": round(total_amount / trade_count, 2) if trade_count else None,
        "holding_days_median": _holding_days_median(trades),
        "turnover": _turnover(trades),
        "asset_count": int(trades["asset_id"].nunique()) if "asset_id" in trades.columns else 0,
        "top5_concentration": _top5_concentration(trades)["ratio"],
        "top5_assets": _top5_concentration(trades)["assets"],
    }


def _holding_days_median(trades: pd.DataFrame) -> float | None:
    """买入→卖出 FIFO 配对间隔的中位数（天）；无可配对返回 None。"""
    if trades.empty or "asset_id" not in trades.columns:
        return None
    frame = trades.sort_values("trade_date")
    intervals: list[int] = []
    for _, group in frame.groupby("asset_id"):
        buys: list[pd.Timestamp] = []
        for _, row in group.iterrows():
            if row["side"] == "BUY":
                buys.append(row["trade_date"])
            elif row["side"] == "SELL" and buys:
                intervals.append((row["trade_date"] - buys.pop(0)).days)
    return float(np.median(intervals)) if intervals else None


def _turnover(trades: pd.DataFrame) -> float | None:
    """换手率 = 成交额 / 期末估持仓（买入净额，取正值）；无法估计返回 None。"""
    if trades.empty:
        return None
    buy = float(trades.loc[trades["side"] == "BUY", "amount"].sum())
    sell = float(trades.loc[trades["side"] == "SELL", "amount"].sum())
    turnover_amount = buy + sell
    position = max(0.0, buy - sell)
    if position <= 0 or turnover_amount <= 0:
        return None
    return round(turnover_amount / position, 4)


def _top5_concentration(trades: pd.DataFrame) -> dict:
    """集中度：Top5 资产成交额占比。"""
    if trades.empty or "asset_id" not in trades.columns:
        return {"ratio": None, "assets": []}
    by_asset = trades.groupby("asset_id")["amount"].sum().sort_values(ascending=False)
    total = float(by_asset.sum())
    top5 = [str(code) for code in by_asset.head(5).index]
    ratio = round(float(by_asset.head(5).sum()) / total, 4) if total > 0 else None
    return {"ratio": ratio, "assets": top5}


def _override_profile(overrides: pd.DataFrame, ref_date: str | None = None) -> dict:
    """人工干预档案：按动作分布与 expires_on 状态（有效/已过期/无期限）。"""
    if overrides.empty:
        return {"total": 0, "by_action": {}, "active": 0, "expired": 0, "no_expiry": 0}
    ref = pd.Timestamp(ref_date or today_iso())
    action = overrides["override_action"].fillna("").astype(str)
    by_action: dict[str, int] = {}
    for value, count in action.value_counts().items():
        by_action[str(value)] = int(count)
    expiry = overrides["expires_on"].fillna("").astype(str)
    active = expired = no_expiry = 0
    for value in expiry:
        if not value:
            no_expiry += 1
            continue
        try:
            active += 1 if pd.Timestamp(value) >= ref else 0
            expired += 1 if pd.Timestamp(value) < ref else 0
        except Exception:
            no_expiry += 1
    return {"total": int(len(overrides)), "by_action": by_action,
            "active": active, "expired": expired, "no_expiry": no_expiry}


def _non_confirmed_audit(ledger: pd.DataFrame) -> list[dict]:
    """非 CONFIRMED 记录审计（当前台账全 CONFIRMED 时为空）。"""
    if "confirm_status" not in ledger.columns:
        return []
    bad = ledger.loc[ledger["confirm_status"].fillna("").astype(str) != _CONFIRMED]
    records: list[dict] = []
    for _, row in bad.iterrows():
        records.append({
            "trade_date": _safe_str(row.get("trade_date")),
            "strategy_id": _safe_str(row.get("strategy_id")),
            "asset_id": _safe_str(row.get("asset_id")),
            "side": _safe_str(row.get("side")),
            "confirm_status": _safe_str(row.get("confirm_status")),
            "notes": _safe_str(row.get("notes")),
        })
    return records


def _safe_str(value) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    return str(value)


def analyze_trader_fingerprint(
    ledger_path: str | object,
    override_path: str | object,
    human_log_path: str | object | None = None,
) -> dict:
    """分析系统A交易流水，返回结构化交易指纹（dict）。

    参数：
        ledger_path: A ``config/actual_trade_ledger.csv`` 路径。
        override_path: A ``config/manual_override.csv`` 路径。
        human_log_path: 可选 A ``data/logs/human_override_log.csv``，缺失跳过。

    返回：
        含 ``generated_date / ledger_rows / override_rows / human_log_rows /
        strategies / override_profile / non_confirmed`` 的 dict。
    """
    ledger = _read_ledger(ledger_path)
    overrides = _read_overrides(override_path)
    human_log = _read_human_log(human_log_path)

    strategies: dict[str, dict] = {}
    if "strategy_id" in ledger.columns:
        for sid, group in ledger.groupby("strategy_id"):
            trades = group.loc[group["side"].isin(_TRADE_SIDES)].copy()
            cash_rows = int((group["side"] == "CASH").sum())
            strategies[_safe_str(sid)] = _strategy_fingerprint(trades, cash_rows)

    return {
        "generated_date": today_iso(),
        "ledger_rows": int(len(ledger)),
        "override_rows": int(len(overrides)),
        "human_log_rows": None if human_log is None else int(len(human_log)),
        "strategies": strategies,
        "override_profile": _override_profile(overrides),
        "non_confirmed": _non_confirmed_audit(ledger),
    }


def render_fingerprint_markdown(fingerprint: dict) -> str:
    """把结构化指纹渲染为 Markdown。

    **全 ASCII 输出**（英文标签 + 标识符），从根上保证中文策略名零出现，
    便于自动化断言（机器输出无中文策略名）。
    """
    lines: list[str] = [
        "# Trader Fingerprint (static, read-only)",
        "",
        "- generated_date: " + str(fingerprint["generated_date"]),
        "- ledger_rows: " + str(fingerprint["ledger_rows"]),
        "- override_rows: " + str(fingerprint["override_rows"]),
        "- human_log_rows: " + _fmt_optional(fingerprint.get("human_log_rows")),
        "",
        "## Strategies",
        "",
    ]
    strategies = fingerprint.get("strategies", {})
    if not strategies:
        lines.append("(none)")
    for sid in sorted(strategies):
        profile = strategies[sid]
        lines.append("### " + sid)
        lines.append("")
        lines.append("| metric | value |")
        lines.append("|---|---|")
        metrics = [
            ("trade_count", profile["trade_count"]),
            ("buy_count", profile["buy_count"]),
            ("sell_count", profile["sell_count"]),
            ("cash_count", profile["cash_count"]),
            ("buy_ratio", _fmt_optional(profile["buy_ratio"])),
            ("sell_ratio", _fmt_optional(profile["sell_ratio"])),
            ("cash_ratio", _fmt_optional(profile["cash_ratio"])),
            ("total_amount", _fmt_optional(profile["total_amount"])),
            ("avg_amount", _fmt_optional(profile["avg_amount"])),
            ("holding_days_median", _fmt_optional(profile["holding_days_median"])),
            ("turnover", _fmt_optional(profile["turnover"])),
            ("asset_count", profile["asset_count"]),
            ("top5_concentration", _fmt_optional(profile["top5_concentration"])),
            ("top5_assets", ", ".join(profile["top5_assets"]) or ""),
        ]
        for key, value in metrics:
            lines.append(f"| {key} | {value} |")
        lines.append("")

    lines.append("## Override Profile")
    lines.append("")
    profile = fingerprint.get("override_profile", {})
    lines.append("- total: " + str(profile.get("total", 0)))
    by_action = profile.get("by_action", {})
    if by_action:
        for action, count in sorted(by_action.items()):
            lines.append(f"- action:{_fmt_optional(action)} count:{count}")
    else:
        lines.append("- by_action: (none)")
    lines.append(f"- active: {profile.get('active', 0)} / expired: {profile.get('expired', 0)} / no_expiry: {profile.get('no_expiry', 0)}")
    lines.append("")
    lines.append("## Non-Confirmed Records")
    lines.append("")
    non_confirmed = fingerprint.get("non_confirmed", [])
    if not non_confirmed:
        lines.append("(none)")
    else:
        lines.append("| trade_date | strategy_id | asset_id | side | confirm_status | notes |")
        lines.append("|---|---|---|---|---|---|")
        for record in non_confirmed:
            lines.append("| {} | {} | {} | {} | {} | {} |".format(
                record.get("trade_date", ""), record.get("strategy_id", ""),
                record.get("asset_id", ""), record.get("side", ""),
                record.get("confirm_status", ""), record.get("notes", "")))
        lines.append("")
    return "\n".join(lines) + "\n"


def _fmt_optional(value) -> str:
    if value is None:
        return "N/A"
    return str(value)


__all__ = [
    "analyze_trader_fingerprint",
    "render_fingerprint_markdown",
]
