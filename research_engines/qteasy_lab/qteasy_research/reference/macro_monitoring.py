"""宏观监控框架（三剑客 / 全球配置）：M1 适配月报 / M2 相关性 / M3 极端情景韧性。

设计文档 ``docs/macro_monitoring_framework_design.md`` 的实现层（触发：A 侧策略详情
已就绪，NOTICE_20260809_strategy_details_ready.json）。

**核心边界（纪律）**：本模块只产出**描述性统计 / 情景模拟结果**，不定义决策阈值、
不产出调仓建议，``approval_policy=REFERENCE_ONLY``，供 A 侧人工参考。

- **M-003 单向数据流**：只写 ``reports/macro_monitoring/``，不写共享目录、不写系统A、
  不读 ``systemA_feedback/``。
- 机器输出全 ASCII，``strategy_id``/``asset_id`` 标识，零中文策略名。
- 假设统一标注 ``- B-side; awaiting A confirmation``。
- 估值分位（PE/PB）为缺口：B 侧无底层估值数据，输出 ``valuation_na=1``（不虚构）。

复用（不新造轮子）：
``macro_scenarios``（场景表 + 月度收益）、``duration_phase``（阶段）、
``stress_simulator``（历史压力月均收益）、``volatility_cone``（波动分位）、
``backtest_engine``（行情加载/对齐）。详见设计文档 §3 复用能力映射。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qteasy_research.reference.config import (
    MACRO_MONITOR_DIR,
    STRESS_MIN_SAMPLES,
    STRESS_RATE_UP_BP,
)
from qteasy_research.reference.duration_phase import build_duration_phase
from qteasy_research.reference.macro_scenarios import (
    build_monthly_scenario_table,
    scenario_monthly_returns,
)
from qteasy_research.reference.metadata import (
    build_header,
    embed_header_csv,
    today_iso,
)
from qteasy_research.reference.stress_simulator import build_stress_simulator
from qteasy_research.reference.volatility_cone import current_vol_rank

# ---- 监控常量（设计文档 §5.4 / §4.3）----
# 相关性滚动窗口：63 个交易日 ≈ 3 个月。
CORRELATION_WINDOW_DAYS = 63
# 高相关展示标记阈值（非决策阈值）。
HIGH_CORR_THRESHOLD = 0.7
# 共同交易日下限：低于该值相关性置 n/a（不虚构）。
CORR_MIN_OVERLAP = 60
# M1 适配分样本下限（复用 STRESS_MIN_SAMPLES 语义）。
FITNESS_MIN_SAMPLES = STRESS_MIN_SAMPLES
# M3 韧性测试模拟窗口（B 侧假设，报告 Assumptions 标注）。
STRESS_WINDOW_MONTHS = 12

# 9 个宏观状态列（与 macro_scenarios._all_states / duration_phase 对齐）。
_MACRO_STATE_COLUMNS: tuple[str, ...] = (
    "rate_up", "rate_down", "rate_stable",
    "curve_inverted", "curve_normal",
    "real_yield_up", "real_yield_down", "real_yield_stable",
)

# ---- M3 默认情景（B 侧假设，报告标注；A 可覆盖后重跑）----
# scenario key → {label, 受影响资产注入收益（0 位 = 从窗口末尾往前数的月序）}
_DEFAULT_SCENARIOS: dict[str, dict[str, Any]] = {
    "S1_gold_m30": {
        "label": "gold -30% in 1 month",
        "asset": "518880.SH",
        # 单月 -30%：月末价 = 月初 × 0.70。
        "monthly_rets": [-0.30],
    },
    "S2_dividend_m20_3m": {
        "label": "dividend -20% cumulative over 3 months",
        "asset": "512890.SH",
        # 连续 3 个月累计 -20%：每月复合收益 (0.80)^(1/3)-1。
        "monthly_rets": [(0.80 ** (1.0 / 3.0)) - 1.0] * 3,
    },
    "S3_rate_up_50bp": {
        "label": "rate up +50bp (historical stress replay)",
        "asset": None,
        # 全部资产注入历史 rate_up_50bp 情景月均收益（stress_simulator 输出）。
        "replay_scenario": "rate_up_50bp",
        "monthly_rets": None,
    },
    "S4_us_equity_m15_2m": {
        "label": "US equity -15% cumulative over 2 months",
        "asset": "513650.SH",
        # 连续 2 个月累计 -15%：每月复合收益 (0.85)^0.5-1。
        "monthly_rets": [(0.85 ** 0.5) - 1.0] * 2,
    },
}


# ============================================================
# 数据准备（复用 backtest_engine / macro_scenarios）
# ============================================================


def load_monitoring_frames(
    asset_ids: list[str],
    data_root: str | Path,
    *,
    online_ok: bool = False,
) -> dict[str, pd.DataFrame]:
    """加载标的行情（hfq close），返回 ``{asset_id: DataFrame(trade_date, close)}``。

    包装 ``backtest_engine.load_price_frames``：确定性优先（默认不做在线补齐）。
    """
    from qteasy_research.reference.backtest_engine import load_price_frames

    return load_price_frames(list(asset_ids), data_dir=data_root, online_ok=online_ok)


def build_monthly_returns(
    frames: dict[str, pd.DataFrame],
    macro_table: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """逐资产月度收益 × 宏观场景，``{asset_id: DataFrame(month, asset_return, states)}``。

    复用 ``scenario_monthly_returns``；行情缺失资产不产出（不虚构）。
    """
    result: dict[str, pd.DataFrame] = {}
    for asset_id, frame in frames.items():
        if frame is None or frame.empty:
            continue
        monthly = scenario_monthly_returns(frame, macro_table)
        if not monthly.empty:
            result[asset_id] = monthly
    return result


def _monthly_prices(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """各资产月末收盘价（index=月末, columns=asset_id），供 M1 波动分位/全历史月均用。"""
    series: dict[str, pd.Series] = {}
    for asset_id, frame in frames.items():
        if frame is None or frame.empty:
            continue
        sub = frame.copy()
        sub["trade_date"] = pd.to_datetime(sub["trade_date"], errors="coerce")
        sub["close"] = pd.to_numeric(sub["close"], errors="coerce")
        sub = sub.dropna(subset=["trade_date", "close"])
        sub = sub[sub["close"] > 0]
        if sub.empty:
            continue
        monthly = sub.set_index("trade_date")["close"].resample("ME").last()
        series[asset_id] = monthly
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).sort_index()


def _daily_returns_from_frames(
    frames: dict[str, pd.DataFrame],
    calendar: pd.DatetimeIndex,
) -> pd.DataFrame:
    """对齐各资产到全局日历的日收益（index=交易日, columns=asset_id）。

    复用 ``align_asset_prices``（停牌缺口前收回填）后 ``pct_change``。
    """
    from qteasy_research.reference.backtest_engine import align_asset_prices

    aligned = align_asset_prices(frames, calendar)
    returns = aligned.pct_change(fill_method=None)
    return returns


# ============================================================
# 宏观状态（M1 当前宏观背景）
# ============================================================


def current_macro_state(macro_table: pd.DataFrame) -> dict[str, Any]:
    """当前宏观状态摘要：phase / durations / active_states / rate_proxy / asof_month。

    - ``active_states``：场景表末行的 9 状态布尔 → 状态名列表（升序）。
    - 末行 ``macro_unavailable`` → ``active_states=[]``（不把缺失当中性）。
    """
    if macro_table is None or macro_table.empty:
        return {"asof_month": None, "phase": None, "phase_confidence": None,
                "durations": {}, "active_states": [], "rate_proxy": None,
                "macro_unavailable": True}
    table = macro_table.sort_values("month")
    last = table.iloc[-1]
    asof_month = str(last.get("month", "")) or None
    duration = build_duration_phase(table)
    active = [s for s in _MACRO_STATE_COLUMNS if bool(last.get(s, False))]
    macro_unavailable = bool(last.get("macro_unavailable", False))
    if macro_unavailable:
        active = []
    return {
        "asof_month": asof_month,
        "phase": duration.get("phase"),
        "phase_confidence": duration.get("phase_confidence"),
        "phase_basis": duration.get("phase_basis"),
        "durations": duration.get("durations", {}),
        "active_states": active,
        "rate_proxy": last.get("rate_proxy"),
        "macro_unavailable": macro_unavailable,
    }


def _states_tuple(states: Any) -> tuple[str, ...] | None:
    """states 值规范化：list → 排序元组；JSON 字符串 → 解析（fixture 兼容）。"""
    if isinstance(states, list):
        return tuple(sorted(str(s) for s in states))
    if isinstance(states, str):
        try:
            loaded = json.loads(states)
            if isinstance(loaded, list):
                return tuple(sorted(str(s) for s in loaded))
        except Exception:
            return None
    return None


# ============================================================
# M1：三剑客宏观适配月报
# ============================================================


def _states_group_means(monthly: pd.DataFrame) -> dict[tuple[str, ...], list[float]]:
    """按 states 组合分组的月收益列表 → ``{states_tuple: [returns]}``。"""
    groups: dict[tuple[str, ...], list[float]] = {}
    for _, row in monthly.iterrows():
        key = _states_tuple(row.get("states"))
        if key is None:
            continue
        value = float(row.get("asset_return"))
        if not np.isfinite(value):
            continue
        groups.setdefault(key, []).append(value)
    return groups


def compute_macro_fitness(
    asset_ids: list[str],
    frames: dict[str, pd.DataFrame],
    monthly: dict[str, pd.DataFrame],
    macro_state: dict[str, Any],
    vol_windows: tuple[int, ...] = (20, 60),
) -> pd.DataFrame:
    """M1：逐资产宏观适配分（每资产一行，long 表）。

    口径（设计文档 §4.3）：
    - ``fit_score``：当前 ``active_states`` 完全匹配月份的月均收益，在该资产全部
      states 组合月均收益中的 min-max 归一（0~1）。**描述性统计，非买入评分**。
    - ``state_hist_avg``：与当前 states 完全相同的月份的历史月均收益（%）。
    - ``cur_state_avg``：同 ``state_hist_avg``（当前宏观状态月均收益，%）。
    - ``hist_avg``：该资产全部月份的月收益均值（对照基线，%）。
    - ``sample_count``：匹配当前 states 的月份数。
    - ``vol_pctile_20d/60d``：当前年化波动率在自身历史的分位（0~1）。
    - ``confidence``：样本 < ``FITNESS_MIN_SAMPLES`` → ``low``，数值置 None（不虚构）。

    估值分位缺口：不产出 valuation 列（B 侧无数据源），报告层标注 ``valuation_na``。
    """
    active = tuple(sorted(macro_state.get("active_states", [])))
    rows: list[dict[str, Any]] = []
    for asset_id in asset_ids:
        frame = frames.get(asset_id)
        monthly_frame = monthly.get(asset_id)
        row: dict[str, Any] = {
            "asset_id": asset_id,
            "fit_score": None,
            "state_hist_avg": None,
            "cur_state_avg": None,
            "hist_avg": None,
            "sample_count": 0,
            "confidence": "low",
        }
        for window in vol_windows:
            row[f"vol_pctile_{window}d"] = None
        if frame is None or frame.empty:
            rows.append(row)
            continue

        # 波动分位（当前年化波动率在自身历史分布的分位）。
        sub = frame.copy()
        sub["trade_date"] = pd.to_datetime(sub["trade_date"], errors="coerce")
        sub = sub.sort_values("trade_date").dropna(subset=["trade_date"])
        returns = pd.to_numeric(sub["close"], errors="coerce").pct_change().dropna()
        for window in vol_windows:
            row[f"vol_pctile_{window}d"] = current_vol_rank(returns, window)

        # 全历史月均收益。
        if monthly_frame is not None and not monthly_frame.empty:
            all_rets = pd.to_numeric(monthly_frame["asset_return"], errors="coerce")
            all_rets = all_rets.dropna()
            if not all_rets.empty:
                row["hist_avg"] = round(float(all_rets.mean()) * 100.0, 4)

        # 当前 states 匹配月份。
        if monthly_frame is None or monthly_frame.empty or not active:
            rows.append(row)
            continue
        match = monthly_frame[
            monthly_frame["states"].apply(lambda s: _states_tuple(s) == active)
        ]
        row["sample_count"] = int(len(match))
        if len(match) < FITNESS_MIN_SAMPLES:
            # 样本不足：不虚构适配分。
            rows.append(row)
            continue
        matched_rets = pd.to_numeric(match["asset_return"], errors="coerce").dropna()
        if matched_rets.empty:
            rows.append(row)
            continue
        cur_avg = float(matched_rets.mean())
        row["state_hist_avg"] = round(cur_avg * 100.0, 4)
        row["cur_state_avg"] = round(cur_avg * 100.0, 4)

        # fit_score：当前状态月均收益 → 该资产全部 states 组合月均收益 min-max 归一。
        groups = _states_group_means(monthly_frame)
        if groups:
            group_means = [float(np.mean(v)) for v in groups.values()]
            lo, hi = min(group_means), max(group_means)
            score = (cur_avg - lo) / (hi - lo) if hi > lo else 0.5
            row["fit_score"] = round(float(score), 4)
            row["confidence"] = (
                "high" if len(match) >= 12
                else "medium" if len(match) >= 6
                else "low"
            )
        rows.append(row)
    return pd.DataFrame(rows)


def _fmt_pct(value: Any) -> str:
    return "n/a" if value is None else f"{value:+.2f}%"


def _fmt_score(value: Any) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def render_macro_fitness_md(
    rule: str,
    fitness: pd.DataFrame,
    macro_state: dict[str, Any],
) -> str:
    """M1 Markdown 月报（设计文档 §4.2 结构，全 ASCII）。"""
    lines: list[str] = []
    asof = macro_state.get("asof_month") or "n/a"
    lines.append(f"# Macro Fitness Report: {rule} (asof {asof})")
    lines.append("")
    lines.append("## Macro State")
    lines.append(f"- phase: {macro_state.get('phase') or 'na'}")
    phase_basis = macro_state.get("phase_basis") or "na"
    lines.append(f"- phase_basis: {phase_basis}")
    lines.append(f"- phase_confidence: {macro_state.get('phase_confidence') or 'na'}")
    durations = macro_state.get("durations") or {}
    if durations:
        dur_str = ", ".join(
            f"{state}: {int(count)}m" for state, count in durations.items() if count > 0
        ) or "none"
        lines.append(f"- state_durations: {dur_str}")
    rate_proxy = macro_state.get("rate_proxy")
    lines.append(
        f"- rate_proxy: {'n/a' if rate_proxy is None else rate_proxy} | "
        f"macro_unavailable: {bool(macro_state.get('macro_unavailable'))}"
    )
    active = macro_state.get("active_states") or []
    lines.append(f"- active_states: {', '.join(active) if active else 'none'}")
    lines.append("")
    lines.append("## Asset Macro Fitness (descriptive; REFERENCE_ONLY)")
    lines.append(
        "| asset_id | fit_score | state_hist_avg | cur_state_avg | hist_avg | "
        "vol_pctile_20d | vol_pctile_60d | sample_count | confidence | valuation |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    if fitness.empty:
        lines.append("| (no assets) | - | - | - | - | - | - | - | - | - |")
    for _, row in fitness.iterrows():
        lines.append(
            f"| {row['asset_id']} | {_fmt_score(row['fit_score'])} | "
            f"{_fmt_pct(row['state_hist_avg'])} | {_fmt_pct(row['cur_state_avg'])} | "
            f"{_fmt_pct(row['hist_avg'])} | "
            f"{'n/a' if pd.isna(row['vol_pctile_20d']) else f'{row['vol_pctile_20d']:.0%}'} | "
            f"{'n/a' if pd.isna(row['vol_pctile_60d']) else f'{row['vol_pctile_60d']:.0%}'} | "
            f"{int(row['sample_count'])} | {row['confidence']} | valuation_na |"
        )
    lines.append("")
    lines.append("## Notes")
    lines.append("- fit_score: min-max normalized historical avg monthly return "
                 "for the current macro states (descriptive only; no decision rule).")
    lines.append("- valuation column is a data gap (no PE/PB source); marked "
                 "valuation_na, not fabricated.")
    lines.append("- Assumptions are B-side; awaiting A confirmation.")
    return "\n".join(lines) + "\n"


def write_macro_fitness(
    rule: str,
    fitness: pd.DataFrame,
    macro_state: dict[str, Any],
    output_root: str | Path,
    *,
    data_asof: str | None = None,
) -> tuple[Path, Path]:
    """写 M1 三件套（Markdown + CSV）。返回 ``(md_path, csv_path)``。"""
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    asof_month = (macro_state.get("asof_month") or today_iso())[:7].replace("-", "")
    md_path = root / f"{asof_month}_macro_fitness_{rule}.md"
    csv_path = root / f"{asof_month}_macro_fitness_{rule}.csv"

    md_path.write_text(render_macro_fitness_md(rule, fitness, macro_state), encoding="utf-8")

    header = build_header(generated_date=today_iso(), data_asof=data_asof or asof_month)
    csv = fitness.copy()
    csv["valuation"] = "na"
    embed_header_csv(csv_path, header, csv)
    return md_path, csv_path


# ============================================================
# M2：相关性监控（全球配置 10×10 主矩阵 + 三剑客基底参考列）
# ============================================================


def _recent_returns_window(
    returns: pd.DataFrame,
    window_days: int = CORRELATION_WINDOW_DAYS,
) -> pd.DataFrame:
    """最近 ``window_days`` 个交易日的日收益（各资产缺数段保留 NaN）。"""
    if returns.empty:
        return returns
    return returns.tail(window_days)


def build_correlation(
    matrix_assets: list[str],
    reference_assets: list[str],
    returns: pd.DataFrame,
    *,
    window_days: int = CORRELATION_WINDOW_DAYS,
    min_overlap: int = CORR_MIN_OVERLAP,
    high_corr_threshold: float = HIGH_CORR_THRESHOLD,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """M2：相关性主矩阵 + 参考列 + 高相关对表。

    返回 ``(matrix, reference, high_corr)``：
    - ``matrix``：``matrix_assets × matrix_assets`` 对称相关矩阵（窗口内日收益 corr）。
    - ``reference``：``matrix_assets × reference_assets`` 交叉相关（参考列）。
    - ``high_corr``：上三角 ``|corr| > threshold`` 的对表（含方向 flag）。

    样本下限：共同交易日 ``< min_overlap`` → 该格 ``NaN``（不填充 0、不虚构）。
    """
    cols = [a for a in matrix_assets if a in returns.columns]
    ref_cols = [a for a in reference_assets if a in returns.columns]
    window_returns = _recent_returns_window(returns, window_days)

    matrix = pd.DataFrame(index=cols, columns=cols, dtype=float)
    reference = pd.DataFrame(index=cols, columns=ref_cols, dtype=float)
    for a in cols:
        for b in cols:
            if a == b:
                # 对角线自相关恒为 1.0（避免 [[a,a]] 列名重复使 pair[a] 变 DataFrame）。
                matrix.loc[a, b] = 1.0
                continue
            pair = window_returns[[a, b]].dropna()
            if len(pair) < min_overlap:
                matrix.loc[a, b] = np.nan
                continue
            corr = pair[a].corr(pair[b])
            matrix.loc[a, b] = round(float(corr), 4) if np.isfinite(corr) else np.nan
    for a in cols:
        for b in ref_cols:
            if a == b:
                reference.loc[a, b] = 1.0
                continue
            pair = window_returns[[a, b]].dropna()
            if len(pair) < min_overlap:
                reference.loc[a, b] = np.nan
                continue
            corr = pair[a].corr(pair[b])
            reference.loc[a, b] = round(float(corr), 4) if np.isfinite(corr) else np.nan

    # 高相关对（上三角，|corr| > threshold，含方向 flag）。
    pairs: list[dict[str, Any]] = []
    if not window_returns.empty:
        window_end = window_returns.index[-1]
        window_start = window_returns.index[0] if len(window_returns) >= window_days \
            else window_returns.index[0]
    else:
        window_start = window_end = None
    for i, a in enumerate(cols):
        for j in range(i + 1, len(cols)):
            b = cols[j]
            value = matrix.loc[a, b]
            if pd.isna(value):
                continue
            if abs(value) > high_corr_threshold:
                pairs.append({
                    "asset_a": a,
                    "asset_b": b,
                    "corr_3m": value,
                    "window_start": window_start.strftime("%Y-%m-%d") if window_start is not None else "",
                    "window_end": window_end.strftime("%Y-%m-%d") if window_end is not None else "",
                    "flag": "HIGH_CORR" if value > 0 else "NEG_HIGH_CORR",
                })
    high_corr = pd.DataFrame(pairs, columns=[
        "asset_a", "asset_b", "corr_3m", "window_start", "window_end", "flag",
    ])
    return matrix, reference, high_corr


def render_correlation_md(
    matrix: pd.DataFrame,
    reference: pd.DataFrame,
    high_corr: pd.DataFrame,
    *,
    window_days: int = CORRELATION_WINDOW_DAYS,
    high_corr_threshold: float = HIGH_CORR_THRESHOLD,
) -> str:
    """M2 Markdown 摘要：主矩阵 + 参考列 + 高相关对清单。"""
    lines: list[str] = []
    lines.append("# Correlation Monitor (3-month rolling)")
    lines.append(f"- window_days: {window_days}")
    lines.append(f"- high_corr_threshold: {high_corr_threshold} (display marker; not a decision rule)")
    lines.append("")
    lines.append("## Main Matrix (10x10)")
    if matrix.empty:
        lines.append("(no assets with data)")
    else:
        header = "| asset_id | " + " | ".join(matrix.columns) + " |"
        lines.append(header)
        lines.append("|" + "---|" * (len(matrix.columns) + 1))
        for idx, row in matrix.iterrows():
            cells = []
            for value in row:
                cells.append("n/a" if pd.isna(value) else f"{value:.2f}")
            lines.append(f"| {idx} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## Reference Columns (3-musketeer base vs 10 assets)")
    if reference.empty:
        lines.append("(no reference assets with data)")
    else:
        header = "| asset_id | " + " | ".join(reference.columns) + " |"
        lines.append(header)
        lines.append("|" + "---|" * (len(reference.columns) + 1))
        for idx, row in reference.iterrows():
            cells = []
            for value in row:
                cells.append("n/a" if pd.isna(value) else f"{value:.2f}")
            lines.append(f"| {idx} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("## High Correlation Pairs")
    if high_corr.empty:
        lines.append(f"(none above |{high_corr_threshold}|)")
    else:
        lines.append("| asset_a | asset_b | corr_3m | window_start | window_end | flag |")
        lines.append("|---|---|---|---|---|---|")
        for _, row in high_corr.iterrows():
            flag = row["flag"]
            mark = "**" if flag == "HIGH_CORR" else "*"
            lines.append(
                f"| {row['asset_a']} | {row['asset_b']} | "
                f"{mark}{row['corr_3m']:.2f}{mark} | {row['window_start']} | "
                f"{row['window_end']} | {flag} |"
            )
    lines.append("")
    lines.append("## Notes")
    lines.append("- Correlation is a descriptive statistic over the trailing window; "
                 "no decision rule implied.")
    lines.append("- Missing cells: fewer than 60 common trading days (n/a, not 0).")
    return "\n".join(lines) + "\n"


def write_correlation(
    matrix: pd.DataFrame,
    reference: pd.DataFrame,
    high_corr: pd.DataFrame,
    output_root: str | Path,
    *,
    asof_month: str,
    data_asof: str,
    window_days: int = CORRELATION_WINDOW_DAYS,
) -> tuple[Path, Path, Path]:
    """写 M2 三件套。返回 ``(matrix_path, pairs_path, summary_path)``。"""
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    ym = asof_month[:7].replace("-", "")
    matrix_path = root / f"{ym}_correlation_matrix.csv"
    pairs_path = root / f"{ym}_high_corr_pairs.csv"
    summary_path = root / f"{ym}_correlation_summary.md"

    # 主矩阵 CSV：行列对称；参考列附加（asset_id 行 + 基底三列）。
    # 首列表头用 asset_id（而非 #）：A 侧以 comment='#' 读取，首列名为 # 会被
    # 误当注释行跳过（读入变 Unnamed: 0），改为 asset_id 后首行即表头。
    combined = matrix.copy()
    for ref_col in reference.columns:
        combined[f"{ref_col} (base)"] = reference[ref_col]
    combined = combined.reset_index().rename(columns={"index": "asset_id"})

    header_matrix = build_header(
        generated_date=today_iso(), data_asof=data_asof,
    )
    header_matrix["window_days"] = str(window_days)
    embed_header_csv(matrix_path, header_matrix, combined)

    header_pairs = build_header(generated_date=today_iso(), data_asof=data_asof)
    header_pairs["window_days"] = str(window_days)
    embed_header_csv(pairs_path, header_pairs, high_corr)

    summary_path.write_text(
        render_correlation_md(matrix, reference, high_corr),
        encoding="utf-8",
    )
    return matrix_path, pairs_path, summary_path


# ============================================================
# M3：极端情景韧性测试
# ============================================================


def _static_portfolio_nav(
    prices: pd.DataFrame,
    weights: dict[str, float],
    window: pd.DatetimeIndex,
) -> pd.Series:
    """静态权重组合净值：``Σ w_i × price_i / price_i[window_start]``。

    **B 侧假设**（报告 Assumptions 标注）：不 rebalance、不含成本、等权资金分配
    以契约 ``target_weight`` 为基准——描述性净值差异，非完整事件驱动模拟。
    """
    nav = pd.Series(0.0, index=window, dtype=float)
    if prices.empty:
        return nav
    for asset_id, weight in weights.items():
        if asset_id not in prices.columns:
            continue
        series = prices[asset_id].ffill()
        if len(series) == 0:
            continue
        start = window[0]
        if start not in series.index or not np.isfinite(series.loc[start]):
            # 资产在窗口起点未上市 → 从首个有效值起步（占位为缺口前权重落空，不虚构）。
            valid = series.dropna()
            if valid.empty:
                continue
            base = valid.iloc[0]
            # 缺口期净值从 1 起步（与其余资产同一基准，B 侧假设）。
            scaled = pd.Series(1.0, index=window)
            scaled.loc[valid.index] = valid / base
            nav = nav + weight * scaled
            continue
        base = float(series.loc[start])
        nav = nav + weight * (series / base)
    return nav


def _inject_monthly_returns(
    prices: pd.DataFrame,
    asset_id: str,
    monthly_rets: list[float],
    window: pd.DatetimeIndex,
) -> pd.DataFrame:
    """对资产注入月度收益路径：从窗口末尾往前数 ``len(monthly_rets)`` 个自然月，
    注入月内每日价格 = 该月前参考价 × ``(1+ret)``（月内 flat，月末即冲击后水平）。

    ``monthly_rets[0]`` 为最早注入月（窗口末尾往前第 k 个自然月）。
    返回注入后的价格帧副本（其余资产不变）。
    """
    if asset_id not in prices.columns or not monthly_rets:
        return prices
    out = prices.copy()
    series = out[asset_id].copy()
    month_of = pd.Series(window.strftime("%Y-%m"), index=window)
    months = sorted(month_of.unique())
    target_months = months[-len(monthly_rets):]
    prev_price: float | None = None
    for k, month_key in enumerate(target_months):
        month_idx = window[month_of.values == month_key]
        if len(month_idx) == 0:
            continue
        if prev_price is None:
            first_day = month_idx[0]
            prior = series.loc[: first_day - pd.Timedelta(days=1)].dropna()
            if prior.empty:
                valid = series.dropna()
                if valid.empty:
                    continue
                base = float(valid.iloc[0])
            else:
                base = float(prior.iloc[-1])
        else:
            base = prev_price
        target = base * (1.0 + float(monthly_rets[k]))
        series.loc[month_idx] = target
        prev_price = target
    out[asset_id] = series
    return out


def _replay_scenario_returns(
    stress_map: dict[str, dict[str, dict[str, Any]]],
    asset_ids: list[str],
    scenario_key: str,
) -> dict[str, float]:
    """取各资产某压力情景的历史月均收益（%→小数），供 S3 历史重放注入。

    无该情景数据（样本不足）→ 不注入（该资产无冲击）。
    """
    result: dict[str, float] = {}
    for asset_id in asset_ids:
        asset_stress = stress_map.get(asset_id, {})
        pnl = asset_stress.get(scenario_key, {}).get("pnl_pct")
        if pnl is not None and np.isfinite(float(pnl)):
            result[asset_id] = float(pnl) / 100.0
    return result


def build_stress_scenarios(
    strategies: dict[str, dict[str, float]],
    prices: pd.DataFrame,
    window: pd.DatetimeIndex,
    stress_map: dict[str, dict[str, dict[str, Any]]],
    scenarios: dict[str, dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """M3：每情景每策略的净值差异（baseline vs stressed）。

    ``strategies``：``{strategy_id: {asset_id: target_weight}}``（契约权重）。
    返回 long 表：``scenario, label, strategy_id, baseline_nav, stressed_nav,
    diff_amount, diff_pct``。
    """
    scenarios = scenarios or _DEFAULT_SCENARIOS
    rows: list[dict[str, Any]] = []
    if prices.empty or len(window) == 0:
        return pd.DataFrame(columns=[
            "scenario", "label", "strategy_id", "baseline_nav",
            "stressed_nav", "diff_amount", "diff_pct", "note",
        ])
    for scenario_key, spec in scenarios.items():
        label = str(spec.get("label", scenario_key))
        for strategy_id, weights in strategies.items():
            baseline = _static_portfolio_nav(prices, weights, window)
            base_end = float(baseline.iloc[-1]) if len(baseline) else np.nan

            stressed_prices = prices
            note: str = ""
            replay = spec.get("replay_scenario")
            asset = spec.get("asset")
            monthly_rets = spec.get("monthly_rets")
            if replay is not None:
                # S3：历史压力月重放（每资产注入历史 rate_up_50bp 月均收益）。
                injected = _replay_scenario_returns(stress_map, list(weights), replay)
                if not injected:
                    # 样本不足 → 情景不生效，显式标注（不静默 0 差异，不虚构）。
                    note = (
                        f"no replay samples for {replay} "
                        f"(sample_count < {FITNESS_MIN_SAMPLES}); scenario not applied"
                    )
                for aid, ret in injected.items():
                    stressed_prices = _inject_monthly_returns(
                        stressed_prices, aid, [ret], window,
                    )
            elif asset is not None and monthly_rets is not None:
                stressed_prices = _inject_monthly_returns(
                    stressed_prices, str(asset), list(monthly_rets), window,
                )

            stressed = _static_portfolio_nav(stressed_prices, weights, window)
            stressed_end = float(stressed.iloc[-1]) if len(stressed) else np.nan
            diff = (stressed_end - base_end) if np.isfinite(base_end) else np.nan
            diff_pct = (diff / base_end) if (np.isfinite(base_end) and base_end != 0) else np.nan
            rows.append({
                "scenario": scenario_key,
                "label": label,
                "strategy_id": strategy_id,
                "baseline_nav": round(base_end, 4) if np.isfinite(base_end) else None,
                "stressed_nav": round(stressed_end, 4) if np.isfinite(stressed_end) else None,
                "diff_amount": round(diff, 4) if np.isfinite(diff) else None,
                "diff_pct": round(diff_pct, 4) if np.isfinite(diff_pct) else None,
                "note": note,
            })
    return pd.DataFrame(rows)


def render_stress_md(scenarios_df: pd.DataFrame) -> str:
    """M3 Markdown 报告（设计文档 §6.4 结构）。"""
    lines: list[str] = []
    lines.append("# Stress Scenario Resilience Simulation")
    lines.append("")
    lines.append("## Results (per scenario per strategy)")
    lines.append(
        "| scenario | label | strategy_id | baseline_nav | stressed_nav | "
        "diff_amount | diff_pct | note |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    if scenarios_df.empty:
        lines.append("| (no scenarios) | - | - | - | - | - | - | - |")
    for _, row in scenarios_df.iterrows():
        note = str(row.get("note") or "")
        lines.append(
            f"| {row['scenario']} | {row['label']} | {row['strategy_id']} | "
            f"{_fmt_num(row['baseline_nav'])} | {_fmt_num(row['stressed_nav'])} | "
            f"{_fmt_num(row['diff_amount'])} | {_fmt_num(row['diff_pct'])} | {note} |"
        )
    lines.append("")
    lines.append("## Sensitivity Notes")
    lines.append("- Simulation uses static contract target weights (no rebalance, no cost); "
                 "descriptive only; no decision rule implied.")
    lines.append("- Scenario amplitudes/paths are B-side assumptions; "
                 "awaiting A confirmation.")
    lines.append("- S3 replays historical rate_up_50bp monthly returns "
                 "(stress_simulator replay); assets without sufficient samples are unaffected.")
    return "\n".join(lines) + "\n"


def _fmt_num(value: Any) -> str:
    return "n/a" if value is None or (isinstance(value, float) and not np.isfinite(value)) \
        else f"{value:.4f}"


def write_stress(
    scenarios_df: pd.DataFrame,
    output_root: str | Path,
    *,
    asof_month: str,
    data_asof: str,
) -> tuple[Path, Path]:
    """写 M3 两件套。返回 ``(md_path, csv_path)``。"""
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    ym = asof_month[:7].replace("-", "")
    md_path = root / f"{ym}_stress_scenarios.md"
    csv_path = root / f"{ym}_stress_scenarios.csv"
    md_path.write_text(render_stress_md(scenarios_df), encoding="utf-8")
    header = build_header(generated_date=today_iso(), data_asof=data_asof)
    embed_header_csv(csv_path, header, scenarios_df)
    return md_path, csv_path


# ============================================================
# 组装入口（供 CLI 调用，返回逐项写入结果）
# ============================================================


def run_macro_monitoring(
    contract,
    data_root: str | Path,
    output_root: str | Path = MACRO_MONITOR_DIR,
    *,
    rules: tuple[str, ...] = ("three_musketeers", "global_allocation"),
    online_ok: bool = False,
) -> dict[str, Any]:
    """宏观监控全流程：M1 + M2 + M3，写 ``reports/macro_monitoring/``。

    返回 ``{"status", "asof_month", "written": {...}, "counts", "errors"}``。
    任何单监控项异常降级（不中断其余项），与回测引擎 ERROR 语义一致。
    """
    output_root = Path(output_root)
    errors: list[str] = []

    # ---- 数据层（共享一次宏观场景表）----
    try:
        macro_table = build_monthly_scenario_table(data_root)
    except Exception as exc:
        macro_table = pd.DataFrame()
        errors.append(f"build_monthly_scenario_table: {type(exc).__name__}: {exc}")

    strategies = {s.strategy_id: s for s in contract.strategies}
    rule_assets: dict[str, list[str]] = {}
    weights_map: dict[str, dict[str, float]] = {}
    for rule in rules:
        strategy = strategies.get(rule)
        if strategy is None:
            errors.append(f"rule_not_found: {rule}")
            continue
        enabled = list(strategy.enabled_assets)
        rule_assets[rule] = enabled
        weights_map[rule] = {
            a.asset_id: float(a.target_weight)
            for a in strategy.assets
            if a.enabled and a.target_weight is not None and a.target_weight > 0
        }

    all_asset_ids: list[str] = []
    for assets in rule_assets.values():
        for asset_id in assets:
            if asset_id not in all_asset_ids:
                all_asset_ids.append(asset_id)

    frames: dict[str, pd.DataFrame] = {}
    if all_asset_ids:
        try:
            frames = load_monitoring_frames(all_asset_ids, data_root, online_ok=online_ok)
        except Exception as exc:
            errors.append(f"load_price_frames: {type(exc).__name__}: {exc}")

    macro_state = current_macro_state(macro_table)
    asof_month = macro_state.get("asof_month") or today_iso()
    data_asof = asof_month

    monthly = build_monthly_returns(frames, macro_table) if not macro_table.empty else {}

    # stress_map（M3 S3 历史重放需要；失败降级为空 dict）。
    stress_map: dict[str, dict[str, dict[str, Any]]] = {}
    if all_asset_ids:
        try:
            assets_df = pd.DataFrame({"asset_id": all_asset_ids})
            stress_map = build_stress_simulator(
                assets_df, frames, macro_table, data_root,
            )
        except Exception as exc:
            errors.append(f"build_stress_simulator: {type(exc).__name__}: {exc}")

    written: dict[str, Any] = {"M1": [], "M2": [], "M3": []}
    counts = {"M1": 0, "M2": 0, "M3": 0}

    # ---- M1 宏观适配月报 ----
    for rule in rule_assets:
        assets = rule_assets[rule]
        try:
            fitness = compute_macro_fitness(assets, frames, monthly, macro_state)
            md, csv = write_macro_fitness(
                rule, fitness, macro_state, output_root, data_asof=data_asof,
            )
            written["M1"].append({"rule": rule, "md": str(md), "csv": str(csv)})
            counts["M1"] += 1
        except Exception as exc:
            errors.append(f"M1 {rule}: {type(exc).__name__}: {exc}")

    # ---- M2 相关性监控 ----
    try:
        ga_assets = rule_assets.get("global_allocation", [])
        base_assets = rule_assets.get("three_musketeers", [])
        if ga_assets:
            frames_ga = {a: f for a, f in frames.items() if a in ga_assets}
            from qteasy_research.reference.backtest_engine import build_trading_calendar

            calendar = build_trading_calendar(frames_ga)
            returns = _daily_returns_from_frames(frames_ga, calendar)
            matrix, reference, high_corr = build_correlation(
                ga_assets, base_assets, returns,
            )
            paths = write_correlation(
                matrix, reference, high_corr, output_root,
                asof_month=asof_month, data_asof=data_asof,
            )
            written["M2"] = {
                "matrix": str(paths[0]), "pairs": str(paths[1]), "summary": str(paths[2]),
            }
            counts["M2"] = 1
        else:
            errors.append("M2: no global_allocation assets")
    except Exception as exc:
        errors.append(f"M2: {type(exc).__name__}: {exc}")

    # ---- M3 极端情景韧性 ----
    try:
        if all_asset_ids:
            frames_all = {a: f for a, f in frames.items() if a in all_asset_ids}
            from qteasy_research.reference.backtest_engine import build_trading_calendar

            calendar = build_trading_calendar(frames_all)
            if len(calendar) > 0:
                # M3 窗口：最近 STRESS_WINDOW_MONTHS 个自然月。
                months = sorted({d.strftime("%Y-%m") for d in calendar})
                window_months = months[-STRESS_WINDOW_MONTHS:]
                window = calendar[calendar.strftime("%Y-%m").isin(window_months)]
                prices = _monthly_prices(frames_all)
                # 用月末价格构建净值窗口（静态权重净值按月末粒度）。
                month_prices = prices.loc[prices.index.strftime("%Y-%m").isin(window_months)]
                stress_df = build_stress_scenarios(
                    weights_map, month_prices, month_prices.index,
                    stress_map,
                )
                md, csv = write_stress(
                    stress_df, output_root, asof_month=asof_month, data_asof=data_asof,
                )
                written["M3"] = {"md": str(md), "csv": str(csv)}
                counts["M3"] = 1
            else:
                errors.append("M3: empty trading calendar")
        else:
            errors.append("M3: no assets")
    except Exception as exc:
        errors.append(f"M3: {type(exc).__name__}: {exc}")

    return {
        "status": "OK" if not errors else "PARTIAL",
        "asof_month": asof_month,
        "written": written,
        "counts": counts,
        "errors": errors,
    }


__all__ = [
    "CORRELATION_WINDOW_DAYS",
    "HIGH_CORR_THRESHOLD",
    "CORR_MIN_OVERLAP",
    "STRESS_WINDOW_MONTHS",
    "MACRO_MONITOR_DIR",
    "load_monitoring_frames",
    "build_monthly_returns",
    "current_macro_state",
    "compute_macro_fitness",
    "render_macro_fitness_md",
    "write_macro_fitness",
    "build_correlation",
    "render_correlation_md",
    "write_correlation",
    "build_stress_scenarios",
    "render_stress_md",
    "write_stress",
    "run_macro_monitoring",
]
