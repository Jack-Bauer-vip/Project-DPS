"""Point-in-time macro matching for a small set of global research ETFs.

This module intentionally does not import the A-share factor scorer.  It reads
only the normalized global macro/ETF snapshots and the global ETF tables in
ResearchStore.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qteasy_research.pretrade.storage import ResearchStore


def suggest_modifier_from_condition_returns(
    condition_return: float,
    baseline_return: float,
    sample_count: int,
) -> dict[str, Any]:
    """根据条件收益相对全期基准的偏离，给出建议宏观修正系数（仅供人工参考）。

    输入为月度收益率的百分点数（如 1.5 表示 1.5%）。偏差 = 条件收益 - 基准收益，
    阈值取 ±0.5 和 ±1.5 个百分点，对应五档离散 modifier：

    - 偏差 >= +1.5pp → 1.15（强烈利好）
    - 偏差 >= +0.5pp → 1.08（温和利好）
    - 偏差 >  -0.5pp → 1.00（中性）
    - 偏差 >  -1.5pp → 0.92（温和利空）
    - 偏差 <= -1.5pp → 0.85（强烈利空）

    样本联动：少于 24 个月仅作参考（modifier=None）；24~59 个月为候选
    （CANDIDATE，不能进入正式修正）；>=60 个月才允许 APPROVED。
    本函数只返回建议值，不自动写入规则表；生效的 modifier 只能来自
    数据库中人工确认的 APPROVED 规则。
    """
    if sample_count < 24:
        return {"modifier": None, "confidence": "insufficient_sample", "status": "REFERENCE_ONLY"}

    deviation = condition_return - baseline_return
    if deviation >= 1.5:
        modifier, confidence = 1.15, "high"
    elif deviation >= 0.5:
        modifier, confidence = 1.08, "medium"
    elif deviation > -0.5:
        modifier, confidence = 1.00, "low"
    elif deviation > -1.5:
        modifier, confidence = 0.92, "medium"
    else:
        modifier, confidence = 0.85, "high"
    if modifier == 1.00:
        status = "NEUTRAL"
    else:
        status = "APPROVED" if sample_count >= 60 else "CANDIDATE"
    return {"modifier": modifier, "confidence": confidence, "status": status}


@dataclass
class GlobalEtfScoreResult:
    target_date: str
    status: str = "PARTIAL"
    rate_proxy: str | None = None
    scores: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    output_csv: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_date": self.target_date,
            "status": self.status,
            "rate_proxy": self.rate_proxy,
            "scores": self.scores,
            "warnings": self.warnings,
            "output_csv": self.output_csv,
        }


def initialize_default_global_etf_profiles(
    store_root: str | Path,
    *,
    enabled: bool = False,
) -> list[dict[str, Any]]:
    """Register SPY/TLT/GLD as disabled-by-default research assets."""

    store = ResearchStore(store_root)
    definitions = [
        ("SPY", "SPDR S&P 500 ETF", "monthly"),
        ("TLT", "iShares 20+ Year Treasury Bond ETF", "daily"),
        ("GLD", "SPDR Gold Shares", "monthly"),
    ]
    result = []
    for asset_code, name, frequency in definitions:
        store.upsert_global_etf_definition({
            "asset_code": asset_code,
            "research_asset_code": asset_code,
            "name": name,
            "source": "YahooFinance",
        })
        result.append(store.upsert_global_etf_activation({
            "asset_code": asset_code,
            "horizon": "medium",
            "enabled": int(enabled),
            "frequency": frequency,
        }))
    return result


class GlobalEtfEngine:
    """Calculate macro-conditioned scores for research assets such as SPY/TLT/GLD."""

    ASSETS = ("SPY", "TLT", "GLD")
    MIN_RULE_SAMPLE = 24

    def __init__(self, data_root: str | Path, store_root: str | Path) -> None:
        self.data_root = Path(data_root)
        self.store = ResearchStore(store_root)
        # A global engine must never silently read the A-share Parquet directory.
        if (self.data_root / "factor_values").exists():
            raise RuntimeError(
                "GlobalEtfEngine 不得读取 A 股 factor_values 目录。"
                "请将 data_root 指向全球数据目录。"
            )
        self.macro_dir = self.data_root / "processed" / "global_macro"
        self.output_dir = self.data_root / "global_etf_values"

    def _load_series(self, series_id: str, target_date: pd.Timestamp) -> pd.DataFrame:
        path = self.macro_dir / f"{series_id}.csv"
        if not path.exists():
            return pd.DataFrame(columns=["observation_date", "available_at", "value", "quality_level"])
        frame = pd.read_csv(path)
        required = {"observation_date", "available_at", "value"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"{series_id}.csv 缺少字段：{sorted(missing)}")
        frame = frame.copy()
        frame["observation_date"] = pd.to_datetime(frame["observation_date"], errors="coerce")
        frame["available_at"] = pd.to_datetime(frame["available_at"], errors="coerce")
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        frame = frame.dropna(subset=["observation_date", "available_at", "value"])
        return frame.loc[
            (frame["observation_date"] <= target_date)
            & (frame["available_at"] <= target_date)
        ].sort_values("observation_date")

    @staticmethod
    def _month_end(frame: pd.DataFrame) -> pd.Series:
        if frame.empty:
            return pd.Series(dtype="float64")
        return frame.set_index("observation_date")["value"].resample("ME").last().dropna()

    def _macro_state(self, target_date: pd.Timestamp) -> tuple[list[str], str | None, list[str], dict[str, pd.DataFrame]]:
        series = {
            name: self._load_series(name, target_date)
            for name in ("DGS10", "DGS2", "DGS30", "DFII10")
        }
        warnings: list[str] = []
        dgs30 = self._month_end(series["DGS30"])
        dgs10 = self._month_end(series["DGS10"])
        rate = dgs30 if not dgs30.empty else dgs10
        rate_proxy = "DGS30" if not dgs30.empty else ("DGS10" if not dgs10.empty else None)
        if rate_proxy == "DGS10":
            warnings.append("DGS30 不可用，已降级使用 DGS10；TLT 利率匹配精度降低。")
        if rate_proxy is None:
            warnings.append("DGS30 和 DGS10 均不可用，无法识别利率状态。")
        real_rate = self._month_end(series["DFII10"])
        dgs2 = self._month_end(series["DGS2"])
        states: list[str] = []
        if len(rate) >= 2:
            change = float(rate.iloc[-1] - rate.iloc[-2])
            if change >= 0.20:
                states.append("rate_up")
            elif change <= -0.20:
                states.append("rate_down")
            else:
                states.append("rate_stable")
        if not dgs10.empty and not dgs2.empty:
            spread = self._month_end(series["DGS10"]) - dgs2
            spread = spread.dropna()
            if not spread.empty:
                states.append("curve_inverted" if float(spread.iloc[-1]) < 0 else "curve_normal")
        if len(real_rate) >= 2:
            real_change = float(real_rate.iloc[-1] - real_rate.iloc[-2])
            if real_change >= 0.10:
                states.append("real_yield_up")
            elif real_change <= -0.10:
                states.append("real_yield_down")
            else:
                states.append("real_yield_stable")
        return states, rate_proxy, warnings, series

    @staticmethod
    def _base_score(asset: str, prices: pd.DataFrame, rate_frame: pd.DataFrame) -> dict[str, Any]:
        if prices.empty:
            return {"base_score": None, "sample_count": 0, "frequency_used": "daily" if asset == "TLT" else "monthly"}
        prices = prices.sort_values("observation_date").drop_duplicates("observation_date")
        prices = prices.set_index("observation_date")["value"]
        daily_returns = prices.pct_change().dropna()
        if asset == "TLT":
            frequency = "daily"
            returns = daily_returns.tail(252)
            rate = rate_frame.set_index("observation_date")["value"].pct_change().rename("rate_change")
            common = pd.concat([daily_returns.rename("asset_return"), rate], axis=1).dropna().tail(252)
            beta = None
            r_squared = None
            if len(common) >= 20 and common["rate_change"].var(ddof=0) > 0:
                beta = float(common["asset_return"].cov(common["rate_change"]) / common["rate_change"].var(ddof=0))
                r_squared = float(common["asset_return"].corr(common["rate_change"]) ** 2)
        else:
            frequency = "monthly"
            monthly = prices.resample("ME").last().pct_change().dropna()
            returns = monthly.tail(12)
            beta = None
            r_squared = None
        if returns.empty:
            return {
                "base_score": None,
                "sample_count": 0,
                "frequency_used": frequency,
                "rate_beta": beta,
                "r_squared": r_squared,
            }
        volatility = float(returns.std(ddof=0))
        total_return = float((1.0 + returns).prod() - 1.0)
        base_score = total_return / volatility if volatility > 0 else None
        return {
            "base_score": float(base_score) if base_score is not None else None,
            "sample_count": int(len(returns)),
            "frequency_used": frequency,
            "rate_beta": beta,
            "r_squared": r_squared,
        }

    def calculate_scores(
        self,
        target_date: str,
        assets: list[str] | None = None,
        *,
        horizon: str = "medium",
        persist: bool = True,
    ) -> GlobalEtfScoreResult:
        cutoff = pd.Timestamp(target_date)
        requested = [str(asset).upper() for asset in (assets or list(self.ASSETS))]
        result = GlobalEtfScoreResult(target_date=cutoff.date().isoformat())
        states, rate_proxy, macro_warnings, macro_series = self._macro_state(cutoff)
        result.rate_proxy = rate_proxy
        result.warnings.extend(macro_warnings)
        profiles = {item["asset_code"]: item for item in self.store.list_global_etf_activations(horizon=horizon)}
        for asset in requested:
            profile = profiles.get(asset)
            row: dict[str, Any] = {
                "asset": asset,
                "research_asset": asset,
                "data_as_of": None,
                "data_quality": "C",
                "macro_state": states,
                "rate_proxy": rate_proxy,
                "frequency_used": "daily" if asset == "TLT" else "monthly",
                "base_score": None,
                "macro_modifier": None,
                "final_score": None,
                "macro_support_factors": [],
                "macro_conflict_factors": [],
                "sample_count": 0,
                "rate_beta": None,
                "r_squared": None,
                "status": "PARTIAL",
                "warnings": [],
            }
            if profile is None or not int(profile.get("enabled", 0)) or profile.get("status") != "ENABLED":
                row["warnings"].append("未找到已启用的全球 ETF 研究配置。")
                result.scores.append(row)
                continue
            prices = self._load_series(asset, cutoff)
            if prices.empty:
                row["warnings"].append(f"未找到 {asset} 在目标日期以前的价格数据。")
                result.scores.append(row)
                continue
            row["data_as_of"] = prices["observation_date"].max().date().isoformat()
            row["data_quality"] = str(prices.get("quality_level", pd.Series(["C"])).iloc[-1])
            base = self._base_score(asset, prices, macro_series.get(rate_proxy or "DGS30", pd.DataFrame()))
            row.update(base)
            rules = self.store.get_global_etf_macro_rules(
                asset_code=asset, macro_states=states, target_date=target_date
            )
            rule_map = {rule["macro_state"]: rule for rule in rules}
            if not states:
                row["warnings"].append("宏观状态不可识别，不能生成宏观修正。")
            elif len(rule_map) != len(states):
                missing = [state for state in states if state not in rule_map]
                row["warnings"].append(f"缺少 APPROVED 宏观规则：{', '.join(missing)}。")
            elif any((rule.get("sample_count") or 0) < self.MIN_RULE_SAMPLE for rule in rules):
                row["warnings"].append("宏观规则样本少于 24 个月，仅作参考，不能进入正式修正。")
            else:
                modifiers = [float(rule["modifier"]) for rule in rules]
                row["macro_modifier"] = float(np.prod(modifiers))
                row["macro_support_factors"] = [state for state, rule in rule_map.items() if float(rule["modifier"]) > 1]
                row["macro_conflict_factors"] = [state for state, rule in rule_map.items() if float(rule["modifier"]) < 1]
                if row["base_score"] is not None:
                    row["final_score"] = float(row["base_score"] * row["macro_modifier"])
                    row["status"] = "COMPLETED"
            result.scores.append(row)
            if persist:
                self.store.save_global_etf_score_snapshot({
                    "target_date": target_date,
                    "asset_code": asset,
                    "payload": row,
                })
        result.status = "COMPLETED" if result.scores and all(row["status"] == "COMPLETED" for row in result.scores) else "PARTIAL"
        if persist:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            output = pd.DataFrame(result.scores)
            result.output_csv = str(self.output_dir / f"global_etf_scores_{cutoff.date().isoformat()}.csv")
            output.to_csv(result.output_csv, index=False, encoding="utf-8-sig")
            (self.output_dir / f"global_etf_scores_{cutoff.date().isoformat()}.json").write_text(
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
        return result
