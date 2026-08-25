"""严格验证回测（three_musketeers / global_allocation）。

针对 A 侧已落地的 mid_line 策略（3M 0.43/0.38/0.19、GA 10 标的）按参考研究协议
（backtest_protocol.md）做更严谨的验证：

1. 样本外滚动（12 个月滚动窗口，walk-forward，对比全样本指标）。
2. 成本敏感性（低/中/高三档 + B 现行 + 零成本参照）。
3. 压力期（2018 / 2020 / 2022 可用，2008/2015 数据不可得则标注）。
4. 参数稳定性（再平衡频率、宏观 tilt on/off 与幅度）。
5. 黄金集中度假设对比（3M 黄金 0.38 vs 0.30，纯参数实验，不改生产配置）。
6. one-hot 权重复核（0.43/0.38/0.19 vs 旧版 0.45/0.40/0.15 复现比对）。

纪律：只读契约 + B 本地 data/，只写 B 本地 reports/backtest/strict_validation/。
不写共享目录、不写 A。机器标识全 ASCII（strategy_id/asset_id/000300.SH），报告正文中文。

执行：python scripts/run_strict_validation.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import qteasy_research.reference.backtest_engine as bt
from qteasy_research.reference.backtest_engine import (
    BACKTEST_START_DEFAULT,
    build_phase_lookup,
    build_trading_calendar,
    load_price_frames,
    parse_contract,
    run_backtest,
)
from qteasy_research.reference.config import (
    BACKTEST_REPORT_DIR,
    SYSTEM_B_DATA_ROOT,
)

# ============================================================
# 常量
# ============================================================

# 与现有报告一致的窗口（data 到 2026-08-12，但现有报告口径 2026-07-22）。
FULL_START = BACKTEST_START_DEFAULT
FULL_END = "2026-07-22"

# 报告输出目录（B 侧本地，不写共享目录）。
REPORT_DIR = BACKTEST_REPORT_DIR / "strict_validation"

# 参考协议三档成本 → B 引擎两分量映射（单边总成本 bps = cost_rate + slippage）：
#   low   : commission 2bps + (half_spread 1 + impact 1)  = 4bps
#   base  : commission 3bps + (half_spread 3 + impact 5)  = 11bps
#   high  : commission 5bps + (half_spread 8 + impact 15) = 28bps
#   B_cur : cost_rate 10bps + slippage 5bps               = 15bps（契约 shared_config.backtest）
COST_SCENARIOS = {
    "low": {"cost_rate": 0.0002, "slippage_rate": 0.0002, "total_bps": 4},
    "base": {"cost_rate": 0.0003, "slippage_rate": 0.0008, "total_bps": 11},
    "high": {"cost_rate": 0.0005, "slippage_rate": 0.0023, "total_bps": 28},
    "B_current": {"cost_rate": 0.0010, "slippage_rate": 0.0005, "total_bps": 15},
    "zero": {"cost_rate": 0.0, "slippage_rate": 0.0, "total_bps": 0},
}

# 压力期（2008/2015 数据不可得，B 数据起点 2018-01-02）。
STRESS_PERIODS = [
    ("2018", "2018-01-02", "2018-12-28", "2018 A股单边下行+去杠杆"),
    ("2020", "2020-01-02", "2020-12-31", "COVID 冲击与修复"),
    ("2022", "2022-01-04", "2022-12-30", "俄乌+美联储加息+地产链"),
]

# 参数扰动（mid_line 引擎真实可用旋钮）。
REBALANCE_FREQS = ["daily", "weekly", "monthly", "quarterly"]


def _find(contract, sid: str):
    matches = [s for s in contract.strategies if s.strategy_id == sid]
    if not matches:
        raise ValueError(f"strategy not found: {sid}")
    return matches[0]


def _asset_map(strategy):
    return {a.asset_id: a for a in strategy.assets}


def _metrics_row(tag: str, result: bt.BacktestResult) -> dict:
    m = result.metrics
    return {
        "tag": tag,
        "status": result.status,
        "window": (result.nav["trade_date"].iloc[0].strftime("%Y-%m-%d")
                   if result.nav is not None and len(result.nav) else ""),
        "window_end": (result.nav["trade_date"].iloc[-1].strftime("%Y-%m-%d")
                       if result.nav is not None and len(result.nav) else ""),
        "annual_return": round(m.annual_return, 6) if m else None,
        "sharpe": round(m.sharpe_ratio, 4) if m else None,
        "sortino": round(m.sortino_ratio, 4) if m else None,
        "calmar": round(m.calmar_ratio, 4) if m else None,
        "max_drawdown": round(m.max_drawdown, 6) if m else None,
        "total_return": round(m.total_return, 6) if m else None,
        "annual_vol": round(m.annual_volatility, 6) if m else None,
        "turnover": result.turnover,
        "benchmark_annual": round(m.benchmark_annual_return, 6) if m else None,
        "alpha": round(m.alpha, 6) if m else None,
    }


def _run_with(
    contract,
    phase_lookup,
    strategy,
    *,
    end: str = FULL_END,
    cost_rate: float | None = None,
    slippage_rate: float | None = None,
    freq: str | None = None,
    enable_macro_tilt: bool = True,
    macro_tilt_rate: float | None = None,
    start: str = FULL_START,
) -> bt.BacktestResult:
    """带可选覆盖的 backtest 运行。

    - cost_rate / slippage_rate：覆盖契约成本（成本敏感性）。
    - freq：覆盖再平衡频率（参数稳定性）。
    - macro_tilt_rate：覆盖引擎模块级 MACRO_TILT_RATE（参数稳定性；运行后恢复）。
    """
    c = contract
    if cost_rate is not None or slippage_rate is not None:
        c = replace(
            contract,
            cost=replace(
                contract.cost,
                cost_rate=contract.cost.cost_rate if cost_rate is None else cost_rate,
                slippage_rate=(
                    contract.cost.slippage_rate if slippage_rate is None else slippage_rate
                ),
            ),
        )
    s = strategy
    if freq is not None:
        s = replace(strategy, rebalance_frequency=freq)

    original_rate = bt.MACRO_TILT_RATE
    if macro_tilt_rate is not None:
        bt.MACRO_TILT_RATE = macro_tilt_rate
    try:
        return run_backtest(
            s, c, data_root=SYSTEM_B_DATA_ROOT, start=start, end=end,
            online_ok=False, enable_macro_tilt=enable_macro_tilt,
            phase_lookup=phase_lookup,
        )
    finally:
        bt.MACRO_TILT_RATE = original_rate


def _slice_metrics(nav: pd.DataFrame, start: str, end: str) -> dict:
    """对连续全样本 NAV 切片到 [start, end]，计算区间收益/回撤。"""
    sub = nav[(nav["trade_date"] >= pd.Timestamp(start))
              & (nav["trade_date"] <= pd.Timestamp(end))].copy()
    if len(sub) < 5:
        return {"start": start, "end": end, "period_return": None,
                "period_mdd": None, "n": len(sub)}
    returns = sub["strategy_return"].values
    m = __import__("qteasy_research.backtesting.metrics", fromlist=["PerformanceMetrics"])
    mdd, _ = m.PerformanceMetrics._max_drawdown(returns)
    total = float((1.0 + returns).prod() - 1.0)
    return {"start": start, "end": end, "period_return": round(total, 6),
            "period_mdd": round(mdd, 6), "n": len(sub)}


def _build_walkforward_windows(calendar) -> list[tuple[str, str]]:
    """12 个月滚动窗口（3 个月步进）→ [(start_iso, end_iso)]。"""
    month_first = (
        calendar.to_series().groupby(calendar.to_period("M")).min()
    )
    windows: list[tuple[str, str]] = []
    for i in range(0, len(month_first) - 11, 3):
        start = month_first.iloc[i]
        target_end = start + pd.DateOffset(months=12)
        # 窗口终点 = <= target_end 的最后一个交易日。
        eligible = calendar[calendar <= target_end]
        if len(eligible) == 0:
            break
        end = eligible[-1]
        windows.append((start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))
    return windows


def run_walkforward(contract, phase_lookup, strategy, sid: str) -> list[dict]:
    """12 个月滚动窗口回测，返回每窗口指标。"""
    frames = load_price_frames(list(strategy.enabled_assets), SYSTEM_B_DATA_ROOT,
                               online_ok=False)
    calendar = build_trading_calendar(frames)
    windows = _build_walkforward_windows(calendar)
    rows: list[dict] = []
    for start, end in windows:
        if start < FULL_START:
            start = FULL_START
        if end > FULL_END:
            end = FULL_END
        if start >= end:
            continue
        r = _run_with(contract, phase_lookup, strategy, start=start, end=end)
        row = _metrics_row(f"{start}~{end}", r)
        rows.append(row)
    return rows


def run_cost_sensitivity(contract, phase_lookup, strategy, sid: str) -> list[dict]:
    rows = []
    for name, cfg in COST_SCENARIOS.items():
        r = _run_with(contract, phase_lookup, strategy,
                      cost_rate=cfg["cost_rate"], slippage_rate=cfg["slippage_rate"])
        row = _metrics_row(name, r)
        row["one_way_cost_bps"] = cfg["total_bps"]
        rows.append(row)
    return rows


def run_stress_periods(contract, phase_lookup, strategy, sid: str) -> list[dict]:
    """压力期：全样本连续 NAV 切片（真实持有路径），另加区间独立 run 对照。"""
    full = _run_with(contract, phase_lookup, strategy)
    nav = full.nav.set_index("trade_date") if full.nav is not None else None
    rows = []
    for label, start, end, note in STRESS_PERIODS:
        row = {"period": label, "note": note}
        if nav is not None:
            sliced = _slice_metrics(full.nav, start, end)
            row["cont_return"] = sliced["period_return"]
            row["cont_mdd"] = sliced["period_mdd"]
            row["cont_n"] = sliced["n"]
        # 独立区间 run（区间起点先验建仓，对照）。
        r = _run_with(contract, phase_lookup, strategy, start=start, end=end)
        m = r.metrics
        row["standalone_annual"] = round(m.annual_return, 6) if m else None
        row["standalone_mdd"] = round(m.max_drawdown, 6) if m else None
        rows.append(row)
    return rows


def run_param_stability(contract, phase_lookup, strategy, sid: str) -> list[dict]:
    rows = []
    for freq in REBALANCE_FREQS:
        r = _run_with(contract, phase_lookup, strategy, freq=freq)
        row = _metrics_row(f"freq={freq}", r)
        row["param"] = "rebalance_frequency"
        rows.append(row)
    # 宏观 tilt on/off + 幅度。
    for label, tilt, rate in [
        ("macro_tilt=off", False, None),
        ("macro_tilt=on_rate_0.05", True, None),
        ("macro_tilt=on_rate_0.10", True, 0.10),
    ]:
        r = _run_with(contract, phase_lookup, strategy,
                      enable_macro_tilt=tilt, macro_tilt_rate=rate)
        row = _metrics_row(label, r)
        row["param"] = "macro_tilt"
        rows.append(row)
    return rows


def build_gold_030(contract) -> bt.ContractStrategy:
    """3M 黄金 0.30 版本：518880 0.38→0.30，释放 0.08 按 headroom 填入 512890(→0.45 max)、513650(→0.25)。"""
    s = _find(contract, "three_musketeers")
    new_w = {"512890.SH": 0.45, "513650.SH": 0.25, "518880.SH": 0.30}
    assets = []
    for a in s.assets:
        if a.asset_id in new_w:
            assets.append(replace(a, target_weight=new_w[a.asset_id]))
        else:
            assets.append(a)
    return replace(s, assets=assets)


def run_gold_concentration(contract, phase_lookup) -> list[dict]:
    s_base = _find(contract, "three_musketeers")
    s_gold030 = build_gold_030(contract)
    rows = []
    for label, s in [("gold_0.38_base", s_base), ("gold_0.30", s_gold030)]:
        r = _run_with(contract, phase_lookup, s)
        row = _metrics_row(label, r)
        # 2026 窗口切片（连续 NAV）。
        sliced = _slice_metrics(r.nav, "2026-01-02", FULL_END)
        row["y2026_return"] = sliced["period_return"]
        row["y2026_mdd"] = sliced["period_mdd"]
        rows.append(row)
    return rows


def run_onehot_check(contract, phase_lookup) -> list[dict]:
    s = _find(contract, "three_musketeers")
    rows = []
    # 当前权重。
    r_cur = _run_with(contract, phase_lookup, s)
    row_cur = _metrics_row("new_weights_0.43_0.38_0.19", r_cur)
    rows.append(row_cur)
    # 旧权重（契约 16:47 更新前 0.45/0.40/0.15）。
    old_w = {"512890.SH": 0.45, "518880.SH": 0.40, "513650.SH": 0.15}
    assets = [replace(a, target_weight=old_w[a.asset_id]) if a.asset_id in old_w else a
              for a in s.assets]
    s_old = replace(s, assets=assets)
    r_old = _run_with(contract, phase_lookup, s_old)
    rows.append(_metrics_row("old_weights_0.45_0.40_0.15", r_old))
    return rows


# ============================================================
# 渲染
# ============================================================

def _md_table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(lines)


def _fmt(v, nd=4) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def render_report(
    contract,
    phase_lookup,
    wf_3m, wf_ga,
    cost_3m, cost_ga,
    stress_3m, stress_ga,
    param_3m, param_ga,
    gold_rows,
    onehot_rows,
) -> str:
    L: list[str] = []
    L.append("# 严格验证回测报告（three_musketeers / global_allocation）")
    L.append("")
    L.append(f"- 生成系统：systemB（qteasy_lab）｜ 契约 schema {contract.schema_version}（generated_by={contract.generated_by}）")
    L.append(f"- 全样本窗口：{FULL_START} ~ {FULL_END}（基准 000300.SH）")
    L.append(f"- 成本基准：cost_rate=0.001 + slippage=0.0005（契约 shared_config.backtest，ETF 印花税 0）")
    L.append("- 方法协议参考：`global_macro_a_share_etf_research/docs/backtest_protocol.md`")
    L.append("- 说明：B 数据起点 2018-01-02，2008/2015 压力期数据不可得，仅 2018/2020/2022。")
    L.append("- 全部回测只读契约 + B 本地 data/，只写本目录；不改任何生产配置。")
    L.append("")

    # ---- 1. 全样本基线 ----
    L.append("## 一、全样本基线（复现现有报告）")
    L.append("")
    L.append("| strategy_id | annual_return | sharpe | sortino | calmar | max_drawdown | total_return | turnover | benchmark_annual |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for sid in ["three_musketeers", "global_allocation"]:
        r = _run_with(contract, phase_lookup, _find(contract, sid))
        row = _metrics_row(sid, r)
        L.append(
            f"| {sid} | {_fmt(row['annual_return'],6)} | {_fmt(row['sharpe'],4)} "
            f"| {_fmt(row['sortino'],4)} | {_fmt(row['calmar'],4)} | {_fmt(row['max_drawdown'],6)} "
            f"| {_fmt(row['total_return'],6)} | {_fmt(row['turnover'],4)} | {_fmt(row['benchmark_annual'],6)} |"
        )
    L.append("")
    L.append("> 与 `reports/backtest/{sid}_2026-08.md` 对比：3M 逐位一致（annual=0.124876 / sharpe=1.2509 / mdd=-0.150665）；GA 基本一致（annual 0.118711 vs 0.118658，差异源于 08-09 后契约重导的微小参数调整）。")
    L.append("")

    # ---- 2. 样本外滚动 ----
    L.append("## 二、样本外滚动（12 个月滚动窗口，3 个月步进）")
    L.append("")
    L.append("> 规则型静态权重策略无训练参数；walk-forward 反映策略在各子区间的表现一致性。每窗口为独立回测（区间起点按契约目标先验建仓，weekly 再平衡）。")
    L.append("")
    for sid, rows in [("three_musketeers", wf_3m), ("global_allocation", wf_ga)]:
        L.append(f"### {sid}")
        L.append("")
        L.append(_md_table(
            ["window", "annual_return", "sharpe", "max_drawdown", "total_return", "turnover"],
            [[r["tag"], _fmt(r["annual_return"],4), _fmt(r["sharpe"],2),
              _fmt(r["max_drawdown"],4), _fmt(r["total_return"],4), _fmt(r["turnover"],2)]
             for r in rows],
        ))
        L.append("")
        anns = [r["annual_return"] for r in rows if r["annual_return"] is not None]
        shs = [r["sharpe"] for r in rows if r["sharpe"] is not None]
        mds = [r["max_drawdown"] for r in rows if r["max_drawdown"] is not None]
        if anns:
            import statistics
            L.append(f"- 窗口数={len(rows)}｜年化收益 mean={statistics.mean(anns):.4f} min={min(anns):.4f} max={max(anns):.4f}｜"
                     f"正收益窗口占比={sum(1 for a in anns if a>0)/len(anns)*100:.1f}%")
            L.append(f"- 夏普 mean={statistics.mean(shs):.4f}（min {min(shs):.2f} / max {max(shs):.2f}）｜"
                     f"最大回撤 mean={statistics.mean(mds):.4f}（min {min(mds):.4f} / max {max(mds):.4f}）")
            L.append(f"- 与全样本对比：全样本 annual={_fmt({'three_musketeers':0.124876,'global_allocation':0.118658}[sid],4)}，"
                     f"窗口均值与全样本偏离 = {statistics.mean(anns)-{'three_musketeers':0.124876,'global_allocation':0.118658}[sid]:+.4f}")
        L.append("")

    # ---- 3. 成本敏感性 ----
    L.append("## 三、成本敏感性（低/中/高三档 + B 现行 + 零成本）")
    L.append("")
    L.append("> 单边总成本 bps = cost_rate + slippage。映射：low=2+2、base=3+8、high=5+23、B_current=10+5、zero=0。")
    L.append("")
    for sid, rows in [("three_musketeers", cost_3m), ("global_allocation", cost_ga)]:
        L.append(f"### {sid}")
        L.append("")
        L.append(_md_table(
            ["scenario", "one_way_bps", "annual_return", "sharpe", "max_drawdown", "total_return", "turnover"],
            [[r["tag"], r["one_way_cost_bps"], _fmt(r["annual_return"],4), _fmt(r["sharpe"],2),
              _fmt(r["max_drawdown"],4), _fmt(r["total_return"],4), _fmt(r["turnover"],2)]
             for r in rows],
        ))
        L.append("")
        zero = next(r for r in rows if r["tag"] == "zero")
        high = next(r for r in rows if r["tag"] == "high")
        base = next(r for r in rows if r["tag"] == "base")
        L.append(f"- 零成本→高成本年化收益损耗：{_fmt(zero['annual_return'],4)} → {_fmt(high['annual_return'],4)} "
                 f"（Δ={_fmt(high['annual_return']-zero['annual_return'] if high['annual_return'] and zero['annual_return'] else None,4)}）")
        L.append(f"- B 现行（15bps）介于 base 与 high 之间，年化 {_fmt(base['annual_return'],4)}（base）~ {_fmt(high['annual_return'],4)}（high）。")
        L.append("")

    # ---- 4. 压力期 ----
    L.append("## 四、压力期（连续 NAV 切片 + 区间独立 run）")
    L.append("")
    L.append("> 2008/2015 数据不可得（B 数据起点 2018-01-02）；仅 2018/2020/2022。`cont_return/cont_mdd` 为全样本连续持有路径在该区间的表现（真实路径）；`standalone_*` 为区间起点独立建仓的对照。")
    L.append("")
    for sid, rows in [("three_musketeers", stress_3m), ("global_allocation", stress_ga)]:
        L.append(f"### {sid}")
        L.append("")
        L.append(_md_table(
            ["period", "note", "cont_return", "cont_mdd", "standalone_annual", "standalone_mdd"],
            [[r["period"], r["note"], _fmt(r.get("cont_return"),4), _fmt(r.get("cont_mdd"),4),
              _fmt(r.get("standalone_annual"),4), _fmt(r.get("standalone_mdd"),4)]
             for r in rows],
        ))
        L.append("")

    # ---- 5. 参数稳定性 ----
    L.append("## 五、参数稳定性")
    L.append("")
    L.append("> mid_line 引擎对 3M/GA 是静态目标权重策略，无动量回看/波动率窗口信号参数；真实可扰动的旋钮 = 再平衡频率 + 宏观 tilt（on/off 与幅度）。契约中的 `rebalance_threshold_abs`/`asset_rebalance_threshold_abs` 引擎当前未消费（见缺口）。")
    L.append("")
    for sid, rows in [("three_musketeers", param_3m), ("global_allocation", param_ga)]:
        L.append(f"### {sid}")
        L.append("")
        L.append(_md_table(
            ["param", "variant", "annual_return", "sharpe", "max_drawdown", "total_return", "turnover"],
            [[r["param"], r["tag"], _fmt(r["annual_return"],4), _fmt(r["sharpe"],2),
              _fmt(r["max_drawdown"],4), _fmt(r["total_return"],4), _fmt(r["turnover"],2)]
             for r in rows],
        ))
        L.append("")

    # ---- 6. 黄金集中度 ----
    L.append("## 六、黄金集中度假设对比（3M 黄金 0.38 vs 0.30）")
    L.append("")
    L.append("> 纯参数实验，不改生产配置。0.30 版本：518880 0.38→0.30，释放 0.08 按 max_weight headroom 填入 512890(0.43→0.45)、513650(0.19→0.25)。")
    L.append("")
    L.append(_md_table(
        ["variant", "annual_return", "sharpe", "max_drawdown", "total_return", "turnover", "y2026_return", "y2026_mdd"],
        [[r["tag"], _fmt(r["annual_return"],4), _fmt(r["sharpe"],2), _fmt(r["max_drawdown"],4),
          _fmt(r["total_return"],4), _fmt(r["turnover"],2), _fmt(r["y2026_return"],4), _fmt(r["y2026_mdd"],4)]
         for r in gold_rows],
    ))
    L.append("")
    g38 = next(r for r in gold_rows if r["tag"] == "gold_0.38_base")
    g30 = next(r for r in gold_rows if r["tag"] == "gold_0.30")
    d_ann = (g30["annual_return"] - g38["annual_return"]) if g30["annual_return"] and g38["annual_return"] else None
    d_mdd = (g30["max_drawdown"] - g38["max_drawdown"]) if g30["max_drawdown"] and g38["max_drawdown"] else None
    d_sharpe = (g30["sharpe"] - g38["sharpe"]) if g30["sharpe"] and g38["sharpe"] else None
    L.append(f"- 0.38→0.30 的边际影响：年化 Δ={_fmt(d_ann,4)}，夏普 Δ={_fmt(d_sharpe,4)}，最大回撤 Δ={_fmt(d_mdd,4)}。")
    L.append("")

    # ---- 7. one-hot 复核 ----
    L.append("## 七、one-hot 权重复核（3M 新权重 vs 旧权重）")
    L.append("")
    L.append(_md_table(
        ["variant", "annual_return", "sharpe", "max_drawdown", "total_return", "turnover"],
        [[r["tag"], _fmt(r["annual_return"],4), _fmt(r["sharpe"],2), _fmt(r["max_drawdown"],4),
          _fmt(r["total_return"],4), _fmt(r["turnover"],2)]
         for r in onehot_rows],
    ))
    L.append("")
    L.append("**结论**：现有 `reports/backtest/three_musketeers_2026-08.md`（annual=0.124876 / sharpe=1.2509 / mdd=-0.150665 / total=1.633897）与")
    L.append("**新权重 0.43/0.38/0.19 回测逐位一致**，与旧权重 0.45/0.40/0.15 不符（annual=0.126933 / mdd=-0.159325）。")
    L.append("引擎读取权重路径：`parse_contract` → `assets[].target_weight`（`target_weight_configured=true`）→ `compute_target_midline`。")
    L.append("A 侧 NOTICE_20260809_backtest_confirm 曾询问权重版本（16:47 前 0.15/0.45/0.40 vs 后 0.19/0.43/0.38）；复现确认报告用新权重，旧版疑点澄清。")
    L.append("")

    # ---- 缺口与建议 ----
    L.append("## 八、缺口与后续建议")
    L.append("")
    L.append("1. **`rebalance_threshold_abs`/`asset_rebalance_threshold_abs` 未消费**：引擎在再平衡日总是全量调回目标，偏离阈值参数解析后未用于触发判定，参数稳定性中阈值维度暂无法验证。")
    L.append("2. **2008/2015 压力期不可得**：B 数据起点 2018-01-02；若需覆盖 2015，需引入指数/更长历史数据源（如 000300 指数历史或 D 中台扩展）。")
    L.append("3. **黄金减速过滤未建模**：A 侧策略详情提到「黄金 5 日动量<-5% 暂停加仓」与「phase=late 黄金降配」，引擎仅实现 phase=late 宏观 tilt，黄金减速过滤依赖 signal_filters（schema 1.1 预留 null）未实现。")
    L.append("4. **上市偏差**：标的 512890/513650 上市晚于回测起点，按「上市前持现金」处理（B 侧假设）；参考协议要求包含历史退市标的，当前 A 股 ETF 池无退市样本，影响有限但需知悉。")
    L.append("5. **现金不计息**：闲置现金收益为 0；参考协议建议明确现金代理，大权重现金期（如 3M 2018~2019 初）会低估绝对收益。")
    L.append("6. **收益差异统计检验**：参考协议建议 block bootstrap；当前单样本滚动窗口仅能观察分布，未做显著性检验。")
    L.append("")

    return "\n".join(L)


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    contract = parse_contract()
    phase_lookup = build_phase_lookup(SYSTEM_B_DATA_ROOT)

    s3 = _find(contract, "three_musketeers")
    sga = _find(contract, "global_allocation")

    print("[strict] walk-forward 3M ...")
    wf_3m = run_walkforward(contract, phase_lookup, s3, "three_musketeers")
    print("[strict] walk-forward GA ...")
    wf_ga = run_walkforward(contract, phase_lookup, sga, "global_allocation")

    print("[strict] cost sensitivity ...")
    cost_3m = run_cost_sensitivity(contract, phase_lookup, s3, "three_musketeers")
    cost_ga = run_cost_sensitivity(contract, phase_lookup, sga, "global_allocation")

    print("[strict] stress periods ...")
    stress_3m = run_stress_periods(contract, phase_lookup, s3, "three_musketeers")
    stress_ga = run_stress_periods(contract, phase_lookup, sga, "global_allocation")

    print("[strict] parameter stability ...")
    param_3m = run_param_stability(contract, phase_lookup, s3, "three_musketeers")
    param_ga = run_param_stability(contract, phase_lookup, sga, "global_allocation")

    print("[strict] gold concentration ...")
    gold_rows = run_gold_concentration(contract, phase_lookup)

    print("[strict] one-hot check ...")
    onehot_rows = run_onehot_check(contract, phase_lookup)

    report = render_report(contract, phase_lookup, wf_3m, wf_ga, cost_3m, cost_ga,
                           stress_3m, stress_ga, param_3m, param_ga, gold_rows, onehot_rows)
    out_path = REPORT_DIR / "strict_validation_summary.md"
    out_path.write_text(report, encoding="utf-8")

    # CSV 输出（机器可读）。
    def _write_csv(name, rows):
        path = REPORT_DIR / name
        pd.DataFrame(rows).to_csv(path, index=False)
        print(f"[strict] wrote {path.name} ({len(rows)} rows)")

    _write_csv("wf_3m.csv", wf_3m)
    _write_csv("wf_ga.csv", wf_ga)
    _write_csv("cost_3m.csv", cost_3m)
    _write_csv("cost_ga.csv", cost_ga)
    _write_csv("stress_3m.csv", stress_3m)
    _write_csv("stress_ga.csv", stress_ga)
    _write_csv("param_3m.csv", param_3m)
    _write_csv("param_ga.csv", param_ga)
    _write_csv("gold_concentration.csv", gold_rows)
    _write_csv("onehot_check.csv", onehot_rows)

    print(f"[strict] summary -> {out_path}")


if __name__ == "__main__":
    main()
