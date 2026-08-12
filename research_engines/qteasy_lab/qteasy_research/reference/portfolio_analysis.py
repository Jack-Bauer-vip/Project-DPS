"""L3 组合分析核心模块（因子风险模型 + 组合风险收益 + 比例建议 + L4 组合暴露）。

设计总纲（《B侧研究系统能力需求规格》§8 / 《模块级梳理清单》§6）：

- **Σ 估计（主方案）**：因子模型协方差
  ``Σ = B Σf B' + diag(σ²ε)``，年化系数 252；
  ``Σf`` 用 Ledoit-Wolf 收缩（PSD 保证，sample 对照），
  EWMA(λ=0.94) 稳健性对照；报告三组组合波动，不一致时警告。
- **X 组装**：复用 ``factor_research.estimate_asset_factor_exposure``
  （OLS 滚动 beta）聚合全资产×全因子；窗口 252
  （下限 120）。缺失不虚构：不足样本 → NaN+warning；
  整行剔除进 ``quality_level=D``；因子列有效资产 < 3 整列剔除。
- **Σf 输入**：价格因子用截面多空组合收益
  （top5/bottom5 等权、周调仓）；宏观因子 ΔDGS30/ΔDFII10
  月差（--include-macro 时）。
- **组合风险收益**：组合年化收益/波动
  （因子模型 + sample + EWMA 三口径）/最大回撤/Sharpe/相关性矩阵
  （min_overlap=60）/资产级 RC/因子级 RC + **L4 组合因子暴露 g=w'X**。
- **比例建议**：风险平价（scipy SLSQP；无 scipy 时确定性固定点迭代 +
  逆波动回退，status 区分）；有效前沿扫 λ∈(0.5,10) 40 点
  （无 scipy 诚实省略+警告）；bounds 来自契约 min/max_weight。

安全边界：本模块只做确定性统计 + 组装，不生成交易指令；
``approval_policy="REFERENCE_ONLY"``，恒不自动 APPROVED；机器产出全 ASCII。
"""

from __future__ import annotations

import datetime as _dt
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qteasy_research.pretrade.factor_research import estimate_asset_factor_exposure
from qteasy_research.reference.config import PROJECT_ROOT
from qteasy_research.reference.metadata import build_header, embed_header_csv, today_iso

# ---------------------------------------------------------------------------
# 常量（单一事实源，报告 Assumptions 引用）
# ---------------------------------------------------------------------------

PORTFOLIO_ANALYSIS_SCHEMA = "portfolio-analysis-v1"
APPROVAL_POLICY = "REFERENCE_ONLY"

# 默认价格因子（与 factor_tear.FACTOR_IDS 一致）。
DEFAULT_FACTORS: tuple[str, ...] = (
    "momentum_60d",
    "momentum_120d",
    "low_volatility_20d",
    "liquidity_turnover",
)
# 默认宏观因子：DGS30 / DFII10 月差（--include-macro 时启用）。
DEFAULT_MACRO_FACTORS: tuple[str, ...] = ("DGS30", "DFII10")

# X 暴露估计窗口。
EXPOSURE_WINDOW = 252
EXPOSURE_MIN_WINDOW = 120
# 因子列有效资产数下限：低于则整列剔除（不虚构）。
MIN_VALID_ASSETS_PER_FACTOR = 3
# 资产行有效因子数下限：低于则整行剔除进 quality_level=D。
MIN_VALID_FACTORS_PER_ASSET = 1

# 相关性矩阵最小共同观测数。
CORRELATION_MIN_OVERLAP = 60

# 特异方差 floor（日频方差下限，防零方差退化）。
IDIO_VAR_FLOOR = 1e-8

# EWMA 稳健性对照衰减系数（RiskMetrics）。
EWMA_LAMBDA = 0.94

# 年化交易日数。
TRADING_DAYS = 252

# 风险平价固定点迭代参数。
RP_MAX_ITER = 2000
RP_TOL = 1e-10

# 有效前沿扫参区间与点数。
FRONTIER_LAMBDAS = (0.5, 10.0)
FRONTIER_POINTS = 40

# scipy 可用性（缺失时确定性降级：风险平价固定点迭代 + 有效前沿诚实省略）。
try:  # pragma: no cover - 环境探测
    import scipy.optimize as _scipy_optimize  # noqa: F401

    SCIPY_AVAILABLE = True
except Exception:  # pragma: no cover
    SCIPY_AVAILABLE = False


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _make_psd(cov: np.ndarray, eig_floor: float = 1e-12) -> np.ndarray:
    """对称化 + 特征值下限，保证 PSD（Ledoit-Wolf / 样本协方差的最后一道保险）。"""
    sym = (cov + cov.T) / 2.0
    eigvals, eigvecs = np.linalg.eigh(sym)
    eigvals = np.clip(eigvals, eig_floor, None)
    return eigvecs @ np.diag(eigvals) @ eigvecs.T


def align_close_panel(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """把 ``load_price_frames`` 的 ``{asset_id: DataFrame(trade_date, close)}``
    组装为 ``date x asset`` close 面板（各资产独立日期，缺失为 NaN，不虚构）。"""
    panel = pd.DataFrame()
    for asset_id, frame in frames.items():
        if frame is None or frame.empty:
            continue
        sub = frame.copy()
        sub["trade_date"] = pd.to_datetime(sub["trade_date"], errors="coerce")
        sub = sub.dropna(subset=["trade_date", "close"])
        series = (
            sub.set_index("trade_date")["close"]
            .astype(float)
            .sort_index()
            .groupby(level=0)
            .last()
        )
        panel[asset_id] = series
    return panel.sort_index()


def daily_returns_panel(close_panel: pd.DataFrame) -> pd.DataFrame:
    """日收益面板：``close.pct_change()``。"""
    return close_panel.pct_change()


def _weights_dict(assets: list[str], w: np.ndarray) -> dict[str, float]:
    return {str(asset): float(value) for asset, value in zip(assets, w)}


def _project_to_bounds(w: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """把权重投影回 ``[lo, hi]`` 且求和=1（迭代再分配给非绑定分量）。"""
    w = np.asarray(w, dtype=float).copy()
    lo = np.asarray(lo, dtype=float)
    hi = np.asarray(hi, dtype=float)
    lo = np.minimum(lo, hi)
    for _ in range(200):
        w = np.clip(w, lo, hi)
        s = float(w.sum())
        if abs(s - 1.0) < 1e-12:
            return w
        free = (w > lo + 1e-12) & (w < hi - 1e-12)
        if not np.any(free):
            w = w / s if s > 0 else w
            w = np.clip(w, lo, hi)
            break
        slack = 1.0 - s
        free_w = w[free]
        w[free] = free_w + slack * (free_w / free_w.sum())
    s = float(w.sum())
    if abs(s - 1.0) > 1e-9 and s > 0:
        w = np.clip(w / s, lo, hi)
    return w


# ---------------------------------------------------------------------------
# X 暴露矩阵组装（L4 前置；复用 factor_research.estimate_asset_factor_exposure）
# ---------------------------------------------------------------------------

def _estimate_cell_beta(
    asset_returns: pd.Series,
    factor_values: pd.Series,
    *,
    asset_code: str,
    factor_id: str,
    window: int,
    min_window: int,
) -> tuple[float | None, str | None]:
    """单格 OLS beta：对齐后取最近 window 个有效观测，缺失不虚构。"""
    frame = pd.concat(
        [
            pd.to_numeric(factor_values, errors="coerce").rename("factor"),
            pd.to_numeric(asset_returns, errors="coerce").rename("return"),
        ],
        axis=1,
    )
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()
    if len(frame) < min_window:
        return None, f"valid samples {len(frame)} < min {min_window}"
    frame = frame.iloc[-window:]
    if float(frame["factor"].var(ddof=1)) <= 0:
        return None, "factor variance is 0; cannot estimate OLS beta"
    exposure = estimate_asset_factor_exposure(
        frame["return"],
        frame["factor"],
        asset_code=asset_code,
        factor_id=factor_id,
        horizon="medium",
        min_samples=min_window,
    )
    beta = _finite(exposure.rolling_beta)
    if beta is None:
        return None, "OLS beta could not be computed"
    return beta, None


def assemble_exposure_matrix(
    close_panel: pd.DataFrame,
    factor_panels: dict[str, pd.DataFrame],
    *,
    window: int = EXPOSURE_WINDOW,
    min_window: int = EXPOSURE_MIN_WINDOW,
    min_valid_assets_per_factor: int = MIN_VALID_ASSETS_PER_FACTOR,
    min_valid_factors_per_asset: int = MIN_VALID_FACTORS_PER_ASSET,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """组装资产×因子暴露矩阵 X（全资产×全因子 OLS rolling beta）。

    返回 ``(X, report)``。``report`` 含 ``warnings`` / ``dropped_assets`` /
    ``dropped_factors`` / ``quality_levels``。缺失不虚构：不足样本 NaN+warning；
    整行剔除进 quality_level=D；因子列有效资产 < 下限整列剔除。
    """
    assets = list(close_panel.columns)
    daily_ret = close_panel.pct_change()
    warnings: list[str] = []
    rows: dict[str, dict[str, float | None]] = {}
    cell_warnings: list[str] = []

    for asset in assets:
        ar = daily_ret[asset]
        rows[asset] = {}
        for factor_id, panel in factor_panels.items():
            if panel is None or panel.empty:
                continue
            if asset not in panel.columns:
                rows[asset][factor_id] = None
                continue
            fv = panel[asset].reindex(ar.index)
            beta, warn = _estimate_cell_beta(
                ar, fv, asset_code=asset, factor_id=factor_id,
                window=window, min_window=min_window,
            )
            rows[asset][factor_id] = beta
            if warn is not None:
                cell_warnings.append(f"{asset}:{factor_id} {warn}")

    matrix = pd.DataFrame.from_dict(rows, orient="index")
    matrix.index.name = "asset_id"
    matrix.columns.name = "factor_id"

    # 因子列有效资产 < 下限 → 整列剔除。
    dropped_factors: list[str] = []
    for factor_id in matrix.columns:
        valid = int(matrix[factor_id].notna().sum())
        if valid < min_valid_assets_per_factor:
            dropped_factors.append(factor_id)
            warnings.append(
                f"factor column {factor_id} valid assets {valid} < "
                f"{min_valid_assets_per_factor}; column dropped (not fabricated)"
            )
    if dropped_factors:
        matrix = matrix.drop(columns=dropped_factors)

    # 资产行有效因子 < 下限 → 整行剔除进 quality_level=D。
    quality_levels: dict[str, str] = {}
    dropped_assets: list[str] = []
    for asset in list(matrix.index):
        valid = int(matrix.loc[asset].notna().sum())
        if valid < min_valid_factors_per_asset:
            quality_levels[asset] = "D"
            dropped_assets.append(asset)
            warnings.append(
                f"asset {asset} valid factors {valid} < {min_valid_factors_per_asset}; "
                "row dropped (quality_level=D, not fabricated)"
            )
        else:
            quality_levels[asset] = "A"
    if dropped_assets:
        matrix = matrix.drop(index=dropped_assets)

    warnings.extend(cell_warnings)
    report: dict[str, Any] = {
        "warnings": warnings,
        "dropped_assets": dropped_assets,
        "dropped_factors": dropped_factors,
        "quality_levels": quality_levels,
    }
    return matrix, report


# ---------------------------------------------------------------------------
# Σf 输入：价格因子截面多空收益 + 宏观因子月差
# ---------------------------------------------------------------------------

def build_price_factor_returns(
    close_panel: pd.DataFrame,
    factor_panels: dict[str, pd.DataFrame],
    *,
    top_n: int = 5,
    bottom_n: int = 5,
    rebalance_every: int = 5,
) -> pd.DataFrame:
    """价格因子每日截面多空组合收益（top_n/bottom_n 等权、每 rebalance_every 交易日调仓）。

    每个调仓日 t0：按因子值横截面排名取 top_n/bottom_n；持有期 ``[t0, t1)`` 内每日
    因子收益 = 等权 top_n 日收益均值 - 等权 bottom_n 日收益均值。截面不足 top+bottom
    或持有日收益缺失 → 该日 NaN（不虚构）。
    """
    daily_ret = close_panel.pct_change()
    calendar = list(close_panel.index)
    if len(calendar) < rebalance_every + 1:
        return pd.DataFrame(index=close_panel.index)
    rebalance_idx = list(range(0, len(calendar), rebalance_every))
    out = pd.DataFrame(index=close_panel.index)
    for factor_id, panel in factor_panels.items():
        if panel is None or panel.empty:
            continue
        common = [c for c in panel.columns if c in close_panel.columns]
        if len(common) < top_n + bottom_n:
            continue
        p = panel[common].reindex(close_panel.index)
        ret_col = pd.Series(np.nan, index=close_panel.index, dtype=float)
        for k in range(len(rebalance_idx) - 1):
            t0 = calendar[rebalance_idx[k]]
            t1 = calendar[rebalance_idx[k + 1]]
            frow = p.loc[t0].dropna()
            if len(frow) < top_n + bottom_n:
                continue
            ranked = frow.sort_values(ascending=False)
            top = list(ranked.index[:top_n])
            bottom = list(ranked.index[-bottom_n:])
            mask = (close_panel.index >= t0) & (close_panel.index < t1)
            for d in close_panel.index[mask]:
                top_ret = daily_ret.loc[d, top].dropna()
                bot_ret = daily_ret.loc[d, bottom].dropna()
                if top_ret.empty or bot_ret.empty:
                    continue
                ret_col.loc[d] = float(top_ret.mean() - bot_ret.mean())
        out[factor_id] = ret_col
    return out


def build_macro_factor_returns(
    macro_frames: dict[str, pd.DataFrame],
    calendar: pd.Index,
) -> pd.DataFrame:
    """宏观因子日序列：月差（月末值月环比差分）ffill 到日。

    口径与宏观监控一致（ΔDGS30/ΔDFII10 月差），为与价格因子日频
    Σf 对齐，把月末月差 ffill 到当月每日。返回 ``date x macro_factor``。
    """
    index = pd.DatetimeIndex(calendar)
    out = pd.DataFrame(index=index)
    for series_id, frame in macro_frames.items():
        if frame is None or frame.empty:
            continue
        s = frame.copy()
        date_col = next(
            (c for c in ("date", "trade_date", "observation_date") if c in s.columns),
            None,
        )
        val_col = "value" if "value" in s.columns else None
        if date_col is None or val_col is None:
            continue
        s[date_col] = pd.to_datetime(s[date_col], errors="coerce")
        s = s.dropna(subset=[date_col, val_col])
        if s.empty:
            continue
        series = s.set_index(date_col)[val_col].astype(float).sort_index()
        series = series[~series.index.duplicated(keep="last")]
        monthly = series.groupby(series.index.to_period("M")).last()
        monthly_diff = monthly.diff()
        monthly_diff.index = monthly_diff.index.to_timestamp(how="end")
        col = monthly_diff.reindex(index, method="ffill")
        out[series_id] = col.astype(float)
    return out


# ---------------------------------------------------------------------------
# Σf：Ledoit-Wolf 收缩（主）/ sample（对照）/ EWMA（稳健性对照）
# ---------------------------------------------------------------------------

def _ledoit_wolf_cov(returns: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """Ledoit-Wolf 常数相关收缩（2004）：PSD 保证，返回 (收缩协方差, 收缩强度)。"""
    x = returns.to_numpy(dtype=float)
    T, n = x.shape
    if T < 2 or n < 2:
        return returns.cov(), 0.0
    xc = x - x.mean(axis=0)
    s = (xc.T @ xc) / (T - 1.0)
    if n == 1:
        return pd.DataFrame(s, index=returns.columns, columns=returns.columns), 0.0
    diag = np.diag(s)
    std = np.sqrt(np.maximum(diag, 1e-12))
    corr = s / np.outer(std, std)
    r_bar = (float(corr.sum()) - n) / (n * (n - 1))
    target = np.full((n, n), r_bar) * np.outer(std, std)
    np.fill_diagonal(target, diag)

    pi_sum = 0.0
    rho_sum = 0.0
    gamma_sum = 0.0
    for i in range(n):
        for j in range(i, n):
            cross = xc[:, i] * xc[:, j]
            s_ij = s[i, j]
            f_ij = target[i, j]
            pi_ij = float(np.mean((cross - s_ij) ** 2))
            rho_ij = float(np.mean((cross - f_ij) ** 2 - 2.0 * (cross - s_ij) * (cross - f_ij)))
            gamma_ij = (f_ij - s_ij) ** 2
            if i == j:
                pi_sum += pi_ij
                rho_sum += rho_ij
                gamma_sum += gamma_ij
            else:
                pi_sum += 2.0 * pi_ij
                rho_sum += 2.0 * rho_ij
                gamma_sum += 2.0 * gamma_ij
    if gamma_sum <= 0:
        shrink = 0.0
    else:
        shrink = float(np.clip((pi_sum - rho_sum) / gamma_sum, 0.0, 1.0))
    shrunk = shrink * target + (1.0 - shrink) * s
    shrunk = _make_psd(shrunk)
    return (
        pd.DataFrame(shrunk, index=returns.columns, columns=returns.columns),
        shrink,
    )


def _ewma_cov(returns: pd.DataFrame, lambda_: float = EWMA_LAMBDA) -> pd.DataFrame:
    """RiskMetrics EWMA 协方差：``Sigma_t = lambda*Sigma_{t-1} + (1-lambda)*y_t y_t'``。"""
    x = returns.to_numpy(dtype=float)
    T, n = x.shape
    if T == 0 or n == 0:
        return pd.DataFrame(index=returns.columns, columns=returns.columns)
    cov = np.zeros((n, n))
    for t in range(T):
        cov = lambda_ * cov + (1.0 - lambda_) * np.outer(x[t], x[t])
    return pd.DataFrame(cov, index=returns.columns, columns=returns.columns)


def factor_covariance(
    factor_returns: pd.DataFrame,
    *,
    ewma_lambda: float = EWMA_LAMBDA,
) -> dict[str, Any]:
    """Σf 三种估计：sample / Ledoit-Wolf（主） / EWMA（对照）。"""
    frame = factor_returns.dropna()
    if frame.shape[0] < 2 or frame.shape[1] < 1:
        return {
            "sample": pd.DataFrame(),
            "ledoit_wolf": pd.DataFrame(),
            "ewma": pd.DataFrame(),
            "shrinkage": 0.0,
            "method": "ledoit_wolf",
            "observations": 0,
            "warnings": ["factor return panel insufficient valid observations; Sigma_f empty (not fabricated)"],
        }
    sample = frame.cov()
    lw, shrink = _ledoit_wolf_cov(frame)
    ewma = _ewma_cov(frame, ewma_lambda)
    return {
        "sample": sample,
        "ledoit_wolf": lw,
        "ewma": ewma,
        "shrinkage": float(shrink),
        "method": "ledoit_wolf",
        "observations": int(frame.shape[0]),
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# Σε：特异方差（带 floor）
# ---------------------------------------------------------------------------

def idiosyncratic_variance(
    close_panel: pd.DataFrame,
    X: pd.DataFrame,
    factor_returns: pd.DataFrame,
    *,
    floor: float = IDIO_VAR_FLOOR,
) -> pd.DataFrame:
    """Σε 对角线：残差 ``r_i - X_i' f`` 日频方差，带 floor（不虚构为 0）。"""
    daily_ret = close_panel.pct_change()
    rows: list[dict[str, Any]] = []
    for asset in X.index:
        ar = daily_ret[asset]
        total = pd.Series(0.0, index=ar.index, dtype=float)
        has = False
        for factor_id in X.columns:
            beta = _finite(X.loc[asset, factor_id])
            if beta is None or factor_id not in factor_returns.columns:
                continue
            fr = factor_returns[factor_id].reindex(ar.index).fillna(0.0)
            total = total + beta * fr
            has = True
        if not has:
            rows.append({"asset": asset, "idio_var": np.nan, "idio_std": np.nan})
            continue
        resid = (ar - total).replace([np.inf, -np.inf], np.nan).dropna()
        var = float(resid.var(ddof=1)) if len(resid) > 1 else np.nan
        if not math.isfinite(var) or var < floor:
            var = float(floor)
        rows.append({"asset": asset, "idio_var": var, "idio_std": float(np.sqrt(var))})
    return pd.DataFrame(rows).set_index("asset")


# ---------------------------------------------------------------------------
# 组合风险收益 / 风险贡献 / L4 组合暴露
# ---------------------------------------------------------------------------

def factor_model_cov(
    X: pd.DataFrame,
    factor_cov: pd.DataFrame,
    idio_var: pd.DataFrame,
) -> pd.DataFrame:
    """因子模型日频协方差 ``Σ = B Σf B' + diag(σ²ε)``（B=X）。"""
    assets = list(X.index)
    B = X.reindex(columns=factor_cov.columns).fillna(0.0).to_numpy(dtype=float)
    sig = factor_cov.to_numpy(dtype=float)
    B_sig = B @ sig @ B.T
    idio = idio_var.reindex(assets)["idio_var"].fillna(0.0).to_numpy(dtype=float)
    cov = B_sig + np.diag(idio)
    return pd.DataFrame(cov, index=assets, columns=assets)


def portfolio_vol(weights: dict[str, float], cov_daily: pd.DataFrame) -> float:
    """组合年化波动：``sqrt(252 * w' Sigma w)``（日频协方差）。"""
    assets = list(cov_daily.columns)
    w = np.array([weights.get(a, 0.0) for a in assets], dtype=float)
    var = float(w @ cov_daily.to_numpy() @ w)
    return float(math.sqrt(max(var, 0.0)) * math.sqrt(TRADING_DAYS))


def portfolio_return(weights: dict[str, float], close_panel: pd.DataFrame) -> float:
    """组合年化收益：``mean(日组合收益) * 252``（日收益等权加权）。"""
    daily_ret = close_panel.pct_change()
    assets = list(close_panel.columns)
    w = np.array([weights.get(a, 0.0) for a in assets], dtype=float)
    pr = daily_ret.to_numpy(dtype=float) @ w
    pr = pr[np.isfinite(pr)]
    if len(pr) == 0:
        return 0.0
    return float(np.mean(pr) * TRADING_DAYS)


def max_drawdown(weights: dict[str, float], close_panel: pd.DataFrame) -> float | None:
    """组合最大回撤（日度净值曲线）。"""
    daily_ret = close_panel.pct_change()
    assets = list(close_panel.columns)
    w = np.array([weights.get(a, 0.0) for a in assets], dtype=float)
    pr = daily_ret.fillna(0.0).to_numpy(dtype=float) @ w
    wealth = np.cumprod(1.0 + pr)
    if len(wealth) == 0:
        return None
    dd = float((wealth / np.maximum.accumulate(wealth) - 1.0).min())
    return dd


def correlation_matrix(
    close_panel: pd.DataFrame,
    min_overlap: int = CORRELATION_MIN_OVERLAP,
) -> pd.DataFrame:
    """资产日收益相关矩阵；共同观测 < min_overlap 的对置 NaN（不虚构）。"""
    return close_panel.pct_change().corr(min_periods=min_overlap)


def asset_risk_contributions(
    weights: dict[str, float],
    cov_daily: pd.DataFrame,
) -> pd.DataFrame:
    """资产级风险贡献：``RC_i = w_i * (Sigma w)_i / (w' Sigma w)``，求和=1。"""
    assets = list(cov_daily.columns)
    w = np.array([weights.get(a, 0.0) for a in assets], dtype=float)
    cov = cov_daily.to_numpy(dtype=float)
    sw = cov @ w
    var = float(w @ sw)
    rows: list[dict[str, Any]] = []
    if var <= 0:
        for i, asset in enumerate(assets):
            rows.append({"asset": asset, "weight": float(w[i]), "rc": np.nan})
        return pd.DataFrame(rows)
    for i, asset in enumerate(assets):
        rc = float(w[i] * sw[i] / var)
        rows.append({"asset": asset, "weight": float(w[i]), "rc": rc, "marginal_rc": float(sw[i])})
    return pd.DataFrame(rows)


def factor_risk_contributions(
    weights: dict[str, float],
    X: pd.DataFrame,
    factor_cov: pd.DataFrame,
    idio_var: pd.DataFrame,
) -> pd.DataFrame:
    """因子级风险贡献（含 idiosyncratic 行，总和=1）。

    ``g = w'X``（L4 组合暴露）；因子驱动方差 ``g' Σf g``；特异方差
    ``w' diag(σ²ε) w``；RC 均除以总方差。"""
    assets = [a for a in X.index if weights.get(a, 0.0) > 0]
    if not assets:
        return pd.DataFrame(columns=["factor", "rc", "marginal_rc"])
    B = X.loc[assets].reindex(columns=factor_cov.columns).fillna(0.0).to_numpy(dtype=float)
    w = np.array([weights.get(a, 0.0) for a in assets], dtype=float)
    idio = idio_var.reindex(assets)["idio_var"].fillna(0.0).to_numpy(dtype=float)
    g = w @ B
    sig = factor_cov.to_numpy(dtype=float)
    factor_var = float(g @ sig @ g)
    idio_var_total = float(np.sum(w ** 2 * idio))
    total_var = factor_var + idio_var_total
    rows: list[dict[str, Any]] = []
    if total_var <= 0:
        for factor_id in factor_cov.columns:
            rows.append({"factor": factor_id, "rc": np.nan, "marginal_rc": np.nan})
        rows.append({"factor": "idiosyncratic", "rc": np.nan, "marginal_rc": np.nan})
        return pd.DataFrame(rows)
    sg = sig @ g
    for j, factor_id in enumerate(factor_cov.columns):
        rows.append({
            "factor": factor_id,
            "rc": float(g[j] * sg[j] / total_var),
            "marginal_rc": float(sg[j]),
        })
    rows.append({
        "factor": "idiosyncratic",
        "rc": float(idio_var_total / total_var),
        "marginal_rc": float(np.mean(2.0 * w * idio)),
    })
    return pd.DataFrame(rows)


def portfolio_factor_exposure(
    weights: dict[str, float],
    X: pd.DataFrame,
) -> pd.DataFrame:
    """L4 组合因子暴露 ``g = w'X``（1 x k）。"""
    assets = list(X.index)
    w = np.array([weights.get(a, 0.0) for a in assets], dtype=float)
    g = w @ X.to_numpy(dtype=float)
    return pd.DataFrame({"exposure": g}, index=X.columns)


# ---------------------------------------------------------------------------
# 比例建议：风险平价 / 逆波动 / 等权 / 有效前沿
# ---------------------------------------------------------------------------

def inverse_vol_weights(
    cov_daily: pd.DataFrame,
    *,
    bounds: dict[str, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """逆波动率权重（比例建议三锚点之一，兼作风险平价降级回退）。"""
    assets = list(cov_daily.columns)
    if not assets:
        return {"weights": {}, "status": "error", "method": "inverse_vol", "warning": "no assets"}
    diag = np.diag(cov_daily.to_numpy(dtype=float))
    vol = np.sqrt(np.maximum(diag, 1e-12))
    inv = 1.0 / vol
    w = inv / inv.sum()
    lo, hi = _bounds_arrays(assets, bounds)
    w = _project_to_bounds(w, lo, hi)
    return {"weights": _weights_dict(assets, w), "status": "ok", "method": "inverse_vol"}


def equal_weight_weights(
    assets: list[str],
    *,
    bounds: dict[str, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """等权权重（比例建议三锚点之一）。"""
    if not assets:
        return {"weights": {}, "status": "error", "method": "equal_weight", "warning": "no assets"}
    w = np.full(len(assets), 1.0 / len(assets))
    lo, hi = _bounds_arrays(assets, bounds)
    w = _project_to_bounds(w, lo, hi)
    return {"weights": _weights_dict(assets, w), "status": "ok", "method": "equal_weight"}


def _bounds_arrays(
    assets: list[str],
    bounds: dict[str, tuple[float, float]] | None,
) -> tuple[np.ndarray, np.ndarray]:
    bounds_map = bounds or {}
    lo = np.array([bounds_map.get(a, (0.0, 1.0))[0] for a in assets], dtype=float)
    hi = np.array([bounds_map.get(a, (0.0, 1.0))[1] for a in assets], dtype=float)
    lo = np.clip(lo, 0.0, None)
    hi = np.clip(hi, None, 1.0)
    lo = np.minimum(lo, hi)
    return lo, hi


def risk_parity_weights(
    cov_daily: pd.DataFrame,
    *,
    bounds: dict[str, tuple[float, float]] | None = None,
    method: str | None = None,
) -> dict[str, Any]:
    """风险平价权重。

    - scipy SLSQP：最小化 ``sum((RC_i - 1/n)^2)``（status=ok, method=scipy_slsqp）。
    - 无 scipy：确定性固定点迭代 ``w_i ∝ 1/(Sigma w)_i``，
      status=ok / non_converged，method=fixed_point；失败回退逆波动
      （status=fallback, method=inverse_vol）。
    """
    assets = list(cov_daily.columns)
    if not assets:
        return {"weights": {}, "status": "error", "method": "none", "warning": "no assets"}
    n = len(assets)
    w0 = np.full(n, 1.0 / n)
    lo, hi = _bounds_arrays(assets, bounds)

    if method is None:
        method = "scipy_slsqp" if SCIPY_AVAILABLE else "fixed_point"

    if method == "scipy_slsqp":
        try:
            import scipy.optimize as opt

            cov = cov_daily.to_numpy(dtype=float)

            def rc_dev(w: np.ndarray) -> float:
                sw = cov @ w
                var = float(w @ sw)
                if var <= 0:
                    return 1e6
                rc = w * sw / var
                return float(np.sum((rc - 1.0 / n) ** 2))

            constraints = [{"type": "eq", "fun": lambda w: float(np.sum(w)) - 1.0}]
            bnds = [(float(lo[i]), float(hi[i])) for i in range(n)]
            res = opt.minimize(
                rc_dev, w0, method="SLSQP",
                bounds=bnds, constraints=constraints,
                options={"maxiter": 500, "ftol": 1e-12},
            )
            if res.success:
                w = _project_to_bounds(res.x, lo, hi)
                return {
                    "weights": _weights_dict(assets, w),
                    "status": "ok",
                    "method": "scipy_slsqp",
                    "objective": float(res.fun),
                }
            # SLSQP 未收敛 → 落固定点
            method = "fixed_point"
        except Exception:
            method = "fixed_point"

    if method == "fixed_point":
        cov = cov_daily.to_numpy(dtype=float)
        w = w0.copy()
        converged = False
        # 朴素迭代 w <- normalize(1/(Σw)) 对近对角矩阵会周期-2 振荡（w_i ∝ 1/(d_i w_i)），
        # 故用阻尼步（0.5 混合）保证单调收敛到风险平价解；仍不收敛则回退逆波动。
        for _ in range(RP_MAX_ITER):
            sw = cov @ w
            sw = np.where(sw <= 0, 1e-12, sw)
            target = 1.0 / sw
            target = target / float(target.sum())
            nw = 0.5 * w + 0.5 * target
            if np.max(np.abs(nw - w)) < RP_TOL:
                w = nw
                converged = True
                break
            w = nw
        if not converged:
            # 确定性回退：逆波动权重（status=fallback，明示非风险平价）。
            fallback = inverse_vol_weights(cov_daily, bounds=bounds)
            return {
                "weights": fallback["weights"],
                "status": "fallback",
                "method": "inverse_vol",
                "warning": "risk parity fixed-point did not converge; fallback to inverse-vol weights (reference)",
            }
        w = _project_to_bounds(w, lo, hi)
        return {
            "weights": _weights_dict(assets, w),
            "status": "ok",
            "method": "fixed_point",
        }

    # inverse_vol fallback（显式 method="inverse_vol" 或不可达分支）。
    fallback = inverse_vol_weights(cov_daily, bounds=bounds)
    return {
        "weights": fallback["weights"],
        "status": "fallback",
        "method": "inverse_vol",
        "warning": "risk parity fixed-point did not converge; fallback to inverse-vol weights",
    }


def efficient_frontier(
    mu: dict[str, float],
    cov_daily: pd.DataFrame,
    *,
    bounds: dict[str, tuple[float, float]] | None = None,
    lambdas: tuple[float, float] = FRONTIER_LAMBDAS,
    points: int = FRONTIER_POINTS,
) -> dict[str, Any]:
    """有效前沿：扫 ``λ`` 最大化 ``w'mu - λ w'Σw``。

    无 scipy 时诚实省略+警告，不伪造前沿点；
    三键点（风险平价/逆波动/等权）由比例建议分脚选出。
    """
    assets = list(cov_daily.columns)
    if not assets:
        return {"status": "degraded", "points": [], "warning": "no assets"}
    if not SCIPY_AVAILABLE:
        return {
            "status": "degraded",
            "points": [],
            "warning": "scipy unavailable; efficient frontier honestly omitted (no fabricated points); "
                       "proportion suggestions only risk_parity/inverse_vol/equal_weight anchors",
        }
    try:
        import scipy.optimize as opt
    except Exception as exc:  # pragma: no cover
        return {
            "status": "degraded",
            "points": [],
            "warning": f"scipy import failed: {exc}; efficient frontier omitted",
        }
    n = len(assets)
    lo, hi = _bounds_arrays(assets, bounds)
    mu_vec = np.array([mu.get(a, 0.0) for a in assets], dtype=float)
    cov = cov_daily.to_numpy(dtype=float)
    bnds = [(float(lo[i]), float(hi[i])) for i in range(n)]
    constraints = [{"type": "eq", "fun": lambda w: float(np.sum(w)) - 1.0}]
    w0 = np.full(n, 1.0 / n)
    points_list: list[dict[str, Any]] = []
    for lam in np.linspace(lambdas[0], lambdas[1], points):
        def objective(w: np.ndarray, lam: float = lam) -> float:
            return -float(w @ mu_vec - lam * (w @ cov @ w))

        res = opt.minimize(
            objective, w0, method="SLSQP",
            bounds=bnds, constraints=constraints,
            options={"maxiter": 500, "ftol": 1e-12},
        )
        if not res.success:
            continue
        w = _project_to_bounds(res.x, lo, hi)
        var = float(w @ cov @ w)
        ret = float(w @ mu_vec)
        points_list.append({
            "lambda": float(lam),
            "weights": _weights_dict(assets, w),
            "return_annual": ret * TRADING_DAYS,
            "vol_annual": math.sqrt(max(var, 0.0)) * math.sqrt(TRADING_DAYS),
            "sharpe": (ret * TRADING_DAYS) / (math.sqrt(max(var, 0.0)) * math.sqrt(TRADING_DAYS))
            if var > 0 else None,
        })
    if not points_list:
        return {
            "status": "degraded",
            "points": [],
            "warning": "efficient frontier SLSQP did not converge; no frontier points (not fabricated)",
        }
    return {"status": "ok", "points": points_list, "warning": None}


# ---------------------------------------------------------------------------
# 策略契约 → 组合分析输入（复用 backtest_engine 的契约结构）
# ---------------------------------------------------------------------------

def strategy_analysis_inputs(strategy: Any) -> dict[str, Any]:
    """从 ``ContractStrategy`` 提取组合分析输入（权重 + bounds + 来源）。

    - ``barbell`` / ``mid_line``：权重 = 契约 ``target_weight``
      （``target_weight_configured=True`` 且 >0 才计入；否则不虚构）。
    - ``grid``：权重 = ``grid_target_weight(asset) = max_weight * 0.5``（用户裁定）。
    - ``short_term``：无启用资产 → ``{"error": ...}``。
    - bounds 一律取契约 ``min_weight`` / ``max_weight``。

    返回：
        {"weights": {asset_id: float}, "bounds": {asset_id: (min, max)},
         "source": "contract_target" | "grid_target" | ...}
    """
    from qteasy_research.reference.backtest_engine import ContractStrategy, grid_target_weight

    if not isinstance(strategy, ContractStrategy):
        raise TypeError("strategy 必须是 ContractStrategy")
    enabled = strategy.enabled_assets
    if not enabled:
        return {
            "weights": {}, "bounds": {}, "source": "none",
            "error": "strategy has no enabled assets (short_term placeholder)",
        }
    asset_map = {asset.asset_id: asset for asset in strategy.assets}
    weights: dict[str, float] = {}
    bounds: dict[str, tuple[float, float]] = {}
    source = "contract_target"
    for asset_id in enabled:
        spec = asset_map.get(asset_id)
        if spec is None:
            continue
        if strategy.decision_rule == "grid":
            weight = grid_target_weight(spec)
            source = "grid_target"
        else:
            if spec.target_weight is None or spec.target_weight <= 0:
                continue  # 不虚构
            weight = float(spec.target_weight)
        weights[asset_id] = weight
        bounds[asset_id] = (float(spec.min_weight), float(spec.max_weight))
    if not weights:
        return {
            "weights": {}, "bounds": {}, "source": "none",
            "error": "no positive target_weight / grid target among enabled assets",
        }
    return {"weights": weights, "bounds": bounds, "source": source, "error": None}


# ---------------------------------------------------------------------------
# 组合分析主入口
# ---------------------------------------------------------------------------

def analyze_portfolio(
    weights: dict[str, float],
    close_panel: pd.DataFrame,
    factor_panels: dict[str, pd.DataFrame],
    *,
    macro_frames: dict[str, pd.DataFrame] | None = None,
    include_macro: bool = False,
    window: int = EXPOSURE_WINDOW,
    min_window: int = EXPOSURE_MIN_WINDOW,
    bounds: dict[str, tuple[float, float]] | None = None,
    correlation_min_overlap: int = CORRELATION_MIN_OVERLAP,
    frontier_lambdas: tuple[float, float] = FRONTIER_LAMBDAS,
    frontier_points: int = FRONTIER_POINTS,
    idio_var_floor: float = IDIO_VAR_FLOOR,
    run_id: str | None = None,
) -> dict[str, Any]:
    """L3 组合分析主入口。

    参数：
        weights: {asset_id: weight}（可未归一化）。
        close_panel: date x asset close 面板。
        factor_panels: {factor_id: date x asset factor 值}。
        macro_frames: {series_id: DataFrame(date/value)}；include_macro 时启用。
        bounds: {asset_id: (min_weight, max_weight)}；None → 默认 (0, 1)。
    返回：完整分析结果 dict（含 X / Σf / Σε / 组合指标 /
    RC / L4 暴露 / 比例建议）。
    """
    if not weights:
        raise ValueError("weights 不能为空")
    # 归一化权重，剔除 0 权重资产。
    total = sum(float(v) for v in weights.values() if _finite(v) is not None)
    if total <= 0:
        raise ValueError("weights 全部为 0 或不可用")
    w_norm = {k: float(v) / total for k, v in weights.items() if _finite(v) is not None}
    assets = [a for a in close_panel.columns if w_norm.get(a, 0.0) > 0]
    if not assets:
        raise ValueError("close_panel 中无权重资产的行情")

    universe_assets = [a for a in close_panel.columns]
    warnings: list[str] = []

    # 1) X 暴露矩阵（全资产×全因子；含宏观因子面板广播）。
    panels = {k: v for k, v in factor_panels.items() if v is not None and not v.empty}
    if include_macro and macro_frames:
        macro_returns = build_macro_factor_returns(macro_frames, close_panel.index)
        for series_id in macro_returns.columns:
            if series_id in panels:
                continue
            broadcast = pd.DataFrame(
                {asset: macro_returns[series_id] for asset in universe_assets},
                index=close_panel.index,
            )
            panels[f"Δ{series_id}"] = broadcast
    X, x_report = assemble_exposure_matrix(
        close_panel, panels, window=window, min_window=min_window
    )
    warnings.extend(x_report["warnings"])

    # 掉出 X 的资产：从组合分析中剔除并归一化剩余权重（不虚构，明示）。
    kept_assets = [a for a in assets if a in X.index]
    dropped_assets = [a for a in assets if a not in X.index]
    if dropped_assets:
        warnings.append(
            f"assets {dropped_assets} dropped for insufficient exposure; "
            "weights renormalized on remaining assets (not fabricated)"
        )
    if not kept_assets:
        raise ValueError("无可用资产暴露，无法进行组合分析（全部 quality_level=D）")
    w_kept = {a: w_norm[a] for a in kept_assets}
    w_total = sum(w_kept.values())
    w_kept = {a: v / w_total for a, v in w_kept.items()}
    kept_close = close_panel[kept_assets]

    # 2) 因子收益面板（价格多空 + 宏观月差，在全资产截面上构造）→ Σf。
    price_factor_ids = [f for f in panels if f in X.columns]
    price_panels = {f: panels[f] for f in price_factor_ids}
    price_returns = build_price_factor_returns(close_panel, price_panels)
    macro_ret: pd.DataFrame | None = None
    if include_macro and macro_frames:
        macro_ret = build_macro_factor_returns(macro_frames, close_panel.index)
        macro_ret = macro_ret.rename(columns={c: f"Δ{c}" for c in macro_ret.columns})
    if macro_ret is not None and not macro_ret.empty:
        factor_returns = pd.concat([price_returns, macro_ret], axis=1)
    else:
        factor_returns = price_returns
    factor_returns = factor_returns[[c for c in factor_returns.columns if c in X.columns]]

    if factor_returns.shape[1] == 0 or factor_returns.dropna().shape[0] < 2:
        warnings.append("factor return panel empty; Sigma_f cannot be estimated (not fabricated)")
        factor_covs = factor_covariance(pd.DataFrame())
    else:
        factor_covs = factor_covariance(factor_returns)
    warnings.extend(factor_covs.get("warnings", []))

    # 3) Σε 特异方差（带 floor）。
    idio = idiosyncratic_variance(
        close_panel, X, factor_returns, floor=idio_var_floor
    )
    idio_kept = idio.reindex(kept_assets)

    # 4) 组合风险收益（三口径）。
    daily_ret = kept_close.pct_change()
    ann_return = portfolio_return(w_kept, kept_close)
    mdd = max_drawdown(w_kept, kept_close)

    cov_factor_daily = pd.DataFrame()
    vol_factor = vol_sample = vol_ewma = None
    if not factor_covs["ledoit_wolf"].empty and not idio_kept.empty:
        cov_factor_daily = factor_model_cov(
            X.loc[kept_assets], factor_covs["ledoit_wolf"], idio_kept
        )
        vol_factor = portfolio_vol(w_kept, cov_factor_daily)
    asset_sample_cov = daily_ret.cov()
    if not asset_sample_cov.empty:
        vol_sample = portfolio_vol(w_kept, asset_sample_cov)
    asset_ewma_cov = _ewma_cov(daily_ret.dropna(), EWMA_LAMBDA)
    if not asset_ewma_cov.empty and asset_ewma_cov.shape[0] == len(kept_assets):
        vol_ewma = portfolio_vol(w_kept, asset_ewma_cov)

    vol_consistency_warnings: list[str] = []
    if vol_factor is not None and vol_sample is not None and vol_factor > 0:
        diff = abs(vol_factor - vol_sample) / vol_factor
        if diff > 0.20:
            vol_consistency_warnings.append(
                f"factor model vol {vol_factor:.4f} vs sample vol {vol_sample:.4f} "
                f"deviates {diff:.1%} (>20%); please check X/Sigma_f inputs"
            )
    if vol_factor is not None and vol_ewma is not None and vol_factor > 0:
        diff = abs(vol_factor - vol_ewma) / vol_factor
        if diff > 0.20:
            vol_consistency_warnings.append(
                f"factor model vol {vol_factor:.4f} vs EWMA vol {vol_ewma:.4f} "
                f"deviates {diff:.1%} (>20%); please check"
            )
    warnings.extend(vol_consistency_warnings)

    sharpe = (ann_return / vol_factor) if (vol_factor is not None and vol_factor > 0) else None

    # 5) 相关矩阵。
    corr = correlation_matrix(kept_close, min_overlap=correlation_min_overlap)

    # 6) RC（资产级 + 因子级）。
    asset_rc = pd.DataFrame()
    if not cov_factor_daily.empty:
        asset_rc = asset_risk_contributions(w_kept, cov_factor_daily)
    factor_rc = pd.DataFrame()
    if not factor_covs["ledoit_wolf"].empty and not idio_kept.empty:
        factor_rc = factor_risk_contributions(
            w_kept, X, factor_covs["ledoit_wolf"], idio_kept
        )

    # 7) L4 组合因子暴露 g = w'X。
    exposure = portfolio_factor_exposure(w_kept, X)

    # 8) 比例建议（风险平价 / 逆波动 / 等权 + 有效前沿）。
    bounds_kept = {a: bounds.get(a, (0.0, 1.0)) for a in kept_assets} if bounds else None
    rp = risk_parity_weights(cov_factor_daily, bounds=bounds_kept) if not cov_factor_daily.empty else {
        "weights": {a: 0.0 for a in kept_assets}, "status": "error", "method": "none"
    }
    iv = inverse_vol_weights(cov_factor_daily, bounds=bounds_kept) if not cov_factor_daily.empty else {
        "weights": {a: 0.0 for a in kept_assets}, "status": "error", "method": "none"
    }
    ew = equal_weight_weights(kept_assets, bounds=bounds_kept)
    mu = {a: float(daily_ret[a].mean()) * TRADING_DAYS for a in kept_assets}
    frontier = efficient_frontier(
        mu, cov_factor_daily,
        bounds=bounds_kept,
        lambdas=frontier_lambdas,
        points=frontier_points,
    ) if not cov_factor_daily.empty else {
        "status": "degraded", "points": [], "warning": "Sigma unavailable; efficient frontier omitted"
    }
    if frontier.get("status") == "degraded" and frontier.get("warning"):
        warnings.append(frontier["warning"])
    if rp.get("status") == "fallback":
        warnings.append("risk parity fixed-point did not converge; fallback to inverse-vol weights (reference)")

    return {
        "schema": PORTFOLIO_ANALYSIS_SCHEMA,
        "approval_policy": APPROVAL_POLICY,
        "run_id": run_id,
        "generated_at": today_iso(),
        "assets": kept_assets,
        "all_input_assets": assets,
        "universe_assets": universe_assets,
        "dropped_assets": x_report["dropped_assets"],
        "dropped_factors": x_report["dropped_factors"],
        "weights": w_kept,
        "quality_levels": x_report["quality_levels"],
        "window": int(window),
        "factors": [f for f in X.columns],
        "x_matrix": X,
        "factor_cov": factor_covs,
        "idiosyncratic_variance": idio,
        "cov_factor_daily": cov_factor_daily,
        "portfolio": {
            "annual_return": ann_return,
            "annual_vol_factor_model": vol_factor,
            "annual_vol_sample": vol_sample,
            "annual_vol_ewma": vol_ewma,
            "max_drawdown": mdd,
            "sharpe": sharpe,
            "vol_consistency_warnings": vol_consistency_warnings,
        },
        "correlation_matrix": corr,
        "asset_risk_contributions": asset_rc,
        "factor_risk_contributions": factor_rc,
        "factor_exposure": exposure,
        "proportions": {
            "risk_parity": rp,
            "inverse_vol": iv,
            "equal_weight": ew,
            "efficient_frontier": frontier,
        },
        "scipy_available": SCIPY_AVAILABLE,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# 输出：JSON / CSV / ASCII summary
# ---------------------------------------------------------------------------

def _clean_frame_for_json(frame: pd.DataFrame) -> Any:
    """DataFrame → 嵌套 list（NaN → None），供 JSON 序列化。"""
    if frame is None or frame.empty:
        return []
    cleaned = frame.copy()
    cleaned = cleaned.where(pd.notna(cleaned), None)
    return cleaned.to_dict(orient="index") if cleaned.index.name else cleaned.to_dict(orient="list")


def result_to_dict(result: dict[str, Any]) -> dict[str, Any]:
    """把分析结果转成 JSON 可序列化 dict（DataFrame → dict/None）。"""
    fc = result.get("factor_cov", {})
    factor_cov_out: dict[str, Any] = {
        "method": fc.get("method", "ledoit_wolf"),
        "shrinkage": fc.get("shrinkage", 0.0),
        "observations": fc.get("observations", 0),
        "ledoit_wolf": _clean_frame_for_json(fc.get("ledoit_wolf", pd.DataFrame())),
        "sample": _clean_frame_for_json(fc.get("sample", pd.DataFrame())),
        "ewma": _clean_frame_for_json(fc.get("ewma", pd.DataFrame())),
    }
    return {
        "schema": result["schema"],
        "approval_policy": result["approval_policy"],
        "run_id": result.get("run_id"),
        "generated_at": result["generated_at"],
        "assets": result["assets"],
        "all_input_assets": result["all_input_assets"],
        "dropped_assets": result["dropped_assets"],
        "dropped_factors": result["dropped_factors"],
        "weights": {k: (None if v is None or (isinstance(v, float) and not math.isfinite(v)) else v) for k, v in result["weights"].items()},
        "quality_levels": result["quality_levels"],
        "window": result["window"],
        "factors": result["factors"],
        "exposure_matrix": _clean_frame_for_json(result["x_matrix"]),
        "factor_cov": factor_cov_out,
        "idiosyncratic_variance": _clean_frame_for_json(result["idiosyncratic_variance"].reset_index()),
        "portfolio": {
            k: (None if (isinstance(v, float) and not math.isfinite(v)) else v)
            for k, v in result["portfolio"].items()
        },
        "correlation_matrix": _clean_frame_for_json(result["correlation_matrix"].reset_index()),
        "asset_risk_contributions": _clean_frame_for_json(result["asset_risk_contributions"]),
        "factor_risk_contributions": _clean_frame_for_json(result["factor_risk_contributions"]),
        "factor_exposure": _clean_frame_for_json(result["factor_exposure"].reset_index()),
        "proportions": _serialize_proportions(result["proportions"]),
        "scipy_available": result["scipy_available"],
        "warnings": result["warnings"],
    }


def _serialize_proportions(proportions: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("risk_parity", "inverse_vol", "equal_weight"):
        item = proportions.get(key, {})
        out[key] = {
            "status": item.get("status"),
            "method": item.get("method"),
            "weights": {k: v for k, v in item.get("weights", {}).items()},
            "warning": item.get("warning"),
            "objective": item.get("objective"),
        }
    frontier = proportions.get("efficient_frontier", {})
    out["efficient_frontier"] = {
        "status": frontier.get("status"),
        "warning": frontier.get("warning"),
        "points": [
            {**p, "weights": {k: v for k, v in p["weights"].items()}}
            for p in frontier.get("points", [])
        ],
    }
    return out


def write_outputs(result: dict[str, Any], out_dir: str | Path) -> dict[str, Path]:
    """写全部输出文件到 ``reports/portfolio_analysis/{run_id}/``。

    文件：summary.md（ASCII）、portfolio_analysis.json、exposure_matrix.csv、
    factor_cov.csv、correlation_matrix.csv、risk_contributions.csv、
    risk_parity_weights.csv、efficient_frontier.csv。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    header = build_header(
        generated_date=result["generated_at"],
        data_asof=result["generated_at"],
        schema_version=PORTFOLIO_ANALYSIS_SCHEMA,
    )

    paths: dict[str, Path] = {}
    paths["summary.md"] = out_dir / "summary.md"
    paths["summary.md"].write_text(render_summary_md(result), encoding="ascii")

    payload = result_to_dict(result)
    paths["portfolio_analysis.json"] = out_dir / "portfolio_analysis.json"
    paths["portfolio_analysis.json"].write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    x = result["x_matrix"]
    if not x.empty:
        p = out_dir / "exposure_matrix.csv"
        embed_header_csv(p, header, x.reset_index())
        paths["exposure_matrix.csv"] = p

    fc = result["factor_cov"]["ledoit_wolf"]
    if not fc.empty:
        p = out_dir / "factor_cov.csv"
        embed_header_csv(p, header, fc.reset_index())
        paths["factor_cov.csv"] = p

    corr = result["correlation_matrix"]
    if not corr.empty:
        p = out_dir / "correlation_matrix.csv"
        embed_header_csv(p, header, corr.reset_index())
        paths["correlation_matrix.csv"] = p

    arc = result["asset_risk_contributions"]
    if not arc.empty:
        p = out_dir / "risk_contributions.csv"
        embed_header_csv(p, header, arc)
        paths["risk_contributions.csv"] = p

    rp = result["proportions"]["risk_parity"].get("weights", {})
    if rp:
        p = out_dir / "risk_parity_weights.csv"
        frame = pd.DataFrame([{"asset_id": k, "weight": v} for k, v in rp.items()])
        embed_header_csv(p, header, frame)
        paths["risk_parity_weights.csv"] = p

    frontier = result["proportions"]["efficient_frontier"]
    if frontier.get("points"):
        rows: list[dict[str, Any]] = []
        for point in frontier["points"]:
            row = {"lambda": point["lambda"], "return_annual": point["return_annual"],
                   "vol_annual": point["vol_annual"], "sharpe": point["sharpe"]}
            row.update(point["weights"])
            rows.append(row)
        p = out_dir / "efficient_frontier.csv"
        embed_header_csv(p, header, pd.DataFrame(rows))
        paths["efficient_frontier.csv"] = p

    return paths


def _fmt(value: float | None, digits: int = 4) -> str:
    return "NA" if value is None or not math.isfinite(float(value)) else f"{float(value):.{digits}f}"


def render_summary_md(result: dict[str, Any]) -> str:
    """ASCII markdown 汇总（机器产出全 ASCII，零中文策略名）。"""
    lines: list[str] = []
    lines.append("# Portfolio Analysis (L3)")
    lines.append(f"schema={result['schema']} approval_policy={result['approval_policy']}")
    lines.append(f"run_id={result.get('run_id') or 'n/a'} generated_at={result['generated_at']}")
    lines.append(f"scipy_available={result['scipy_available']} window={result['window']}")
    lines.append("")

    lines.append("## Weights")
    lines.append("| asset | weight | quality |")
    lines.append("|---|---|---|")
    for asset in result["assets"]:
        w = result["weights"].get(asset)
        q = result["quality_levels"].get(asset, "A")
        lines.append(f"| {asset} | {_fmt(w, 6)} | {q} |")
    if result["dropped_assets"]:
        lines.append("")
        lines.append("Dropped assets (quality_level=D, not fabricated):")
        for asset in result["dropped_assets"]:
            lines.append(f"- {asset}")
    if result["dropped_factors"]:
        lines.append("")
        lines.append("Dropped factors (valid assets < 3, not fabricated):")
        for factor in result["dropped_factors"]:
            lines.append(f"- {factor}")
    lines.append("")

    lines.append("## Portfolio Risk / Return")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    lines.append(f"| annual_return | {_fmt(result['portfolio']['annual_return'])} |")
    lines.append(f"| annual_vol_factor_model | {_fmt(result['portfolio']['annual_vol_factor_model'])} |")
    lines.append(f"| annual_vol_sample | {_fmt(result['portfolio']['annual_vol_sample'])} |")
    lines.append(f"| annual_vol_ewma | {_fmt(result['portfolio']['annual_vol_ewma'])} |")
    lines.append(f"| max_drawdown | {_fmt(result['portfolio']['max_drawdown'])} |")
    lines.append(f"| sharpe | {_fmt(result['portfolio']['sharpe'])} |")
    if result["portfolio"]["vol_consistency_warnings"]:
        lines.append("")
        lines.append("Vol consistency warnings:")
        for item in result["portfolio"]["vol_consistency_warnings"]:
            lines.append(f"- {item}")
    lines.append("")

    lines.append("## Factor Covariance (Sigma_f, Ledoit-Wolf)")
    lines.append(f"method={result['factor_cov']['method']} shrinkage={_fmt(result['factor_cov']['shrinkage'], 4)} "
                 f"observations={result['factor_cov']['observations']}")
    lines.append("")

    lines.append("## Factor Exposure (L4, g = w'X)")
    lines.append("| factor | exposure |")
    lines.append("|---|---|")
    for factor_id, row in result["factor_exposure"].iterrows():
        lines.append(f"| {factor_id} | {_fmt(row['exposure'])} |")
    lines.append("")

    lines.append("## Asset Risk Contributions")
    arc = result["asset_risk_contributions"]
    if not arc.empty:
        lines.append("| asset | weight | rc |")
        lines.append("|---|---|---|")
        for _, row in arc.iterrows():
            lines.append(f"| {row['asset']} | {_fmt(row['weight'], 6)} | {_fmt(row['rc'])} |")
    lines.append("")

    lines.append("## Factor Risk Contributions")
    frc = result["factor_risk_contributions"]
    if not frc.empty:
        lines.append("| factor | rc |")
        lines.append("|---|---|")
        for _, row in frc.iterrows():
            lines.append(f"| {row['factor']} | {_fmt(row['rc'])} |")
    lines.append("")

    lines.append("## Proportion Suggestions")
    for key, label in (
        ("risk_parity", "risk_parity"),
        ("inverse_vol", "inverse_vol"),
        ("equal_weight", "equal_weight"),
    ):
        item = result["proportions"].get(key, {})
        lines.append(f"- {label}: status={item.get('status')} method={item.get('method')}")
        weights = item.get("weights", {})
        weights_text = ", ".join(f"{a}={_fmt(w, 6)}" for a, w in weights.items())
        lines.append(f"    weights: {weights_text}")
        if item.get("warning"):
            lines.append(f"    warning: {item['warning']}")
    frontier = result["proportions"]["efficient_frontier"]
    lines.append(f"- efficient_frontier: status={frontier.get('status')} "
                 f"points={len(frontier.get('points', []))}")
    if frontier.get("warning"):
        lines.append(f"    warning: {frontier['warning']}")
    lines.append("")

    if result["warnings"]:
        lines.append("## Warnings")
        for item in result["warnings"]:
            lines.append(f"- {item}")
        lines.append("")

    lines.append("### Assumptions")
    lines.append("- factor model covariance: Sigma = B Sigma_f B' + diag(sigma2_eps), x252")
    lines.append("- Sigma_f: Ledoit-Wolf shrinkage (PSD); sample/EWMA(0.94) as robustness check")
    lines.append("- X: OLS rolling beta via factor_research.estimate_asset_factor_exposure, window 252 (min 120)")
    lines.append("- factor returns: price factors top5/bottom5 equal-weight weekly; macro factors monthly diff")
    lines.append("- all values are reference only (REFERENCE_ONLY); no auto approval")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 组合分析输出目录常量
# ---------------------------------------------------------------------------

PORTFOLIO_ANALYSIS_DIR = PROJECT_ROOT / "reports" / "portfolio_analysis"


def default_run_id() -> str:
    """默认 run_id：YYYYMMDD_HHMMSS。"""
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
