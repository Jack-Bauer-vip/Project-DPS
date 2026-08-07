"""人机对比月报（human_machine_compare）：B 参考维度（应然） vs A 人工干预（实然）。

**性质**：B 本地离线分析工具。读 A 只读数据（``data/logs/human_override_log.csv`` +
``config/actual_trade_ledger.csv``）+ B 决策包（``decision_ref_package.json``）→ 产出
Markdown 月报 ``reports/human_machine_compare/{YYYY-MM}_hmc.md``。**不写共享目录、
不写 A 任何文件、不读 ``systemA_feedback/``。**

**口径（设计文档 ``docs/human_machine_compare_design.md``）**：
- B 参考维度是**资产级**，A 干预是**策略×资产级**：先按 ``asset_id`` join B 决策包，
  再按 ``strategy_id`` 分组；同一资产被多策略共享时 B 维度对该资产的所有干预一致。
- 方向一致性三分类：人工调增 vs B 看多 → ``agree``；调增 vs B 看空 → ``diverge``；
  无明确信号或 delta=0 → ``neutral``。
- 干预后表现：干预资产下月收益 vs 全资产同月均收益；样本不足 ``confidence="low"``。

**纪律**：机器输出全 ASCII，报告内只出现 ``strategy_id``/``asset_id``；中文仅限
``reason`` 原文引用与月报标题（reports 属 B 本地，允许中文标题）。策略名（三剑客/
网格等）**永不硬编码**。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

# human_override_log 必要列（顺序无关，解析后统一列序）。
_HUMAN_LOG_COLUMNS = ("time", "strategy_id", "asset_id", "old_weight", "new_weight", "reason")
# 干预后表现最小样本数（当月有收益的资产数）。
_MIN_POST_SAMPLES = 5
# 显著压力：情景 confidence 非 low 且 pnl_pct（百分比数值）≤ -1.0（月亏超 1%）。
_STRESS_PNL_THRESHOLD = -1.0


# ---------------------------------------------------------------- 数据读取


def parse_human_override_log(path: str | Path | None) -> pd.DataFrame:
    """读 A 人工干预审计日志（逗号分隔 CSV，兼容 BOM/CRLF）。

    缺失返回空 DataFrame（不报错）；缺必要列抛 ``ValueError``；时间/权重无法解析
    的行保留（``NaT``/``NaN``），由调用方在明细中降级标注，不虚构。
    """
    if path is None or not Path(path).exists():
        return pd.DataFrame(columns=list(_HUMAN_LOG_COLUMNS))
    frame = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    missing = set(_HUMAN_LOG_COLUMNS).difference(frame.columns)
    if missing:
        raise ValueError(f"human_override_log.csv 缺少字段：{sorted(missing)}")
    frame = frame[list(_HUMAN_LOG_COLUMNS)].copy()
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    frame["old_weight"] = pd.to_numeric(frame["old_weight"], errors="coerce")
    frame["new_weight"] = pd.to_numeric(frame["new_weight"], errors="coerce")
    frame["month"] = frame["time"].dt.strftime("%Y-%m")
    return frame.reset_index(drop=True)


# ---------------------------------------------------------------- B 信号与分类


def _red_flag_blocked(red_flag: dict | None) -> bool:
    """红/橙牌视为风险阻断；yellow 与 None 不阻断。"""
    if not red_flag:
        return False
    return red_flag.get("level") in ("red", "orange")


def _has_significant_stress(macro_stress: dict) -> bool:
    """任一情景有样本（confidence 非 low）且月亏超阈值即视为显著压力。"""
    if not macro_stress:
        return False
    for scenario in macro_stress.values():
        sample_count = scenario.get("sample_count", 0)
        confidence = scenario.get("confidence")
        pnl = scenario.get("pnl_pct")
        if confidence in ("high", "medium") and pnl is not None and pnl <= _STRESS_PNL_THRESHOLD:
            return True
    return False


def b_signal(asset: dict[str, Any] | None) -> str:
    """B 参考维度看多/看空/中性信号（bullish/bearish/neutral）。

    口径（设计文档 §3.3 要素）：
    - 看多（任一满足）：``returns(20d) > 0``；或 ``beta`` 任一基准有正值 且
      ``red_flag`` 无红/橙。
    - 看空（任一满足）：``returns(20d) < 0``；或 ``macro_stress`` 存在显著压力情景。
    - 其余（数据缺失 / 零信号 / 信号矛盾）→ ``neutral``，不硬判。

    **口径调整说明**：「macro_stress 无显著压力」是**不看空**而非看多——若把它当独立
    看多条件，无压力资产会全部看多、一致率虚高。无信号即中性，与「不虚构数据」纪律一致。
    """
    if not asset:
        return "neutral"
    ret = asset.get("returns") or {}
    returns20 = float(ret["20d"]) if isinstance(ret, dict) and ret.get("20d") is not None else None

    beta = asset.get("beta") or {}
    beta_positive = any(
        isinstance(v, dict)
        and any(x is not None and float(x) > 0.0 for x in v.values())
        for v in beta.values()
    )
    red_blocked = _red_flag_blocked(asset.get("red_flag"))
    stress = _has_significant_stress(asset.get("macro_stress") or {})

    bullish = (returns20 is not None and returns20 > 0.0) or (beta_positive and not red_blocked)
    bearish = (returns20 is not None and returns20 < 0.0) or stress
    if bullish and bearish:
        return "neutral"
    if bullish:
        return "bullish"
    if bearish:
        return "bearish"
    return "neutral"


def classify_direction(delta: float, signal: str) -> str:
    """方向一致性三分类（agree/diverge/neutral）。

    ``delta > 0`` 调增、``delta < 0`` 调减；与 B 信号方向一致→``agree``，
    背离→``diverge``；无明确信号或 delta=0→``neutral``。
    """
    if signal not in ("bullish", "bearish") or delta == 0.0:
        return "neutral"
    if (delta > 0.0) == (signal == "bullish"):
        return "agree"
    return "diverge"


def weight_delta(old_weight: Any, new_weight: Any) -> float:
    """权重偏离量 delta = new - old；任一侧缺失/脏值（NaN）返回 0.0，不硬判。"""
    if old_weight is None or new_weight is None:
        return 0.0
    try:
        old_f = float(old_weight)
        new_f = float(new_weight)
    except (TypeError, ValueError):
        return 0.0
    if pd.isna(old_f) or pd.isna(new_f):
        return 0.0
    return new_f - old_f


# ---------------------------------------------------------------- 干预后表现


def monthly_returns_from_prices(aligned: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
    """从对齐行情算逐资产月收益，返回 ``{asset_id: Series(month, pct)}``。

    空行情/缺 close 列返回空 Series（不虚构）。
    """
    out: dict[str, pd.Series] = {}
    for asset_id, frame in aligned.items():
        if frame is None or frame.empty or "close" not in frame.columns:
            out[asset_id] = pd.Series(dtype=float)
            continue
        f = frame.sort_values("trade_date").copy()
        f["month"] = f["trade_date"].dt.strftime("%Y-%m")
        f["ret"] = f["close"].pct_change()
        monthly = f.groupby("month")["ret"].apply(lambda x: float((1.0 + x).prod() - 1.0))
        out[asset_id] = monthly
    return out


def _next_month(month: str) -> str | None:
    """下一个月（YYYY-MM）；格式不符返回 None（调用方降级）。"""
    parts = str(month).split("-")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    year, m = parts
    y, mm = int(year), int(m)
    y += (mm == 12)
    return f"{y:04d}-{mm % 12 + 1:02d}"


def post_intervention_performance(
    intervention_month: str,
    asset_id: str,
    monthly: dict[str, pd.Series],
    *,
    min_samples: int = _MIN_POST_SAMPLES,
) -> dict[str, Any]:
    """干预后表现：干预资产下月收益 vs 全资产同月均收益。

    返回 ``{asset_pnl_pct, benchmark_pnl_pct, outperformed, sample_count,
    confidence, basis}``。样本不足（当月有收益的资产数 < min_samples）→
    ``outperformed=None`` + ``confidence="low"``，不虚构结论。
    """
    target_month = _next_month(intervention_month)
    if target_month is None:
        return {
            "asset_pnl_pct": None,
            "benchmark_pnl_pct": None,
            "outperformed": None,
            "sample_count": 0,
            "confidence": "low",
            "basis": f"干预月份无效：{intervention_month!r}",
        }
    asset_series = monthly.get(asset_id)
    if asset_series is None or target_month not in asset_series.index:
        return {
            "asset_pnl_pct": None,
            "benchmark_pnl_pct": None,
            "outperformed": None,
            "sample_count": 0,
            "confidence": "low",
            "basis": f"干预月 {intervention_month}，次月 {target_month} 无 {asset_id} 行情",
        }
    asset_pnl = float(asset_series[target_month])
    benchmarks = [float(s[target_month]) for s in monthly.values() if target_month in s.index]
    sample_count = len(benchmarks)
    if sample_count < min_samples:
        return {
            "asset_pnl_pct": round(asset_pnl * 100.0, 3),
            "benchmark_pnl_pct": None,
            "outperformed": None,
            "sample_count": sample_count,
            "confidence": "low",
            "basis": f"次月 {target_month} 有行情资产仅 {sample_count} 只（<{min_samples}）",
        }
    benchmark = float(sum(benchmarks) / sample_count)
    return {
        "asset_pnl_pct": round(asset_pnl * 100.0, 3),
        "benchmark_pnl_pct": round(benchmark * 100.0, 3),
        "outperformed": asset_pnl > benchmark,
        "sample_count": sample_count,
        "confidence": "high" if sample_count >= 12 else "medium",
        "basis": f"次月 {target_month} {sample_count} 只资产月均收益",
    }


# ---------------------------------------------------------------- 聚合


def _asset_dim_lookup(package: dict | None) -> dict[str, dict[str, Any]]:
    """决策包 assets 列表 → {asset_id: 维度 dict}；无包返回空。"""
    if not package:
        return {}
    assets = package.get("assets") or []
    return {str(a.get("asset_id")): a for a in assets if isinstance(a, dict) and a.get("asset_id")}


def build_hmc_report(
    human_df: pd.DataFrame,
    *,
    package: dict | None = None,
    monthly: dict[str, pd.Series] | None = None,
    month: str | None = None,
) -> dict[str, Any]:
    """聚合人机对比月报结构。

    对每行干预：join B 资产维度（先 asset_id）→ 算 signal/delta/direction →
    干预后表现（下月收益 vs 基准）；再按 ``strategy_id`` 分组。返回结构供
    ``render_hmc_markdown`` 渲染，字段全部 ASCII。
    """
    rows: list[dict[str, Any]] = []
    lookup = _asset_dim_lookup(package)
    monthly = monthly or {}

    for _, record in human_df.iterrows():
        asset_id = str(record.get("asset_id") or "").strip()
        strategy_id = str(record.get("strategy_id") or "").strip()
        if not asset_id or not strategy_id:
            continue
        time_val = record.get("time")
        month_val = record.get("month")
        if pd.isna(month_val):
            month_val = ""
        intervention_month = str(month_val)
        if month and intervention_month != month:
            continue
        old_weight = record.get("old_weight")
        new_weight = record.get("new_weight")
        delta = weight_delta(old_weight, new_weight)
        asset_dim = lookup.get(asset_id)
        signal = b_signal(asset_dim)
        direction = classify_direction(delta, signal)
        perf = post_intervention_performance(
            intervention_month, asset_id, monthly
        ) if monthly and intervention_month else {
            "asset_pnl_pct": None, "benchmark_pnl_pct": None, "outperformed": None,
            "sample_count": 0, "confidence": "low",
            "basis": "未提供行情，跳过干预后表现",
        }
        rows.append({
            "time": time_val.isoformat() if hasattr(time_val, "isoformat") else str(time_val),
            "month": intervention_month,
            "strategy_id": strategy_id,
            "asset_id": asset_id,
            "old_weight": None if pd.isna(old_weight) else round(float(old_weight), 4),
            "new_weight": None if pd.isna(new_weight) else round(float(new_weight), 4),
            "delta": None if pd.isna(delta) else round(float(delta), 4),
            "reason": str(record.get("reason") or ""),
            "signal": signal,
            "direction": direction,
            "dimension_na": asset_dim is None,
            "ret_20d": (asset_dim or {}).get("returns", {}).get("20d"),
            "vol_20d": (asset_dim or {}).get("volatility", {}).get("20d"),
            "red_flag": (asset_dim or {}).get("red_flag"),
            "macro_stress": (asset_dim or {}).get("macro_stress"),
            **perf,
        })

    rows.sort(key=lambda r: (r["month"], r["strategy_id"], r["asset_id"]))
    by_strategy: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_strategy.setdefault(row["strategy_id"], []).append(row)

    valid = [r for r in rows if r["direction"] != "neutral"]
    agree = sum(1 for r in rows if r["direction"] == "agree")
    diverge = sum(1 for r in rows if r["direction"] == "diverge")
    neutral = sum(1 for r in rows if r["direction"] == "neutral")
    performed = [r for r in rows if r["outperformed"] is not None]
    effective = sum(1 for r in performed if r["outperformed"])
    ineffective = sum(1 for r in performed if not r["outperformed"])
    net_delta = sum(r["delta"] for r in rows if r["delta"] is not None)

    report = {
        "summary": {
            "interventions": len(rows),
            "strategies": len(by_strategy),
            "assets": len({r["asset_id"] for r in rows}),
            "agree": agree,
            "diverge": diverge,
            "neutral": neutral,
            "agree_rate": round(100.0 * agree / len(valid), 1) if valid else None,
            "net_delta": round(net_delta, 4),
            "net_direction": "up" if net_delta > 0 else ("down" if net_delta < 0 else "flat"),
            "performed": len(performed),
            "effective": effective,
            "ineffective": ineffective,
        },
        "rows": rows,
        "by_strategy": by_strategy,
        "diverge_rows": [r for r in rows if r["direction"] == "diverge"],
        "macro_regime": (package or {}).get("macro_regime", {}),
        "generated_date": (package or {}).get("generated_date", ""),
        "data_asof": (package or {}).get("data_asof", ""),
        "schema_version": (package or {}).get("schema_version", ""),
    }
    return report


# ---------------------------------------------------------------- 渲染


def _fmt_w(v: Any) -> str:
    return "-" if v is None else f"{v:.2f}"


def _fmt_signal(signal: str) -> str:
    return {
        "bullish": "看多",
        "bearish": "看空",
        "neutral": "中性",
    }.get(signal, "中性")


def _fmt_direction(direction: str) -> str:
    return {
        "agree": "一致",
        "diverge": "背离",
        "neutral": "中性",
    }.get(direction, "中性")


def _dimension_summary(row: dict[str, Any]) -> str:
    """对应 B 维度压缩展示（全 ASCII 键名，缺失标 dimension_na）。"""
    if row.get("dimension_na"):
        return "dimension_na"
    parts = []
    if row.get("ret_20d") is not None:
        parts.append(f"ret20={row['ret_20d'] * 100:.1f}%")
    if row.get("vol_20d") is not None:
        parts.append(f"vol20={row['vol_20d'] * 100:.1f}%")
    red = row.get("red_flag") or {}
    parts.append(f"red={red.get('level', 'none')}")
    stress_count = len(row.get("macro_stress") or {})
    if stress_count:
        parts.append(f"stress_scenarios={stress_count}")
    return " ".join(parts) if parts else "empty"


def render_hmc_markdown(report: dict[str, Any], month: str) -> str:
    """渲染人机对比月报 Markdown（中文仅限标题与 reason 原文，其余 ASCII）。"""
    summary = report["summary"]
    regime = report["macro_regime"]
    lines = [f"# 人机对比月报 {month}", ""]
    lines.append(
        f"本月干预 {summary['interventions']} 次 / 涉及策略 {summary['strategies']} 个 / "
        f"涉及资产 {summary['assets']} 只 / 净调增方向 {summary['net_direction']}"
    )
    rate = summary["agree_rate"]
    lines.append(f"方向一致率 {rate}% ｜ 一致 {summary['agree']} / 背离 {summary['diverge']} "
                 f"/ 中性 {summary['neutral']}")
    if report.get("generated_date"):
        lines.append(f"决策包：generated {report['generated_date']} / asof {report['data_asof']} "
                     f"（schema v{report['schema_version']}）")
    lines.append("")

    lines.append("## 干预明细")
    lines.append("")
    lines.append("| 时间 | strategy_id | asset_id | old→new | delta | B信号 | 一致性 | 对应B维度 | reason |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in report["rows"]:
        lines.append(
            f"| {r['time']} | {r['strategy_id']} | {r['asset_id']} "
            f"| {_fmt_w(r['old_weight'])}→{_fmt_w(r['new_weight'])} "
            f"| {_fmt_w(r['delta'])} | {_fmt_signal(r['signal'])} "
            f"| {_fmt_direction(r['direction'])} | {_dimension_summary(r)} | {r['reason']} |"
        )
    lines.append("")

    lines.append("## 方向一致性评估")
    lines.append("")
    lines.append(f"一致 {summary['agree']} / 背离 {summary['diverge']} / 中性 {summary['neutral']}")
    if report["diverge_rows"]:
        lines.append("")
        lines.append("背离明细：")
        lines.append("")
        lines.append("| 时间 | strategy_id | asset_id | delta | B信号 | reason |")
        lines.append("|---|---|---|---|---|---|")
        for r in report["diverge_rows"]:
            lines.append(
                f"| {r['time']} | {r['strategy_id']} | {r['asset_id']} "
                f"| {_fmt_w(r['delta'])} | {_fmt_signal(r['signal'])} | {r['reason']} |"
            )
    lines.append("")

    lines.append("## 干预后表现")
    lines.append("")
    if summary["performed"]:
        lines.append(f"有行情可评估 {summary['performed']} 次：有效（跑赢基准）"
                     f"{summary['effective']} / 无效（跑输）{summary['ineffective']}")
        lines.append("")
        lines.append("| 时间 | strategy_id | asset_id | 资产月收益 | 基准月收益 | 判定 | 置信度 |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in report["rows"]:
            if r["outperformed"] is None:
                continue
            verdict = "有效" if r["outperformed"] else "无效"
            lines.append(
                f"| {r['time']} | {r['strategy_id']} | {r['asset_id']} "
                f"| {r['asset_pnl_pct']}% | {r['benchmark_pnl_pct']}% "
                f"| {verdict} | {r['confidence']} |"
            )
    else:
        lines.append("未提供行情或样本不足，本轮不评估干预后表现。")
    lines.append("")

    lines.append("## 宏观背景")
    lines.append("")
    if regime:
        lines.append(f"阶段 phase：{regime.get('phase', 'N/A')}")
        lines.append(f"置信度：{regime.get('phase_confidence', 'N/A')}")
        states = regime.get("states") or []
        lines.append(f"状态：{', '.join(states) if states else 'N/A'}")
        durations = regime.get("state_durations") or {}
        if durations:
            dur = ", ".join(f"{k}={v}月" for k, v in sorted(durations.items()))
            lines.append(f"持续月数：{dur}")
    else:
        lines.append("未提供决策包，宏观背景 N/A。")
    lines.append("")
    return "\n".join(lines).strip() + "\n"
