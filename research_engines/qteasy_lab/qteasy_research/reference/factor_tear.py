"""alphalens 借鉴的因子 tear sheet 模块（阶段一，不直接引入 alphalens 依赖）。

本模块复用 B 侧既有计算口径，自研四类 tear sheet，供因子有效性人工核对：

- **IC 分布**：逐日横截面 IC 序列（mean / std / ICIR / 正收益占比 / 分位数）；
- **分位收益**：因子值按横截面分位分桶后各分位组的平均 forward return + 单调性；
- **因子衰减（IC decay）**：IC 随 forward_period（1/5/20）变化表；
- **turnover**：alphalens 口径换手率（相邻两期分位桶变化比例的平均，``|Δbucket|/2`` 归一）。

因子值来源：
- ``--source csv``：从 ``data/fund_daily.csv`` 对全量标的即时计算
  （公式口径与 ``data_manager.build_factor_values`` 一致）；
- ``--source parquet``：从 ``research_store/factor_values/*.parquet`` 读
  （阶段二生产管道预留入口；forward return 仍用本地 CSV 价格计算）。

forward return 口径：``close.pct_change().shift(-N)``（``N ∈ {1, 5, 20}``）。
IC / Rank IC / ICIR / group_spread / net_return 复用
``factor_research.evaluate_factor_effectiveness`` 的横截面 DataFrame 模式；
该函数第 245 行 ``turnover=None`` 为硬编码，本模块**不修改 factor_research.py**，
turnover 在本文件内独立实现。

输出：控制台 ASCII 汇总 + ASCII 文件名 CSV；若环境已有 matplotlib 则输出 PNG
tear sheet（ASCII 文件名），没有则降级为纯数据表。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qteasy_research.pretrade.factor_research import (
    estimate_transaction_cost,
    evaluate_factor_effectiveness,
)

FACTOR_IDS: tuple[str, ...] = (
    "momentum_60d",
    "momentum_120d",
    "low_volatility_20d",
    "liquidity_turnover",
)
DEFAULT_FORWARD_PERIODS: tuple[int, ...] = (1, 5, 20)
QUANTILES = 5
MIN_SAMPLES = 24
MIN_CROSS_SECTION = 3

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV_PATH = PROJECT_ROOT / "data" / "fund_daily.csv"
DEFAULT_FACTOR_DIR = PROJECT_ROOT / "research_store" / "factor_values"
DEFAULT_OUT_DIR = PROJECT_ROOT / "reports" / "factor_tear"

# 与 data_manager.build_factor_values (data_manager.py:400-407) 计算口径一致的自包含公式。
# 公式为 lambda，输入为 ``group``（含 close / amount 列、按 trade_date 升序），
# 输出为与 group 等长的因子值 Series。
FACTOR_FORMULAS: dict[str, Any] = {
    "momentum_60d": lambda group: group["close"].pct_change(60),
    "momentum_120d": lambda group: group["close"].pct_change(120),
    "low_volatility_20d": lambda group: -group["close"].pct_change().rolling(20).std(),
    "liquidity_turnover": lambda group: (
        pd.Series(group["amount"], index=group.index)
        .pipe(pd.to_numeric, errors="coerce")
        .clip(lower=0)
        .pipe(np.log1p)
        .rolling(20)
        .mean()
    ),
}


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------

def _read_price_frame(csv_path: str | Path) -> pd.DataFrame:
    """读行情 CSV 并统一成 date x asset 的 close / amount 长表（升序、去重）。"""
    frame = pd.read_csv(csv_path)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["ts_code"] = frame["ts_code"].astype(str).str.upper().str.strip()
    frame = frame.dropna(subset=["trade_date", "close"])
    frame = frame.sort_values(["ts_code", "trade_date"]).drop_duplicates(
        ["ts_code", "trade_date"], keep="last"
    )
    return frame


def load_price_panels(csv_path: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回 ``(close, amount)`` 面板（index=date, columns=asset）。"""
    frame = _read_price_frame(csv_path)
    close = frame.pivot_table(index="trade_date", columns="ts_code", values="close")
    amount = frame.pivot_table(index="trade_date", columns="ts_code", values="amount")
    return close, amount


def compute_factor_panel_from_csv(
    csv_path: str | Path,
    factor_ids: tuple[str, ...] = FACTOR_IDS,
) -> dict[str, pd.DataFrame]:
    """从 CSV 对每个标的即时计算因子，返回 ``{factor_id: date x asset 面板}``。"""
    frame = _read_price_frame(csv_path)
    panels: dict[str, pd.DataFrame] = {}
    for factor_id in factor_ids:
        formula = FACTOR_FORMULAS.get(factor_id)
        if formula is None:
            continue
        per_code: dict[str, pd.Series] = {}
        for code, group in frame.groupby("ts_code"):
            group = group.sort_values("trade_date")
            values = formula(group).astype(float)
            values.index = group["trade_date"]
            per_code[code] = values
        panels[factor_id] = pd.DataFrame(per_code).sort_index()
    return panels


def load_factor_panel_from_parquet(
    factor_dir: str | Path,
    factor_ids: tuple[str, ...] = FACTOR_IDS,
) -> dict[str, pd.DataFrame]:
    """从 ``factor_values/*.parquet`` 读因子面板（long 格式 → date x asset）。"""
    factor_dir = Path(factor_dir)
    panels: dict[str, pd.DataFrame] = {}
    for factor_id in factor_ids:
        path = factor_dir / f"{factor_id}.parquet"
        if not path.exists():
            continue
        frame = pd.read_parquet(path)
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        panel = frame.pivot_table(index="date", columns="asset_code", values="value")
        panels[factor_id] = panel.sort_index()
    return panels


# ---------------------------------------------------------------------------
# forward return / IC / turnover / 分位收益
# ---------------------------------------------------------------------------

def compute_forward_returns(close_panel: pd.DataFrame, forward_period: int) -> pd.DataFrame:
    """N 期前瞻收益：``close.pct_change().shift(-N)``。"""
    return close_panel.pct_change().shift(-forward_period)


def cross_sectional_ic_series(
    factor_panel: pd.DataFrame,
    daily_return_panel: pd.DataFrame,
    forward_period: int,
    min_cross_section: int = MIN_CROSS_SECTION,
) -> pd.Series:
    """逐日横截面 IC 序列（IC 分布数据源）。

    与 ``evaluate_factor_effectiveness`` 的 DataFrame 分支同一对齐口径：
    forward return 为 ``daily_return.shift(-forward_period)``，每日期内 dropna 后
    对至少 ``min_cross_section`` 个资产计算 Pearson 相关。用 numpy 行循环加速。
    """
    fwd = daily_return_panel.shift(-forward_period)
    f, r = factor_panel.align(fwd, join="inner", axis=0)
    f_arr = np.asarray(f, dtype=float)
    r_arr = np.asarray(r, dtype=float)
    dates = f.index
    n = len(f_arr)
    ics = np.full(n, np.nan)
    for i in range(n):
        fi = f_arr[i]
        ri = r_arr[i]
        mask = ~(np.isnan(fi) | np.isnan(ri))
        if int(mask.sum()) < min_cross_section:
            continue
        fv = fi[mask] - fi[mask].mean()
        rv = ri[mask] - ri[mask].mean()
        denom = np.sqrt(float((fv * fv).sum()) * float((rv * rv).sum()))
        if denom <= 0:
            continue
        ics[i] = float((fv * rv).sum()) / denom
    return pd.Series(ics, index=dates, name="ic").dropna()


def _quantile_buckets(factor_panel: pd.DataFrame, quantiles: int) -> pd.DataFrame:
    """逐日横截面分位分桶（0..q-1），某日样本不足时该行全 NaN。numpy 行循环加速。"""
    arr = np.asarray(factor_panel, dtype=float)
    n_rows, n_cols = arr.shape
    out = np.full((n_rows, n_cols), np.nan)
    for i in range(n_rows):
        row = arr[i]
        valid_mask = ~np.isnan(row)
        valid_count = int(valid_mask.sum())
        if valid_count < 2:
            continue
        values = row[valid_mask]
        # rank method="first"：排序后按出现顺序打破平局。
        order = np.argsort(values, kind="mergesort")
        ranks = np.empty(valid_count)
        ranks[order] = np.arange(1, valid_count + 1, dtype=float)
        q = min(quantiles, valid_count)
        # qcut 等频分桶：rank 1..n 均分成 q 组。
        bin_width = valid_count / q
        bin_id = np.floor((ranks - 1.0) / bin_width).astype(float)
        np.clip(bin_id, 0, q - 1, out=bin_id)
        col_idx = np.flatnonzero(valid_mask)
        out[i, col_idx] = bin_id
    return pd.DataFrame(out, index=factor_panel.index, columns=factor_panel.columns)


def turnover(
    factor_panel: pd.DataFrame,
    forward_period: int,
    quantiles: int = QUANTILES,
) -> float:
    """alphalens 口径换手率。

    按 forward_period 对因子值横截面分位分桶，比较间隔为 ``forward_period``
    的两期（t 与 t+forward_period），取桶变化比例的平均，每资产贡献
    ``|Δbucket| / 2``（归一）；无足够样本或无变化时为 0.0。
    """
    buckets = _quantile_buckets(factor_panel, quantiles)
    values = buckets.to_numpy(dtype=float)
    step = max(1, int(forward_period))
    n = len(values)
    period_changes: list[float] = []
    for t in range(n - step):
        cur, nxt = values[t], values[t + step]
        mask = ~(np.isnan(cur) | np.isnan(nxt))
        if int(mask.sum()) < 2:
            continue
        period_changes.append(float(np.abs(nxt[mask] - cur[mask]).mean() / 2.0))
    return float(np.mean(period_changes)) if period_changes else 0.0


def _monotonicity(means: list[float | None]) -> str | None:
    """分位组均值单调性：increasing / decreasing / non_monotonic / None。"""
    valid = [value for value in means if value is not None]
    if len(valid) < 3:
        return None
    increasing = all(valid[i] <= valid[i + 1] for i in range(len(valid) - 1))
    decreasing = all(valid[i] >= valid[i + 1] for i in range(len(valid) - 1))
    if increasing:
        return "increasing"
    if decreasing:
        return "decreasing"
    return "non_monotonic"


def quantile_returns(
    factor_panel: pd.DataFrame,
    forward_return_panel: pd.DataFrame,
    forward_period: int,
    quantiles: int = QUANTILES,
) -> pd.DataFrame:
    """各分位组平均 forward return + 单调性。

    返回 long 表：``quantile, mean_forward_return, obs``（obs 为该分位组样本数）。
    """
    buckets = _quantile_buckets(factor_panel, quantiles)
    rows: list[dict[str, Any]] = []
    means: list[float | None] = []
    for q in range(quantiles):
        mask = buckets.eq(q)
        masked = forward_return_panel.where(mask)
        series = masked.stack()
        series = series[np.isfinite(series.astype(float))]
        mean = float(series.mean()) if not series.empty else None
        means.append(mean)
        rows.append({"quantile": q, "mean_forward_return": mean, "obs": int(mask.sum().sum())})
    table = pd.DataFrame(rows)
    table["monotonicity"] = _monotonicity(means)
    return table


def _ic_stats(ic_series: pd.Series) -> dict[str, float | int | None]:
    if ic_series.empty:
        return {"ic_obs": 0, "ic_std": None, "ic_positive_ratio": None}
    std = float(ic_series.std(ddof=1)) if len(ic_series) > 1 else None
    return {
        "ic_obs": int(len(ic_series)),
        "ic_std": std,
        "ic_positive_ratio": float((ic_series > 0).mean()),
    }


def build_tear_sheet(
    factor_panels: dict[str, pd.DataFrame],
    close_panel: pd.DataFrame,
    forward_periods: tuple[int, ...] = DEFAULT_FORWARD_PERIODS,
    quantiles: int = QUANTILES,
    factor_ids: tuple[str, ...] = FACTOR_IDS,
) -> dict[str, Any]:
    """汇总四类 tear sheet，返回嵌套 dict。

    结构：:
        {
          factor_id: {
            "ic_decay": DataFrame(forward_period, ic, rank_ic, icir, group_spread,
                                  gross_return, net_return, sample_count,
                                  ic_obs, ic_std, ic_positive_ratio),
            "ic_series": {forward_period: Series},          # IC 分布（逐日 IC）
            "quantile_returns": {forward_period: DataFrame},
            "turnover": {forward_period: float},
          }
        }
    """
    daily_returns = close_panel.pct_change()
    tear: dict[str, Any] = {}
    for factor_id in factor_ids:
        factor_panel = factor_panels.get(factor_id)
        if factor_panel is None or factor_panel.empty:
            continue
        decay_rows: list[dict[str, Any]] = []
        ic_series_by_period: dict[int, pd.Series] = {}
        qr_by_period: dict[int, pd.DataFrame] = {}
        turnover_by_period: dict[int, float] = {}
        for period in forward_periods:
            result = evaluate_factor_effectiveness(
                factor_panel,
                daily_returns,
                factor_id=factor_id,
                horizon="medium",
                forward_period=period,
                quantiles=quantiles,
                min_samples=MIN_SAMPLES,
            )
            ic_series = cross_sectional_ic_series(factor_panel, daily_returns, period)
            ic_series_by_period[period] = ic_series
            fwd = compute_forward_returns(close_panel, period)
            qr_by_period[period] = quantile_returns(factor_panel, fwd, period, quantiles)
            turnover_by_period[period] = turnover(factor_panel, period, quantiles)
            decay_rows.append({
                "forward_period": period,
                "ic": result.ic,
                "rank_ic": result.rank_ic,
                "icir": result.icir,
                "group_spread": result.group_spread,
                "gross_return": result.gross_return,
                "net_return": result.net_return,
                "sample_count": result.sample_count,
                **_ic_stats(ic_series),
            })
        tear[factor_id] = {
            "ic_decay": pd.DataFrame(decay_rows),
            "ic_series": ic_series_by_period,
            "quantile_returns": qr_by_period,
            "turnover": turnover_by_period,
        }
    return tear


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

def _fmt(value: float | None, digits: int = 4) -> str:
    return "NA" if value is None else f"{value:.{digits}f}"


def render_summary_text(tear: dict[str, Any]) -> str:
    """控制台 ASCII 汇总文本。"""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("Factor tear sheet summary (alphalens-style)")
    lines.append("=" * 70)
    for factor_id, sheets in tear.items():
        lines.append("")
        lines.append(f"[{factor_id}]")
        decay = sheets["ic_decay"]
        header = "| period |    IC   | RankIC  |  ICIR   | spread  | gross   | net     |  obs  | pos%  |"
        lines.append(header)
        lines.append("|" + "|".join("-" * (len(part) - 2) for part in header.split("|")) + "|")
        for _, row in decay.iterrows():
            lines.append(
                f"| {int(row['forward_period']):<6} | {_fmt(row['ic']):<7} | "
                f"{_fmt(row['rank_ic']):<7} | {_fmt(row['icir']):<7} | "
                f"{_fmt(row['group_spread']):<7} | {_fmt(row['gross_return']):<7} | "
                f"{_fmt(row['net_return']):<7} | {int(row['ic_obs']):<5} | "
                f"{_fmt(row['ic_positive_ratio'], 3):<5} |"
            )
        tr = sheets["turnover"]
        tr_text = "  ".join(f"h{p}={_fmt(tr[p]):s}" for p in sorted(tr))
        lines.append(f"  turnover (|Δbucket|/2): {tr_text}")
        for period, qr in sheets["quantile_returns"].items():
            monotonic = qr["monotonicity"].iloc[0]
            qtext = "  ".join(
                f"q{int(r['quantile'])}={_fmt(r['mean_forward_return'])}" for _, r in qr.iterrows()
            )
            lines.append(f"  quantile_returns h={period} [{monotonic}]: {qtext}")
    lines.append("")
    lines.append("forward return: close.pct_change().shift(-N)")
    return "\n".join(lines)


def export_tear_csv(
    tear: dict[str, Any],
    out_dir: str | Path,
    factor_ids: tuple[str, ...] = FACTOR_IDS,
) -> Path:
    """导出 IC decay / quantile returns / turnover 到 ASCII 文件名 CSV。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for factor_id in factor_ids:
        sheets = tear.get(factor_id)
        if sheets is None:
            continue
        sheets["ic_decay"].to_csv(out_dir / f"{factor_id}_ic_decay.csv", index=False)
        qr_rows: list[pd.DataFrame] = []
        for period, qr in sheets["quantile_returns"].items():
            tmp = qr.copy()
            tmp["forward_period"] = period
            qr_rows.append(tmp)
        pd.concat(qr_rows, ignore_index=True).to_csv(
            out_dir / f"{factor_id}_quantile_returns.csv", index=False
        )
        pd.DataFrame(
            [{"forward_period": p, "turnover": v} for p, v in sheets["turnover"].items()]
        ).to_csv(out_dir / f"{factor_id}_turnover.csv", index=False)
        ic_rows: list[pd.DataFrame] = []
        for period, series in sheets["ic_series"].items():
            tmp = series.rename("ic").reset_index()
            tmp["forward_period"] = period
            ic_rows.append(tmp)
        if ic_rows:
            pd.concat(ic_rows, ignore_index=True).to_csv(
                out_dir / f"{factor_id}_ic_series.csv", index=False
            )
    return out_dir


def export_tear_png(
    tear: dict[str, Any],
    out_dir: str | Path,
    factor_ids: tuple[str, ...] = FACTOR_IDS,
) -> list[str]:
    """若 matplotlib 可用则输出 PNG tear sheet；不可用返回空列表。"""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return []
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for factor_id in factor_ids:
        sheets = tear.get(factor_id)
        if sheets is None:
            continue
        decay = sheets["ic_decay"]
        fig, axes = plt.subplots(2, 2, figsize=(13, 9))
        ax = axes[0, 0]
        ax.plot(decay["forward_period"], decay["ic"], marker="o", label="IC")
        ax.plot(decay["forward_period"], decay["rank_ic"], marker="s", label="RankIC")
        ax.axhline(0, color="gray", linewidth=0.8)
        ax.set_xlabel("forward_period")
        ax.set_ylabel("IC")
        ax.set_title("IC decay")
        ax.legend()
        ax = axes[0, 1]
        for period, qr in sheets["quantile_returns"].items():
            ax.plot(qr["quantile"], qr["mean_forward_return"], marker="o", label=f"h={period}")
        ax.axhline(0, color="gray", linewidth=0.8)
        ax.set_xlabel("quantile")
        ax.set_ylabel("mean forward return")
        ax.set_title("Quantile returns")
        ax.legend()
        ax = axes[1, 0]
        tr = sheets["turnover"]
        labels = [str(p) for p in sorted(tr)]
        ax.bar(labels, [tr[p] for p in sorted(tr)])
        ax.set_title("Turnover")
        ax.set_xlabel("forward_period")
        ax = axes[1, 1]
        ax.plot(decay["forward_period"], decay["group_spread"], marker="o")
        ax.axhline(0, color="gray", linewidth=0.8)
        ax.set_xlabel("forward_period")
        ax.set_ylabel("group_spread")
        ax.set_title("Group spread by horizon")
        fig.suptitle(f"Factor tear sheet: {factor_id}")
        fig.tight_layout()
        path = out_dir / f"{factor_id}_tear_sheet.png"
        fig.savefig(path)
        plt.close(fig)
        paths.append(str(path))
    return paths


def run_tear_sheets(
    source: str = "csv",
    forward_periods: tuple[int, ...] = DEFAULT_FORWARD_PERIODS,
    csv_path: str | Path | None = None,
    factor_dir: str | Path | None = None,
    out_dir: str | Path | None = None,
    factor_ids: tuple[str, ...] = FACTOR_IDS,
    export_csv: bool = True,
    export_png: bool = True,
) -> dict[str, Any]:
    """统一入口：加载因子面板与价格 → 汇总 tear sheet → 可选导出。"""
    csv_path = Path(csv_path) if csv_path else DEFAULT_CSV_PATH
    factor_dir = Path(factor_dir) if factor_dir else DEFAULT_FACTOR_DIR
    out_dir = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    close, _ = load_price_panels(csv_path)
    if source == "parquet":
        panels = load_factor_panel_from_parquet(factor_dir, factor_ids)
    else:
        panels = compute_factor_panel_from_csv(csv_path, factor_ids)
    tear = build_tear_sheet(panels, close, forward_periods, factor_ids=factor_ids)
    summary = render_summary_text(tear)
    csv_dir: Path | None = None
    png_paths: list[str] = []
    if export_csv:
        csv_dir = out_dir / "csv"
        export_tear_csv(tear, csv_dir, factor_ids)
    if export_png:
        png_paths = export_tear_png(tear, out_dir / "png", factor_ids)
    return {
        "tear": tear,
        "summary": summary,
        "csv_dir": str(csv_dir) if csv_dir else None,
        "png_paths": png_paths,
        "source": source,
        "forward_periods": list(forward_periods),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_periods(raw: str) -> tuple[int, ...]:
    parts = [int(item.strip()) for item in raw.split(",") if item.strip()]
    return tuple(dict.fromkeys(parts)) or DEFAULT_FORWARD_PERIODS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="alphalens 借鉴的因子 tear sheet（IC/分位收益/衰减/turnover）"
    )
    parser.add_argument(
        "--source",
        choices=["csv", "parquet"],
        default="csv",
        help="因子值来源：csv=从 fund_daily.csv 即时计算；parquet=读 factor_values/*.parquet",
    )
    parser.add_argument(
        "--forward-periods",
        default="1,5,20",
        help="forward_period 列表，逗号分隔（如 1,5,20）",
    )
    parser.add_argument("--csv-path", default=None, help="行情 CSV 路径（默认 data/fund_daily.csv）")
    parser.add_argument("--factor-dir", default=None, help="因子 parquet 目录（默认 research_store/factor_values）")
    parser.add_argument("--out-dir", default=None, help="输出目录（默认 reports/factor_tear）")
    parser.add_argument("--no-csv", action="store_true", help="不导出 CSV")
    parser.add_argument("--no-png", action="store_true", help="不导出 PNG")
    args = parser.parse_args(argv)

    periods = _parse_periods(args.forward_periods)
    result = run_tear_sheets(
        source=args.source,
        forward_periods=periods,
        csv_path=args.csv_path,
        factor_dir=args.factor_dir,
        out_dir=args.out_dir,
        export_csv=not args.no_csv,
        export_png=not args.no_png,
    )
    print(result["summary"])
    if result["csv_dir"]:
        print(f"CSV exported to: {result['csv_dir']}")
    if args.no_png:
        print("PNG: skipped (--no-png).")
    elif result["png_paths"]:
        print("PNG exported:")
        for path in result["png_paths"]:
            print(f"  {path}")
    else:
        print("PNG: matplotlib unavailable, degraded to pure data table.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
