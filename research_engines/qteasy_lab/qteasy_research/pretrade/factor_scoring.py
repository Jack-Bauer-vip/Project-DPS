"""Production factor scoring from SQLite configuration and Parquet snapshots."""

from __future__ import annotations

import json
import hashlib
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qteasy_research.pretrade.schemas import FactorScoreResult
from qteasy_research.pretrade.storage import ResearchStore

logger = logging.getLogger(__name__)


def _diagnostic(
    code: str,
    *,
    title: str,
    detail: str,
    solution: str,
    factor_id: str | None = None,
    asset_code: str | None = None,
    severity: str = "error",
    actions: list[str] | None = None,
) -> dict[str, Any]:
    """Build a UI-safe, structured explanation for an unavailable result."""

    return {
        "code": code,
        "severity": severity,
        "factor_id": factor_id,
        "asset_code": asset_code,
        "title": title,
        "detail": detail,
        "solution": solution,
        "actions": list(actions or []),
    }


def _add_diagnostic(
    result: FactorScoreResult,
    item: dict[str, Any],
) -> None:
    result.diagnostics.append(item)
    asset_code = item.get("asset_code")
    if asset_code:
        result.asset_diagnostics.setdefault(str(asset_code), []).append(item)


def _looks_like_etf(code: str) -> bool:
    value = str(code).upper().replace(".SH", "").replace(".SZ", "")
    return value.isdigit() and len(value) == 6 and value[:2] in {"15", "51", "56", "58"}


def _classify_missing_value(path: Path, query_code: str, target_date: str) -> str:
    """Explain why a code did not produce a point-in-time factor value."""

    frame = pd.read_parquet(path, columns=["date", "asset_code", "value", "available_at"])
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["available_at"] = pd.to_datetime(frame["available_at"], errors="coerce")
    code_rows = frame.loc[frame["asset_code"].astype(str) == str(query_code)]
    if code_rows.empty:
        return "ASSET_NOT_IN_FACTOR_DATA"
    cutoff = pd.Timestamp(target_date)
    eligible = code_rows.loc[
        (code_rows["date"] <= cutoff)
        & (code_rows["available_at"].isna() | (code_rows["available_at"] <= cutoff))
    ]
    if eligible.empty:
        return "NO_POINT_IN_TIME_DATA"
    if pd.to_numeric(eligible["value"], errors="coerce").dropna().empty:
        return "ALL_FACTOR_VALUES_MISSING"
    return "ASSET_NOT_IN_FACTOR_DATA"


def formula_md5(formula: str) -> str:
    """Return a stable hash of the formula string, including whitespace."""

    return hashlib.md5(formula.encode("utf-8")).hexdigest()


def _factor_values_dir(root: Path) -> Path:
    preferred = root / "data" / "factor_values"
    return preferred if preferred.exists() else root / "factor_values"


def _load_manifest(path: Path) -> dict[str, Any]:
    manifest_path = path.with_suffix(".manifest.json")
    if not manifest_path.exists():
        raise FileNotFoundError(f"因子 Manifest 不存在：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("value_semantics") not in {"neutralized_exposure", "asset_exposure"}:
        raise ValueError(f"因子值语义不受支持：{path.name}")
    return manifest


def _latest_factor_values(
    path: Path,
    *,
    query_codes: set[str],
    target_date: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    manifest = _load_manifest(path)
    frame = pd.read_parquet(path)
    required = {"date", "asset_code", "value", "available_at"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"因子 Parquet 缺少字段：{sorted(missing)}")
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["available_at"] = pd.to_datetime(frame["available_at"], errors="coerce")
    cutoff = pd.Timestamp(target_date)
    frame = frame.loc[
        (frame["date"] <= cutoff)
        & (frame["available_at"].isna() | (frame["available_at"] <= cutoff))
        & frame["asset_code"].astype(str).isin(query_codes)
    ].copy()
    frame = frame.sort_values(["asset_code", "date", "available_at"])
    frame = frame.drop_duplicates("asset_code", keep="last")
    return frame, manifest


def _as_universe(universe: list[str] | pd.DataFrame) -> pd.DataFrame:
    if isinstance(universe, pd.DataFrame):
        if "asset_code" not in universe.columns:
            raise ValueError("universe DataFrame 必须包含 asset_code")
        return universe.copy()
    return pd.DataFrame({"asset_code": [str(item) for item in universe]})


def calculate_factor_scores(
    target_date: str,
    asset_type: str,
    horizon: str,
    *,
    universe: list[str] | pd.DataFrame,
    store_root: str | Path,
    macro_regime: str | None = None,
) -> FactorScoreResult:
    """Calculate a point-in-time cross-sectional factor score snapshot."""

    root = Path(store_root)
    store = ResearchStore(root)
    assets = _as_universe(universe)
    assets["asset_code"] = assets["asset_code"].astype(str)
    result = FactorScoreResult(target_date, asset_type.upper(), horizon)
    scores = assets[["asset_code"]].copy()
    factor_dir = _factor_values_dir(root)
    all_profiles = store.list_factor_activations(
        asset_type=asset_type.upper(), horizon=horizon, enabled_only=False
    )
    profiles = store.list_factor_activations(
        asset_type=asset_type.upper(), horizon=horizon, enabled_only=True
    )
    if asset_type.upper() == "STOCK" and assets["asset_code"].map(_looks_like_etf).any():
        _add_diagnostic(
            result,
            _diagnostic(
                "ASSET_TYPE_MISMATCH",
                title="输入代码可能是 ETF，但当前选择为股票",
                detail=f"当前评分类型为 STOCK，输入资产为：{', '.join(assets['asset_code'].astype(str).tolist())}。",
                solution="请将当前评分资产类型切换为 ETF，并保存对应的 ETF + 投资期限因子配置。",
                severity="warning",
                actions=["switch_asset_type", "open_factor_config"],
            ),
        )
    if not profiles:
        suspended = [item for item in all_profiles if str(item.get("status")) == "SUSPENDED"]
        if suspended:
            for item in suspended:
                _add_diagnostic(
                    result,
                    _diagnostic(
                        "PROFILE_SUSPENDED",
                        factor_id=str(item.get("factor_id")),
                        title="因子配置已暂停",
                        detail=f"{item.get('factor_id')} 因硬止损或人工操作处于 SUSPENDED 状态。",
                        solution="请先人工复核因子回撤，再在因子配置中执行人工恢复。",
                        actions=["open_factor_config"],
                    ),
                )
        else:
            _add_diagnostic(
                result,
                _diagnostic(
                    "NO_ENABLED_PROFILE",
                    title="没有匹配的已启用因子配置",
                    detail=f"没有找到 {asset_type.upper()} + {horizon} 的已启用因子。",
                    solution="请检查资产类型和投资期限，并在因子研究页面勾选启用后保存应用配置。",
                    actions=["open_factor_config"],
                ),
            )
        result.warnings.append("没有找到已启用的因子配置")
        fallback_reason = result.diagnostics[0]["title"] if result.diagnostics else "没有可用因子配置"
        scores = scores.assign(
            factor_score=np.nan,
            composite_score=np.nan,
            availability_status="不可用",
            availability_reason=fallback_reason,
        )
        result.scores = scores.to_dict("records")
        return result

    contributions: dict[str, pd.Series] = {}
    availability: dict[str, pd.Series] = {}
    for profile in profiles:
        factor_id = str(profile["factor_id"])
        supported = profile.get("supported_asset_types") or []
        if supported and asset_type.upper() not in {str(item).upper() for item in supported}:
            result.warnings.append(f"{factor_id} 不支持资产类型 {asset_type}")
            continue
        factor_path = factor_dir / f"{factor_id}.parquet"
        if not factor_path.exists():
            _add_diagnostic(
                result,
                _diagnostic(
                    "FACTOR_FILE_MISSING",
                    factor_id=factor_id,
                    title=f"{factor_id} 因子数据文件不存在",
                    detail=f"未找到 {factor_path}，当前因子没有可供评分的 Parquet 数据。",
                    solution="请先在数据管理中导入/更新行情，再生成该因子数据。",
                    actions=["open_data", "prepare_factor", "recalculate"],
                ),
            )
            result.warnings.append(f"因子数据不存在：{factor_path}；请先在数据管理中更新行情并生成因子数据。")
            continue

        query_codes = set(assets["asset_code"])
        code_map: dict[str, str] = {}
        query_for_asset = {str(code): str(code) for code in assets["asset_code"]}
        if asset_type.upper() in {"ETF", "LOF", "QDII"} and profile.get("value_scope") == "underlying":
            for code in query_codes:
                mapping = store.get_etf_underlying(code, target_date)
                if mapping:
                    query_codes.discard(code)
                    query_codes.add(mapping["underlying_code"])
                    code_map[mapping["underlying_code"]] = code
                    query_for_asset[str(code)] = str(mapping["underlying_code"])
                else:
                    _add_diagnostic(
                        result,
                        _diagnostic(
                            "ETF_UNDERLYING_MAPPING_MISSING",
                            factor_id=factor_id,
                            asset_code=code,
                            title="ETF 缺少有效底层映射",
                            detail=f"{code} 的因子需要底层指数或持仓数据，但截至 {target_date} 没有有效映射。",
                            solution="请配置 ETF 底层映射，或改用直接基于 ETF 自身行情的因子。",
                            actions=["open_data", "open_factor_config"],
                        ),
                    )
                    result.warnings.append(f"{factor_id}: {code} 缺少有效 ETF 底层映射")

        try:
            _load_manifest(factor_path)
        except FileNotFoundError as exc:
            _add_diagnostic(
                result,
                _diagnostic(
                    "MANIFEST_MISSING_OR_INVALID",
                    factor_id=factor_id,
                    title="因子 Manifest 缺失",
                    detail=f"{factor_id} 的 Parquet 存在，但对应 Manifest 不存在：{exc}。",
                    solution="请在数据管理中重新生成该因子数据，不要手工修改 Manifest。",
                    actions=["open_data", "prepare_factor"],
                ),
            )
            continue
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _add_diagnostic(
                result,
                _diagnostic(
                    "MANIFEST_MISSING_OR_INVALID",
                    factor_id=factor_id,
                    title="因子 Manifest 无效",
                    detail=f"{factor_id} 的 Manifest 无法校验：{exc}。",
                    solution="请在数据管理中重新生成该因子数据，不要手工修改 Manifest。",
                    actions=["open_data", "prepare_factor"],
                ),
            )
            continue

        try:
            values, manifest = _latest_factor_values(
                factor_path, query_codes=query_codes, target_date=target_date
            )
        except FileNotFoundError as exc:
            _add_diagnostic(
                result,
                _diagnostic(
                    "MANIFEST_MISSING_OR_INVALID",
                    factor_id=factor_id,
                    title="因子 Manifest 缺失",
                    detail=f"{factor_id} 的 Parquet 存在，但对应 Manifest 不存在：{exc}。",
                    solution="请在数据管理中重新生成该因子数据，不要手工修改 Manifest。",
                    actions=["open_data", "prepare_factor"],
                ),
            )
            result.warnings.append(f"{factor_id} 无法读取：{exc}")
            continue
        except (OSError, ValueError, KeyError) as exc:
            _add_diagnostic(
                result,
                _diagnostic(
                    "FACTOR_FILE_READ_ERROR",
                    factor_id=factor_id,
                    title="因子数据文件无法读取",
                    detail=f"{factor_id} 的 Parquet 或 Manifest 读取失败：{exc}。",
                    solution="请在数据管理中重新生成因子数据，并检查 Parquet 字段和 Manifest。",
                    actions=["open_data", "prepare_factor"],
                ),
            )
            result.warnings.append(f"{factor_id} 无法读取：{exc}")
            continue

        if code_map:
            values["asset_code"] = values["asset_code"].map(code_map).fillna(values["asset_code"])
        values = values[["asset_code", "value", "date"]].rename(columns={"value": factor_id})
        merged = scores[["asset_code"]].merge(values, on="asset_code", how="left")
        raw = pd.to_numeric(merged[factor_id], errors="coerce")
        valid = raw.notna()
        if not valid.any():
            _add_diagnostic(
                result,
                _diagnostic(
                    "ALL_FACTOR_VALUES_MISSING",
                    factor_id=factor_id,
                    title=f"{factor_id} 当前没有有效因子值",
                    detail=f"目标日期 {target_date} 下，输入的 {len(assets)} 个资产均未得到可用值。",
                    solution="请检查目标日期、数据覆盖范围和资产代码；必要时先更新行情并重新生成因子数据。",
                    actions=["open_data", "prepare_factor", "recalculate"],
                ),
            )
        for row_index, asset_code in enumerate(assets["asset_code"].astype(str).tolist()):
            if valid.iloc[row_index]:
                continue
            try:
                missing_code = _classify_missing_value(
                    factor_path, query_for_asset.get(asset_code, asset_code), target_date
                )
            except Exception:
                missing_code = "FACTOR_FILE_READ_ERROR"
            titles = {
                "ASSET_NOT_IN_FACTOR_DATA": "资产不在该因子数据中",
                "NO_POINT_IN_TIME_DATA": "目标日期以前没有可用因子数据",
                "ALL_FACTOR_VALUES_MISSING": "该资产当前没有有效因子值",
                "FACTOR_FILE_READ_ERROR": "无法检查该资产的因子数据",
            }
            solutions = {
                "ASSET_NOT_IN_FACTOR_DATA": "请检查代码格式和资产类型，并重新导入该资产行情后生成因子数据。",
                "NO_POINT_IN_TIME_DATA": "请选择不早于因子数据截至日期的目标日期，或先更新行情。",
                "ALL_FACTOR_VALUES_MISSING": "请检查行情是否有缺失收盘价，并重新生成因子数据。",
                "FACTOR_FILE_READ_ERROR": "请在数据管理中重新生成因子数据。",
            }
            _add_diagnostic(
                result,
                _diagnostic(
                    missing_code,
                    factor_id=factor_id,
                    asset_code=asset_code,
                    title=titles[missing_code],
                    detail=f"{asset_code} 没有可用于 {target_date} 评分的 {factor_id} 值。",
                    solution=solutions[missing_code],
                    actions=["open_data", "prepare_factor", "recalculate"],
                ),
            )
        clean_policy = str(profile.get("missing_policy") or "exclude").lower()
        if clean_policy not in {"exclude", "neutral"}:
            logger.warning("生产端不支持复杂缺失填充，已降级为排除处理。factor=%s policy=%s", factor_id, clean_policy)
            result.warnings.append(f"{factor_id}: 生产端不支持复杂缺失填充，已降级为排除处理。")
            clean_policy = "exclude"

        if valid.any():
            mean = raw.loc[valid].mean()
            std = raw.loc[valid].std(ddof=0)
            z = (raw - mean) / std if std and np.isfinite(std) else raw * 0.0
            z = z.where(valid)
        else:
            z = pd.Series(np.nan, index=raw.index)
        direction = int(profile.get("direction", 1) or 1)
        modifier = store.get_macro_modifier(
            factor_id=factor_id,
            category=str(profile.get("category") or ""),
            target_date=target_date,
            regime=macro_regime,
        )
        weight = float(profile.get("weight", 1.0) or 0.0)
        contribution = z.fillna(0.0) * direction * weight * modifier
        included = valid if clean_policy == "exclude" else pd.Series(True, index=raw.index)
        contributions[factor_id] = contribution
        availability[factor_id] = included
        scores[f"score_{factor_id}"] = contribution
        result.factor_details[factor_id] = {
            "manifest": manifest,
            "direction": direction,
            "weight": weight,
            "modifier": modifier,
            "missing_policy": clean_policy,
            "available_count": int(valid.sum()),
            "as_of_date": values["date"].max().date().isoformat() if not values.empty else None,
        }
        history = pd.read_parquet(factor_path)
        history["date"] = pd.to_datetime(history["date"], errors="coerce")
        history["available_at"] = pd.to_datetime(history["available_at"], errors="coerce")
        cutoff = pd.Timestamp(target_date)
        history = history.loc[
            (history["date"] <= cutoff)
            & (history["available_at"].isna() | (history["available_at"] <= cutoff))
        ]
        history_values = pd.to_numeric(history["value"], errors="coerce").dropna()
        if not history_values.empty and valid.any():
            threshold = float(history_values.quantile(0.95))
            crowding_ratio = float((raw.loc[valid] >= threshold).mean())
            result.crowding[factor_id] = {
                "threshold": threshold,
                "ratio": crowding_ratio,
                "warning": crowding_ratio > 0.30,
            }
            result.factor_details[factor_id]["crowding"] = result.crowding[factor_id]
            if crowding_ratio > 0.30:
                result.warnings.append(f"{factor_id}: 当前因子值处于历史95%分位数的资产比例为 {crowding_ratio:.1%}，存在拥挤风险。")
        result.macro_modifiers[factor_id] = modifier

    if contributions:
        weighted = pd.concat(contributions, axis=1)
        included_frame = pd.concat(availability, axis=1)
        effective_weight_series = pd.Series({
            key: float(result.factor_details[key]["weight"])
            * float(result.factor_details[key].get("modifier", 1.0))
            for key in included_frame.columns
        })
        denominator = included_frame.astype(float).mul(
            effective_weight_series.abs(),
            axis="columns",
        ).sum(axis=1)
        scores["composite_score"] = weighted.sum(axis=1).div(denominator.where(denominator > 0))
        scores["factor_score"] = scores["composite_score"]
        scores["as_of_date"] = target_date
        result.effective_weights = {
            key: float(effective_weight_series[key])
            for key in contributions
        }
        normalized = included_frame.astype(float).mul(effective_weight_series, axis="columns")
        normalized = normalized.div(denominator.where(denominator > 0), axis="index")
        result.normalized_weights = {}
        for row_index, (_, row) in enumerate(normalized.iterrows()):
            row_denominator = denominator.iloc[row_index]
            if pd.isna(row_denominator) or float(row_denominator) <= 0:
                continue
            result.normalized_weights[str(scores.iloc[row_index]["asset_code"])] = {
                factor_id: float(value)
                for factor_id, value in row.items()
                if pd.notna(value) and abs(float(value)) > 0
            }
    else:
        scores["factor_score"] = np.nan
        scores["composite_score"] = np.nan
        scores["as_of_date"] = target_date

    def _asset_reason(asset_code: str) -> str:
        items = result.asset_diagnostics.get(str(asset_code), [])
        if items:
            return str(items[0].get("title") or items[0].get("code"))
        if pd.isna(scores.loc[scores["asset_code"] == asset_code, "composite_score"]).all():
            return str(result.diagnostics[0].get("title")) if result.diagnostics else "没有可用因子值"
        return ""

    scores["availability_status"] = scores["composite_score"].map(
        lambda value: "可用" if pd.notna(value) else "不可用"
    )
    scores["availability_reason"] = scores["asset_code"].map(_asset_reason)

    result.coverage = {
        "asset_count": int(len(scores)),
        "factor_count": len(contributions),
        "available_asset_count": int(scores["composite_score"].notna().sum()),
        "unavailable_asset_count": int(scores["composite_score"].isna().sum()),
        "factor_coverage": {
            factor_id: int(availability[factor_id].sum()) for factor_id in availability
        },
    }
    result.scores = scores.to_dict("records")
    output_dir = root / "outputs" / "factor_scores"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"{asset_type.lower()}_{horizon}_{target_date}.csv"
    scores.to_csv(csv_path, index=False, encoding="utf-8-sig")
    result.csv_path = str(csv_path)
    return result


def monitor_factor_long_short(
    factor_id: str,
    factor_values: pd.DataFrame,
    forward_returns: pd.DataFrame,
    *,
    store_root: str | Path,
    as_of: str,
    profile_id: str | None = None,
    top_quantile: float = 0.1,
) -> dict[str, Any]:
    """Monitor a factor's equal-weight top-minus-bottom spread."""

    required_values = {"date", "asset_code", "value"}
    required_returns = {"date", "asset_code", "forward_return"}
    if not required_values.issubset(factor_values.columns):
        raise ValueError(f"factor_values 缺少字段：{sorted(required_values - set(factor_values.columns))}")
    if not required_returns.issubset(forward_returns.columns):
        raise ValueError(f"forward_returns 缺少字段：{sorted(required_returns - set(forward_returns.columns))}")
    frame = factor_values.merge(forward_returns, on=["date", "asset_code"], how="inner")
    spreads = []
    for date, group in frame.groupby("date"):
        group = group.dropna(subset=["value", "forward_return"]).sort_values("value")
        if len(group) < 2:
            continue
        count = max(1, int(len(group) * top_quantile))
        short = group.head(count)["forward_return"].mean()
        long = group.tail(count)["forward_return"].mean()
        spreads.append({"date": date, "long_return": long, "short_return": short, "long_short_return": long - short})
    history = pd.DataFrame(spreads).sort_values("date")
    if history.empty:
        return {"factor_id": factor_id, "status": "INSUFFICIENT_DATA", "max_drawdown": None}
    history["cumulative_nav"] = (1.0 + history["long_short_return"]).cumprod()
    peak = history["cumulative_nav"].cummax()
    history["drawdown"] = history["cumulative_nav"] / peak - 1.0
    max_drawdown = float(history["drawdown"].min())
    store = ResearchStore(store_root)
    payload = history.iloc[-1].to_dict()
    payload.update({"factor_id": factor_id, "as_of": as_of, "max_drawdown": max_drawdown})
    store.save_factor_monitoring_snapshot(payload)
    if profile_id:
        profiles = [item for item in store.list_factor_activations() if item["profile_id"] == profile_id]
        if profiles:
            profile = profiles[0]
            threshold = float(profile.get("max_drawdown_override", -0.15))
            if int(profile.get("suspend_on_breach", 1)) and max_drawdown <= threshold and profile["status"] == "ENABLED":
                store.suspend_factor(profile_id, reason=f"因子多空回撤 {max_drawdown:.2%} <= {threshold:.2%}", as_of=as_of)
                return {"factor_id": factor_id, "status": "SUSPENDED", "max_drawdown": max_drawdown, "history": history}
    return {"factor_id": factor_id, "status": "OK", "max_drawdown": max_drawdown, "history": history}


__all__ = ["calculate_factor_scores", "monitor_factor_long_short", "formula_md5"]
