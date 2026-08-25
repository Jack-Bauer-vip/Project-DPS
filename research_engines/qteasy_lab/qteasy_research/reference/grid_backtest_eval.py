"""网格回测评估器（独立实现，不改 backtest_engine）。

对给定 A 股 ETF 标的，对比「锚点策略 × 间距乘子 × 极端倍数」组合的网格
回测绩效。按研究参考（a-share-etf-weekly-dynamic-grid-research, 2026-08-24）
约束建模 A 股 ETF 交易规则：

- T+1：当日买入份额次日才可卖，统计 T+1 阻塞次数。
- 涨跌停：涨停附近买单、跌停附近卖单阻止成交，统计阻塞次数。
- 最小 100 份：买入向下取整到整手。
- 佣金 = max(成交额×0.03%, 5 元)，无印花税（ETF）。
- 日线路径近似：高开→高→低→收 / 低开→低→高→收。
- 周度调参：周频动态锚周五收盘计算、下周一开盘生效。

锚点策略：
- fixed:         锚 = 首日几何均值（前 lookback 收盘），全程固定。
- weekly_dynamic: 每周五算原始锚（几何均值），滞后阈值 h + 单周最大移动 u，周一生效。
- floating:      锚随成交重置到最近成交档位价（成交硬重置）。

间距主公式（与 grid_suggestion v2 同口径）：
  spread = clamp(ATR20% × spread_multiplier × regime_mult, cost_floor, cap)
  极端档 spread = regular_spread × extreme_multiplier。

独立实现：不 import backtest_engine 内部状态，仅数值镜像 round_lot /
佣金口径；对日更 / 发包 / 既有回测流程零影响。REFERENCE_ONLY：输出仅供
人工研究，不构成自动采纳依据。机器产出全 ASCII，零中文策略名。
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from qteasy_research.reference.config import PROJECT_ROOT
from qteasy_research.reference.grid_metrics import (
    adx,
    atr_pct,
    fib_levels,
    market_regime,
    swing_levels,
    weekly_dynamic_anchor,
)

# ---- 数值口径镜像（不 import backtest_engine，避免依赖其内部状态）----
TRADE_LOT_SIZE = 100          # ETF 100 份/手
FEE_RATE = 0.0003             # 佣金 0.03%
MIN_COMMISSION = 5.0          # 最低佣金 5 元
STAMP_DUTY_RATE = 0.0         # ETF 无印花税
LIMIT_DEFAULT = 0.10          # 涨跌停默认 10%（创业板/科创板 20% 由 --limit 覆盖）
SPREAD_CAP = 0.05             # 间距上限 5%
COST_FLOOR_ABS = 0.0015       # 成本下限绝对项 0.15%
SLIPPAGE_BUFFER = 0.0003      # 滑点缓冲（PDF: 2s）
REGULAR_LEVELS = 4            # 常规档数/侧（PDF 基线）
EDGE_LEVELS = 2               # 极端档数/侧
WEEKLY_LOOKBACK = 20          # 周频动态锚回看交易日数
H_LAG = 0.005                 # 滞后阈值 h = 0.5%（PDF 方案）
U_MAX = 0.03                  # 单周最大移动 u = 3%（PDF 方案）
INITIAL_CASH = 50_000.0       # 初始现金
INITIAL_ASSET_VALUE = 50_000.0  # 初始资产市值（50/50 起始）
TARGET_WEIGHT = 0.5           # 库存偏离基准 50%


def round_lot(shares: float, lot: int = TRADE_LOT_SIZE) -> int:
    """买入向下取整到整手；lot<=1 返回整数原值（口径镜像 backtest_engine）。"""
    if lot <= 1:
        return int(shares)
    return int(math.floor(shares / lot) * lot)


def commission(notional: float) -> float:
    """单笔佣金 = max(成交额×0.03%, 5 元)；无印花税。"""
    return max(notional * FEE_RATE, MIN_COMMISSION)


def load_asset_frame(asset_id: str, data_path: str | Path | None = None) -> pd.DataFrame:
    """从 fund_daily.csv 读单标的日线（trade_date/open/high/low/close/pre_close）。"""
    path = Path(data_path) if data_path else PROJECT_ROOT / "data" / "fund_daily.csv"
    df = pd.read_csv(path, dtype={"ts_code": str})
    sub = df[df["ts_code"] == asset_id].copy()
    sub["trade_date"] = pd.to_datetime(sub["trade_date"], errors="coerce")
    sub = sub.dropna(subset=["trade_date", "close"]).sort_values("trade_date")
    cols = [c for c in ("trade_date", "open", "high", "low", "close", "pre_close", "vol")
            if c in sub.columns]
    return sub[cols].reset_index(drop=True)


def _anchor_value(
    closes: np.ndarray,
    highs: np.ndarray | None,
    lows: np.ndarray | None,
    vols: np.ndarray | None,
    method: str,
) -> float:
    """按锚取法计算锚值（fixed 初始锚 / weekly 重算共用）。

    取法（锚公式对比实验，REFERENCE_ONLY）：
      - geometric_mean：多周期(20/60/90/120)几何均值（有量 VWAP→SMA→中点，≥3 源），对齐生产主锚；
      - vwap：VWAP（近 20 日，有量）；无量 → SMA 兜底；
      - sma：60 日简单移动平均；
      - swing_mid：近 200 日摆动中点（swing_levels）；
      - fibonacci：摆动区间 0.618 回撤位（fib_levels）。
    历史不足 → 近 20 日均值兜底（不虚构）。
    """
    n = int(len(closes))
    if n == 0:
        return float("nan")
    close = np.asarray(closes, dtype=float)
    if method == "vwap":
        if vols is not None and float(np.nansum(vols)) > 0:
            return float((close * vols).sum() / float(vols.sum()))
        return float(close.mean())
    if method == "sma":
        k = min(60, n)
        return float(close[-k:].mean())
    if method in ("swing_mid", "fibonacci"):
        if highs is not None and lows is not None and len(highs) >= 2 and len(lows) >= 2:
            sw_high, sw_low, sw_mid = swing_levels(
                pd.Series(highs), pd.Series(lows), window=min(200, n)
            )
            if method == "swing_mid":
                if sw_mid is not None:
                    return float(sw_mid)
            else:
                if sw_high is not None and sw_low is not None and sw_high > sw_low:
                    for f in fib_levels(sw_low, sw_high):
                        if f["ratio"] == 0.618:
                            return float(f["level"])
        return float(close.mean())
    # geometric_mean：多周期几何均值（对齐 grid_suggestion._anchor_suggestion 语义）
    sources: list[float] = []
    for w in (20, 60, 90, 120):
        sub = close[-w:]
        if len(sub) < 10:
            continue
        if vols is not None:
            v = np.asarray(vols[-w:], dtype=float)
            mask = v > 0
            if mask.any():
                sources.append(float((sub[mask] * v[mask]).sum() / float(v[mask].sum())))
                continue
        sources.append(float(sub.mean()))
    logs = [math.log(v) for v in sources if v > 0]
    if len(logs) >= 3:
        return float(math.exp(sum(logs) / len(logs)))
    return float(close[-min(20, n):].mean())


def _spread(atr: float | None, spread_mult: float, lot_notional: float) -> float:
    """ATR 主公式（成本硬约束），与 grid_suggestion v2 同口径。

    floor = max(2×fee_rate + 2×min_commission/lot_notional + slippage_buffer,
                spread_floor_min)；lot_notional 为单笔名义金额（Q×P）。
    """
    if atr is None or atr <= 0:
        return COST_FLOOR_ABS
    raw = atr * spread_mult
    per_lot = 2 * MIN_COMMISSION / max(lot_notional, 1.0)
    floor = max(2 * FEE_RATE + per_lot + SLIPPAGE_BUFFER, COST_FLOOR_ABS)
    return min(max(raw, floor), SPREAD_CAP)


@dataclass
class BacktestResult:
    """单组合回测结果。"""
    anchor_strategy: str
    spread_multiplier: float
    extreme_multiplier: float
    regular_spread: float
    edge_spread: float
    n_days: int
    net_return: float
    annual_return: float
    max_drawdown: float
    buy_trades: int
    sell_trades: int
    total_commission: float
    avg_asset_weight: float
    avg_inventory_deviation: float
    t1_blocks: int
    limit_blocks: int
    annual_turnover: float
    profit_ratio: float
    benchmark_return: float
    excess_return: float
    equity_series: list[float] = field(default_factory=list)
    error: str | None = None


def run_grid_backtest(
    frame: pd.DataFrame,
    anchor_strategy: str,
    spread_multiplier: float,
    extreme_multiplier: float,
    limit: float = LIMIT_DEFAULT,
    initial_cash: float = INITIAL_CASH,
    initial_asset_value: float = INITIAL_ASSET_VALUE,
    anchor_method: str = "geometric_mean",
) -> BacktestResult:
    """单组合回放（Day 级）。frame 需含 trade_date/open/high/low/close。

    档位簿记模型：每侧每档在价格穿越时最多成交一次；价格回到锚的另一侧后
    重置该侧档位（可再次触发）。floating 锚每次成交重置到成交档位价。
    anchor_method：锚取法（geometric_mean/vwap/sma/swing_mid/fibonacci），
    决定初始锚与 weekly 重算的锚值计算；默认 geometric_mean 保持主锚语义。
    """
    n = len(frame)
    if n < 40:
        return BacktestResult(anchor_strategy, spread_multiplier, extreme_multiplier,
                              np.nan, np.nan, n, np.nan, np.nan, np.nan, 0, 0, np.nan,
                              np.nan, np.nan, 0, 0, np.nan, np.nan, np.nan, np.nan,
                              error="insufficient_history")
    closes = frame["close"].to_numpy(dtype=float)
    opens = frame["open"].to_numpy(dtype=float)
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    vols = frame["vol"].to_numpy(dtype=float) if "vol" in frame.columns else None
    prev_closes = np.roll(closes, 1)
    prev_closes[0] = opens[0]
    dates = frame["trade_date"].to_numpy()

    # 波幅输入：ATR20%（全样本尾部，与建议引擎 window 口径一致）
    atr = atr_pct(closes, highs, lows, period=20)
    total_levels = REGULAR_LEVELS + EDGE_LEVELS
    shares_per_trade = round_lot(initial_asset_value / (2 * total_levels) / closes[0])
    regular_spread = _spread(atr, spread_multiplier, shares_per_trade * closes[0])
    edge_spread = regular_spread * extreme_multiplier

    # 锚初始化：按锚取法计算（默认多周期几何均值）
    anchor = _anchor_value(closes, highs, lows, vols, anchor_method)
    pending_anchor: float | None = None  # 周五算好的新锚，周一应用

    # 仓位状态
    sellable = round_lot(initial_asset_value / closes[0])   # 初始 50/50 持仓，期初可卖
    pending_shares = 0
    cash = initial_cash
    total_buy_cost = 0.0
    total_sell_net = 0.0
    buy_trades = 0
    sell_trades = 0
    total_comm = 0.0
    t1_blocks = 0
    limit_blocks = 0
    triggered_sell: set[int] = set()   # 本侧已成交卖档
    triggered_buy: set[int] = set()    # 本侧已成交买档

    equity_series: list[float] = []
    weights: list[float] = []
    deviations: list[float] = []

    weeks = pd.Series(dates).dt.isocalendar().week.to_numpy()

    def buy_level_price(k: int) -> float:
        """第 k 档买单触发价（下行方向，锚下方）。"""
        if k <= REGULAR_LEVELS:
            return anchor * (1.0 - k * regular_spread)
        return anchor * (1.0 - REGULAR_LEVELS * regular_spread
                         - (k - REGULAR_LEVELS) * edge_spread)

    def sell_level_price(k: int) -> float:
        """第 k 档卖单触发价（上行方向，锚上方）。"""
        if k <= REGULAR_LEVELS:
            return anchor * (1.0 + k * regular_spread)
        return anchor * (1.0 + REGULAR_LEVELS * regular_spread
                         + (k - REGULAR_LEVELS) * edge_spread)

    def _try_sell(lo: float, hi: float, limit_down_px: float) -> None:
        """路径段 [lo, hi]：价格从下方穿越卖档 → 卖出 1 档（每档一次）。"""
        nonlocal cash, sellable, sell_trades, total_sell_net, total_comm
        nonlocal t1_blocks, limit_blocks, anchor, triggered_sell
        for k in range(1, total_levels + 1):
            if k in triggered_sell:
                continue
            sp = sell_level_price(k)
            if lo < sp <= hi:
                if sp <= limit_down_px:   # 档位价在跌停价之下，当日无法成交
                    limit_blocks += 1
                    continue
                if sellable <= 0:
                    t1_blocks += 1
                    continue
                shares = min(shares_per_trade, sellable)
                notional = shares * sp
                fee = commission(notional)
                cash += notional - fee
                total_sell_net += notional - fee
                total_comm += fee
                sellable -= shares
                sell_trades += 1
                triggered_sell.add(k)
                if anchor_strategy == "floating":
                    anchor = sp
                    triggered_sell.clear()
                    triggered_buy.clear()

    def _try_buy(lo: float, hi: float, limit_up_px: float) -> None:
        """路径段 [lo, hi]：价格从上方穿越买档 → 买入 1 档（每档一次）。"""
        nonlocal cash, pending_shares, buy_trades, total_buy_cost, total_comm
        nonlocal limit_blocks, anchor, triggered_buy
        for k in range(1, total_levels + 1):
            if k in triggered_buy:
                continue
            bp = buy_level_price(k)
            if lo <= bp < hi:
                if bp >= limit_up_px:     # 档位价在涨停价之上，当日无法成交
                    limit_blocks += 1
                    continue
                notional = shares_per_trade * bp
                fee = commission(notional)
                if cash < notional + fee:
                    continue
                shares = shares_per_trade
                cash -= notional + fee
                total_buy_cost += notional + fee
                total_comm += fee
                pending_shares += shares
                buy_trades += 1
                triggered_buy.add(k)
                if anchor_strategy == "floating":
                    anchor = bp
                    triggered_sell.clear()
                    triggered_buy.clear()

    for i in range(n):
        close_i = closes[i]
        open_i = opens[i]
        high_i = highs[i]
        low_i = lows[i]
        prev_close = prev_closes[i]
        limit_up_px = prev_close * (1.0 + limit)
        limit_down_px = prev_close * (1.0 - limit)

        # 周频动态锚：新 iso 周首日应用上周五计算的锚
        if anchor_strategy == "weekly_dynamic":
            if i > 0 and weeks[i] != weeks[i - 1] and pending_anchor is not None:
                anchor = pending_anchor

        # 日线路径近似：高开→高→低→收 / 低开→低→高→收（按穿越判定成交）
        if open_i >= prev_close:
            _try_sell(open_i, high_i, limit_down_px)      # 上行段：open→high
            _try_buy(low_i, high_i, limit_up_px)          # 下行段：high→low
            if close_i > low_i:
                _try_sell(low_i, close_i, limit_down_px)  # 收段：low→close（上行）
        else:
            _try_buy(low_i, open_i, limit_up_px)          # 下行段：open→low
            _try_sell(low_i, high_i, limit_down_px)       # 上行段：low→high
            if close_i < high_i:
                _try_buy(close_i, high_i, limit_up_px)    # 收段：high→close（下行）

        # 收盘：T+1 解锁 + 档位重置 + 记账
        sellable += pending_shares
        pending_shares = 0
        if close_i > anchor:
            triggered_buy.clear()     # 价格回到锚上方，买档可重新触发
        if close_i < anchor:
            triggered_sell.clear()    # 价格回到锚下方，卖档可重新触发
        holdings = sellable
        equity = cash + holdings * close_i
        equity_series.append(equity)
        if equity > 0:
            w = holdings * close_i / equity
            weights.append(w)
            deviations.append(abs(w - TARGET_WEIGHT))

        # 周频动态锚：本周最后一个交易日收盘算原始锚
        if anchor_strategy == "weekly_dynamic":
            is_last_of_week = (i == n - 1) or (weeks[i + 1] != weeks[i])
            if is_last_of_week:
                if anchor_method == "geometric_mean":
                    series = pd.Series(closes[max(0, i - WEEKLY_LOOKBACK + 1): i + 1])
                    res = weekly_dynamic_anchor(series, prev_anchor=anchor, h=H_LAG, u=U_MAX)
                    if res["anchor"] is not None:
                        pending_anchor = res["anchor"]
                else:
                    pending_anchor = _anchor_value(
                        closes[: i + 1],
                        highs[: i + 1],
                        lows[: i + 1],
                        vols[: i + 1] if vols is not None else None,
                        anchor_method,
                    )

    # 指标
    eq = np.asarray(equity_series, dtype=float)
    eq0 = eq[0] if len(eq) else np.nan
    eq_end = eq[-1] if len(eq) else np.nan
    net_return = eq_end / eq0 - 1.0 if (eq0 and eq0 > 0) else np.nan
    annual_return = (1.0 + net_return) ** (252.0 / n) - 1.0 if np.isfinite(net_return) else np.nan
    peak = np.maximum.accumulate(eq)
    drawdowns = eq / peak - 1.0
    max_dd = float(drawdowns.min()) if len(drawdowns) else np.nan
    avg_weight = float(np.mean(weights)) if weights else np.nan
    avg_dev = float(np.mean(deviations)) if deviations else np.nan
    avg_equity = float(np.mean(eq)) if len(eq) else np.nan
    annual_turnover = (2.0 * total_buy_cost / avg_equity) * (252.0 / n) if (avg_equity and avg_equity > 0) else np.nan
    profit_ratio = (total_sell_net - total_buy_cost) / total_buy_cost if total_buy_cost > 0 else np.nan

    # 同口径 buy-and-hold：初始 50/50，初始买入一次佣金，期末卖出一次佣金
    bh_shares = round_lot(initial_asset_value / closes[0])
    bh_fee = commission(bh_shares * closes[0])
    bh_cash = initial_cash
    bh_end_asset = bh_shares * closes[-1]
    bh_sell_fee = commission(bh_end_asset)
    bh_end = bh_cash + bh_end_asset - bh_sell_fee
    bh_return = bh_end / (initial_cash + initial_asset_value) - 1.0

    return BacktestResult(
        anchor_strategy=anchor_strategy,
        spread_multiplier=spread_multiplier,
        extreme_multiplier=extreme_multiplier,
        regular_spread=regular_spread,
        edge_spread=edge_spread,
        n_days=n,
        net_return=round(net_return, 6) if np.isfinite(net_return) else np.nan,
        annual_return=round(annual_return, 6) if np.isfinite(annual_return) else np.nan,
        max_drawdown=round(max_dd, 6) if np.isfinite(max_dd) else np.nan,
        buy_trades=buy_trades,
        sell_trades=sell_trades,
        total_commission=round(total_comm, 2),
        avg_asset_weight=round(avg_weight, 6) if np.isfinite(avg_weight) else np.nan,
        avg_inventory_deviation=round(avg_dev, 6) if np.isfinite(avg_dev) else np.nan,
        t1_blocks=t1_blocks,
        limit_blocks=limit_blocks,
        annual_turnover=round(annual_turnover, 6) if np.isfinite(annual_turnover) else np.nan,
        profit_ratio=round(profit_ratio, 6) if np.isfinite(profit_ratio) else np.nan,
        benchmark_return=round(bh_return, 6),
        excess_return=round(net_return - bh_return, 6) if np.isfinite(net_return) else np.nan,
        equity_series=[round(v, 4) for v in eq],
    )


def _result_row(r: BacktestResult) -> dict[str, object]:
    return {
        "anchor_strategy": r.anchor_strategy,
        "spread_multiplier": r.spread_multiplier,
        "extreme_multiplier": r.extreme_multiplier,
        "regular_spread": r.regular_spread,
        "edge_spread": r.edge_spread,
        "n_days": r.n_days,
        "net_return": r.net_return,
        "annual_return": r.annual_return,
        "max_drawdown": r.max_drawdown,
        "buy_trades": r.buy_trades,
        "sell_trades": r.sell_trades,
        "total_commission": r.total_commission,
        "avg_asset_weight": r.avg_asset_weight,
        "avg_inventory_deviation": r.avg_inventory_deviation,
        "t1_blocks": r.t1_blocks,
        "limit_blocks": r.limit_blocks,
        "annual_turnover": r.annual_turnover,
        "profit_ratio": r.profit_ratio,
        "benchmark_return": r.benchmark_return,
        "excess_return": r.excess_return,
        "error": r.error,
    }


def run_eval(
    asset_id: str,
    frame: pd.DataFrame,
    anchor_strategies: list[str],
    spread_mults: list[float],
    extreme_mults: list[float],
    limit: float = LIMIT_DEFAULT,
    anchor_method: str = "geometric_mean",
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """全组合扫描：返回 (summary_df, {anchor_strategy: matrix_df})。"""
    rows: list[dict[str, object]] = []
    for anchor in anchor_strategies:
        for sm in spread_mults:
            for em in extreme_mults:
                r = run_grid_backtest(frame, anchor, sm, em, limit=limit, anchor_method=anchor_method)
                rows.append(_result_row(r))
    summary = pd.DataFrame(rows)
    matrices: dict[str, pd.DataFrame] = {}
    for anchor in anchor_strategies:
        sub = summary[summary["anchor_strategy"] == anchor]
        mat = sub.pivot_table(index="extreme_multiplier", columns="spread_multiplier",
                              values="net_return", aggfunc="first")
        matrices[anchor] = mat
    return summary, matrices


# ---------------------------------------------------------------------------
# 滚动样本外验证（weekly_dynamic 候选主锚 → 正式主锚的判定裁判）
# ---------------------------------------------------------------------------

@dataclass
class RollingWindowConfig:
    """滚动样本外验证配置。

    window_size=预热交易日数（定锚/初始化），out_of_sample=评估段长度，
    step=滑窗步长，min_windows=最少窗口数（不足判证据不足）。
    anchors/spread/extreme 固定参数组合（不扫描全组合，控计算量）。
    """
    window_size: int = 750
    out_of_sample: int = 126
    step: int = 22
    min_windows: int = 10
    anchors: tuple[str, ...] = ("fixed", "weekly_dynamic")
    spread_multiplier: float = 1.5
    extreme_multiplier: float = 1.0
    limit: float = LIMIT_DEFAULT
    anchor_method: str = "geometric_mean"


def _eval_window_return(r: BacktestResult, eval_days: int) -> float:
    """从回测 equity_series 提取评估段（最后 eval_days 交易日）收益。"""
    eq = np.asarray(r.equity_series, dtype=float)
    if len(eq) < eval_days + 1:
        return float("nan")
    base = eq[len(eq) - 1 - eval_days]
    end = eq[-1]
    return float(end / base - 1.0) if base > 0 else float("nan")


def _window_regime(window: pd.DataFrame) -> str:
    """窗口市场状态（ADX×ATR 四象限）。"""
    close = pd.to_numeric(window["close"], errors="coerce").dropna()
    high = pd.to_numeric(window["high"], errors="coerce").dropna()
    low = pd.to_numeric(window["low"], errors="coerce").dropna()
    if len(close) < 20 or len(high) < 20 or len(low) < 20:
        return "unknown"
    a = atr_pct(close, high, low, period=20)
    adx_v = adx(high, low, close, period=14)
    regime, _ = market_regime(adx_v, a)
    return regime


def run_rolling_validation(
    asset_id: str,
    frame: pd.DataFrame,
    cfg: RollingWindowConfig,
) -> dict:
    """滚动样本外验证：滑窗「预热 750 + 评估 126」→ 每窗口回测 anchors → 汇总判定。

    判定标准（用户拍板）：
      1. 窗口胜率：weekly_dynamic 评估段收益 > fixed 的窗口比例 ≥60%；
      2. 象限超额：ADX 四象限中 ≥3 象限 weekly 相对 fixed 平均超额为正；
      3. 最差窗口：weekly - fixed 超额最小值 > -5%。
    全部满足 → weekly_dynamic 升正式主锚；否则维持候选。
    """
    n = len(frame)
    total = cfg.window_size + cfg.out_of_sample
    rows: list[dict[str, object]] = []
    start = 0
    while start + total <= n:
        window = frame.iloc[start:start + total]
        res: dict[str, BacktestResult] = {}
        for anchor in cfg.anchors:
            res[anchor] = run_grid_backtest(
                window, anchor, cfg.spread_multiplier, cfg.extreme_multiplier,
                limit=cfg.limit, anchor_method=cfg.anchor_method,
            )
        regime = _window_regime(window.iloc[:cfg.window_size])
        weekly_net = _eval_window_return(res["weekly_dynamic"], cfg.out_of_sample)
        fixed_net = _eval_window_return(res["fixed"], cfg.out_of_sample)
        excess = (
            weekly_net - fixed_net
            if np.isfinite(weekly_net) and np.isfinite(fixed_net)
            else float("nan")
        )
        rows.append({
            "window_idx": len(rows),
            "window_start": str(window.iloc[0]["trade_date"])[:10],
            "window_end": str(window.iloc[cfg.window_size - 1]["trade_date"])[:10],
            "eval_start": str(window.iloc[cfg.window_size]["trade_date"])[:10],
            "eval_end": str(window.iloc[-1]["trade_date"])[:10],
            "regime": regime,
            "weekly_net": round(weekly_net, 6) if np.isfinite(weekly_net) else None,
            "fixed_net": round(fixed_net, 6) if np.isfinite(fixed_net) else None,
            "weekly_excess_vs_fixed": round(excess, 6) if np.isfinite(excess) else None,
        })
        start += cfg.step

    df = pd.DataFrame(rows)
    verdict: dict[str, object] = {"pass": False}
    if len(df) >= cfg.min_windows:
        valid = df.dropna(subset=["weekly_excess_vs_fixed"])
        if len(valid):
            win_rate = float((valid["weekly_excess_vs_fixed"] > 0).mean())
            reg_avg = valid.groupby("regime")["weekly_excess_vs_fixed"].mean()
            pos_quadrants = int((reg_avg > 0).sum())
            total_quadrants = int(reg_avg.notna().sum())
            worst = float(valid["weekly_excess_vs_fixed"].min())
        else:
            win_rate, pos_quadrants, total_quadrants, worst = float("nan"), 0, 0, float("nan")
        criteria = {
            "win_rate": round(win_rate, 4) if np.isfinite(win_rate) else None,
            "win_rate_pass": np.isfinite(win_rate) and win_rate >= 0.60,
            "positive_quadrants": pos_quadrants,
            "total_quadrants": total_quadrants,
            "quadrant_pass": total_quadrants >= 4 and pos_quadrants >= 3,
            "worst_window_excess": round(worst, 4) if np.isfinite(worst) else None,
            "worst_pass": np.isfinite(worst) and worst > -0.05,
        }
        passed = bool(
            criteria["win_rate_pass"] and criteria["quadrant_pass"] and criteria["worst_pass"]
        )
        verdict = {
            "criteria": criteria,
            "pass": passed,
            "decision": ("weekly_dynamic -> FORMAL anchor" if passed
                         else "weekly_dynamic -> keep candidate"),
        }
    else:
        verdict = {
            "criteria": None,
            "pass": False,
            "decision": f"insufficient windows ({len(df)} < {cfg.min_windows})",
        }
    return {"asset_id": asset_id, "rows": df, "verdict": verdict}


def _fmt_pct(v: float) -> str:
    return f"{v * 100:.2f}%" if np.isfinite(v) else "-"


def write_report(
    asset_id: str,
    summary: pd.DataFrame,
    matrices: dict[str, pd.DataFrame],
    run_dir: Path,
    limit: float,
) -> Path:
    """写 summary CSV + 敏感性矩阵 CSV + markdown 报告。返回报告路径。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(run_dir / f"summary_{asset_id}.csv", index=False)
    for anchor, mat in matrices.items():
        mat.to_csv(run_dir / f"matrix_{asset_id}_{anchor}.csv")
    report = run_dir / f"report_{asset_id}.md"

    n_days = int(summary["n_days"].iloc[0]) if len(summary) else 0
    best_idx = summary["excess_return"].fillna(-999).idxmax()
    best = summary.loc[best_idx] if len(summary) else None

    lines: list[str] = []
    lines.append(f"# Grid Backtest Eval: {asset_id}")
    lines.append("")
    lines.append(f"- Sample: {n_days} trading days | limit +/-{limit*100:.0f}% | "
                 f"fee={FEE_RATE*100:.2f}% min={MIN_COMMISSION:.0f} CNY | lot={TRADE_LOT_SIZE}")
    lines.append(f"- Spread formula: clamp(ATR20% x mult x regime, cost_floor, cap); "
                 f"edge = regular x extreme_mult")
    lines.append(f"- Anchors: fixed / weekly_dynamic(h={H_LAG},u={U_MAX}) / floating")
    lines.append(f"- Benchmark: same-initial 50/50 buy-and-hold (incl. commission)")
    lines.append("- REFERENCE_ONLY: research output, not an auto-adopt signal.")
    if best is not None:
        lines.append(f"- Best by excess return: anchor={best['anchor_strategy']} "
                     f"spread_mult={best['spread_multiplier']} "
                     f"extreme_mult={best['extreme_multiplier']} "
                     f"net={_fmt_pct(best['net_return'])} "
                     f"vs bench={_fmt_pct(best['benchmark_return'])} "
                     f"(excess {_fmt_pct(best['excess_return'])})")
    lines.append("")
    lines.append("## Per-combination summary")
    lines.append("")
    show = summary[["anchor_strategy", "spread_multiplier", "extreme_multiplier",
                    "net_return", "max_drawdown", "buy_trades", "sell_trades",
                    "total_commission", "avg_asset_weight", "avg_inventory_deviation",
                    "t1_blocks", "limit_blocks", "annual_turnover",
                    "benchmark_return", "excess_return"]].copy()
    for c in show.columns:
        if c in ("anchor_strategy",):
            continue
        if show[c].dtype == float:
            show[c] = show[c].apply(lambda v: f"{v:.4f}" if np.isfinite(v) else "-")
    lines.append("| " + " | ".join(show.columns) + " |")
    lines.append("|" + "|".join(["---"] * len(show.columns)) + "|")
    for _, row in show.iterrows():
        lines.append("| " + " | ".join(str(v) for v in row) + " |")
    lines.append("")
    lines.append("## Sensitivity matrices (net_return) — rows=extreme_mult, cols=spread_mult")
    for anchor, mat in matrices.items():
        lines.append("")
        lines.append(f"### {anchor}")
        lines.append("")
        lines.append("```")
        lines.append(mat.to_string())
        lines.append("```")
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    lines.append("- Daily OHLC cannot reproduce intraday order flow, queue, bid/ask "
                 "spread or limit-board sealing; path approximation is research-grade.")
    lines.append("- Sample may be short; short-sample best parameters must NOT be fixed "
                 "as universal defaults (see grid_anchor_spread_upgrade.md).")
    lines.append("- Commission is a demo rate; replace with actual broker fee schedule.")
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grid backtest evaluator (B-side, REFERENCE_ONLY)")
    parser.add_argument("--asset", required=True, help="asset id, e.g. 513520.SH")
    parser.add_argument("--anchor", default="fixed,weekly_dynamic,floating",
                        help="comma list of anchor strategies")
    parser.add_argument("--spread-mults", default="0.5,0.75,1.0,1.25,1.5,2.0",
                        help="comma list of spread multipliers (ATR K)")
    parser.add_argument("--extreme-mults", default="1.0,1.5,2.0,3.0",
                        help="comma list of extreme multipliers")
    parser.add_argument("--limit", type=float, default=LIMIT_DEFAULT,
                        help="price limit ratio (default 0.10; ChiNext 0.20)")
    parser.add_argument("--data", default=None, help="path to fund_daily.csv")
    parser.add_argument("--run", default=None, help="run dir name (default auto)")
    parser.add_argument("--mode", default="scan", choices=["scan", "rolling"],
                        help="scan=全组合扫描（默认）；rolling=滚动样本外验证")
    parser.add_argument("--window-size", type=int, default=750,
                        help="rolling: 预热窗口交易日数")
    parser.add_argument("--out-of-sample", type=int, default=126,
                        help="rolling: 样本外评估段长度")
    parser.add_argument("--step", type=int, default=22,
                        help="rolling: 滑窗步长")
    parser.add_argument("--min-windows", type=int, default=10,
                        help="rolling: 最少窗口数（不足判证据不足）")
    parser.add_argument("--anchor-method", default="geometric_mean",
                        choices=["geometric_mean", "vwap", "sma", "swing_mid", "fibonacci"],
                        help="锚取法（默认 geometric_mean 主锚语义）")
    args = parser.parse_args(argv)

    frame = load_asset_frame(args.asset, args.data)
    if frame.empty:
        print(f"ERROR: no data for asset {args.asset}")
        return 1
    anchors = [s.strip() for s in args.anchor.split(",") if s.strip()]
    spread_mults = [float(x) for x in args.spread_mults.split(",") if x.strip()]
    extreme_mults = [float(x) for x in args.extreme_mults.split(",") if x.strip()]

    if args.mode == "rolling":
        cfg = RollingWindowConfig(
            window_size=args.window_size, out_of_sample=args.out_of_sample,
            step=args.step, min_windows=args.min_windows, limit=args.limit,
            anchor_method=args.anchor_method,
        )
        result = run_rolling_validation(args.asset, frame, cfg)
        run_name = args.run or f"{args.asset}_rolling_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        run_dir = PROJECT_ROOT / "reports" / "grid_backtest_eval" / run_name
        run_dir.mkdir(parents=True, exist_ok=True)
        result["rows"].to_csv(run_dir / f"rolling_windows_{args.asset}.csv", index=False)
        verdict = result["verdict"]
        lines = [
            f"# Rolling Validation: {args.asset}",
            "",
            f"- config: window={cfg.window_size} oos={cfg.out_of_sample} step={cfg.step} "
            f"anchors={cfg.anchors} spread={cfg.spread_multiplier}",
            f"- windows: {len(result['rows'])}",
            "",
        ]
        crit = verdict.get("criteria")
        if crit:
            lines.append(f"- win_rate (weekly>fixed): {crit['win_rate']} -> "
                         f"{'PASS' if crit['win_rate_pass'] else 'FAIL'} (>=60%)")
            lines.append(f"- positive quadrants: {crit['positive_quadrants']}/{crit['total_quadrants']} "
                         f"-> {'PASS' if crit['quadrant_pass'] else 'FAIL'} (>=3/4)")
            lines.append(f"- worst window excess: {crit['worst_window_excess']} -> "
                         f"{'PASS' if crit['worst_pass'] else 'FAIL'} (>-5%)")
        lines += ["", f"**DECISION: {verdict.get('decision')}**", "",
                  "## Per-window", "",
                  "| idx | window_start | eval_start | eval_end | regime | weekly | fixed | excess |",
                  "|---|---|---|---|---|---|---|---|"]
        for _, r in result["rows"].iterrows():
            lines.append(
                f"| {int(r['window_idx'])} | {r['window_start']} | {r['eval_start']} | {r['eval_end']} "
                f"| {r['regime']} | {r['weekly_net']:.4f} | {r['fixed_net']:.4f} "
                f"| {r['weekly_excess_vs_fixed']:.4f} |"
            )
        report = run_dir / f"rolling_report_{args.asset}.md"
        report.write_text("\n".join(lines), encoding="utf-8")
        print(f"OK: rolling validation -> {run_dir}")
        print(f"report: {report}")
        print(f"verdict: {verdict.get('decision')}")
        return 0

    summary, matrices = run_eval(
        args.asset, frame, anchors, spread_mults, extreme_mults, args.limit,
        anchor_method=args.anchor_method,
    )
    run_name = args.run or f"{args.asset}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir = PROJECT_ROOT / "reports" / "grid_backtest_eval" / run_name
    report = write_report(args.asset, summary, matrices, run_dir, args.limit)
    print(f"OK: {len(summary)} combinations -> {run_dir}")
    print(f"report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
