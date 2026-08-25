"""Managed local data access for research and factor production.

The desktop data page uses this module as its only network-facing entry point.
Providers fetch data; the manager normalizes and persists it before research
or scoring reads the local store.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pandas as pd
import numpy as np

from qteasy_research.pretrade.factor_scoring import formula_md5
from qteasy_research.pretrade.providers import (
    AkshareProvider,
    DProvider,
    LocalCsvProvider,
    ProviderData,
    TushareProvider,
)
from qteasy_research.pretrade.storage import ResearchStore
from qteasy_research.pretrade.symbols import normalize_code


@dataclass
class DataSyncRequest:
    datasets: list[str] = field(default_factory=lambda: ["market"])
    codes: list[str] = field(default_factory=list)
    universe: str = "selected"
    start: str | None = None
    end: str | None = None
    mode: str = "direct"
    update_policy: str = "incremental"
    build_factors: list[str] = field(default_factory=list)


@dataclass
class DataSourceHealth:
    source: str
    configured: bool
    available: bool
    message: str
    checked_at: str | None = None


@dataclass
class DataSyncResult:
    job_id: str | None = None
    status: str = "PARTIAL"
    rows_added: int = 0
    rows_updated: int = 0
    factors_built: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DataQueryResult:
    dataset: str
    rows: pd.DataFrame = field(default_factory=pd.DataFrame)
    total: int = 0
    as_of: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class FactorBuildResult:
    factor_id: str
    path: str | None = None
    manifest_path: str | None = None
    row_count: int = 0
    as_of: str | None = None
    status: str = "PARTIAL"
    warnings: list[str] = field(default_factory=list)


class MiddlewareDataProvider(Protocol):
    name: str

    def health_check(self) -> DataSourceHealth: ...

    def fetch(
        self,
        dataset: str,
        code: str,
        *,
        start: str | None = None,
        end: str | None = None,
    ) -> ProviderData: ...


class HttpMiddlewareProvider:
    """Small adapter for the future local data-platform HTTP contract."""

    name = "middleware"

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("DATA_MIDDLEWARE_URL", "")).rstrip("/")
        self.token = (token or os.getenv("DATA_MIDDLEWARE_TOKEN", "")).strip()

    def health_check(self) -> DataSourceHealth:
        if not self.base_url:
            return DataSourceHealth(self.name, False, False, "未配置 DATA_MIDDLEWARE_URL")
        try:
            import requests

            response = requests.get(f"{self.base_url}/health", timeout=5)
            return DataSourceHealth(self.name, True, response.ok, f"HTTP {response.status_code}")
        except Exception as exc:
            return DataSourceHealth(self.name, True, False, f"{type(exc).__name__}: {exc}")

    def fetch(self, dataset: str, code: str, *, start: str | None = None, end: str | None = None) -> ProviderData:
        if not self.base_url:
            raise RuntimeError("未配置 DATA_MIDDLEWARE_URL")
        import requests

        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        response = requests.post(
            f"{self.base_url}/v1/data/query",
            json={"dataset": dataset, "codes": [code], "start": start, "end": end},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        frame = pd.DataFrame(payload.get("rows", []))
        return ProviderData(
            data=frame,
            source=self.name,
            as_of=payload.get("as_of"),
            message=str(payload.get("message") or "数据中台返回"),
            official=bool(payload.get("official", False)),
            request={"dataset": dataset, "code": code, "start": start, "end": end},
            fields_returned=list(frame.columns),
        )


def _slice_dates(frame: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    if frame.empty or "trade_date" not in frame.columns:
        return frame
    result = frame.copy()
    dates = pd.to_datetime(result["trade_date"], errors="coerce")
    if start:
        result = result.loc[dates >= pd.Timestamp(start)]
        dates = dates.loc[result.index]
    if end:
        result = result.loc[dates <= pd.Timestamp(end)]
    return result.reset_index(drop=True)


class DataManager:
    """Download/update/query service backed by ResearchStore SQLite."""

    def __init__(self, store_root: str | Path, *, data_dir: str | Path | None = None) -> None:
        self.root = Path(store_root)
        self.store = ResearchStore(self.root)
        self.local = LocalCsvProvider(data_dir)

    def check_data_source(self, mode: str = "direct") -> list[DataSourceHealth]:
        if mode == "middleware":
            return [HttpMiddlewareProvider().health_check()]
        output = [
            DataSourceHealth("akshare", True, self._package_available("akshare"), "已安装" if self._package_available("akshare") else "未安装 akshare"),
            DataSourceHealth("tushare", bool(os.getenv("TUSHARE_TOKEN", "").strip()), bool(os.getenv("TUSHARE_TOKEN", "").strip()), "已配置" if os.getenv("TUSHARE_TOKEN", "").strip() else "未配置 TUSHARE_TOKEN"),
        ]
        return output

    @staticmethod
    def _package_available(name: str) -> bool:
        try:
            __import__(name)
            return True
        except Exception:
            return False

    def _codes(self, request: DataSyncRequest) -> list[str]:
        if request.codes:
            return [normalize_code(code)[0] for code in request.codes]
        if request.universe in {"all_etf", "all_funds"}:
            frame = pd.read_csv(self.local.data_dir / "fund_basic.csv") if (self.local.data_dir / "fund_basic.csv").exists() else pd.DataFrame()
            if not frame.empty and "ts_code" in frame.columns:
                return sorted(frame["ts_code"].astype(str).str.upper().tolist())
        return []

    def _providers(self, mode: str) -> list[Any]:
        if mode == "middleware":
            return [HttpMiddlewareProvider()]
        # CSV is imported explicitly as a bootstrap action; it is not treated
        # as a competing live source during an API update.
        return [DProvider(), AkshareProvider(), TushareProvider()]

    def _identity(self, code: str):
        return self.local.resolve(code)

    def _fetch(self, dataset: str, code: str, request: DataSyncRequest, job_id: str, result: DataSyncResult) -> ProviderData:
        identity = self._identity(code)
        providers = self._providers(request.mode)
        for provider in providers:
            started = time.perf_counter()
            try:
                if request.mode == "middleware":
                    fetched = provider.fetch(dataset, code, start=request.start, end=request.end)
                elif dataset == "market":
                    fetched = provider.get_price_history(identity)
                elif dataset == "metadata":
                    fetched = provider.get_metadata(identity)
                else:
                    fetched = ProviderData(source=getattr(provider, "name", "unknown"), message=f"暂不支持数据集：{dataset}")
                frame = _slice_dates(fetched.data, request.start, request.end) if dataset == "market" else fetched.data
                fetched.data = frame
                if dataset == "market" and not frame.empty and "trade_date" in frame.columns:
                    fetched.as_of = pd.to_datetime(frame["trade_date"], errors="coerce").max().date().isoformat()
                attempt = {
                    "job_id": job_id,
                    "source": getattr(provider, "name", "unknown"),
                    "dataset": dataset,
                    "code": code,
                    "success": int(not frame.empty),
                    "request": fetched.request,
                    "message": fetched.message,
                    "as_of": fetched.as_of,
                    "rows_returned": len(frame),
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                }
                self.store.save_data_update_attempt(attempt)
                result.attempts.append(attempt)
                if not frame.empty:
                    return fetched
            except Exception as exc:
                attempt = {
                    "job_id": job_id,
                    "source": getattr(provider, "name", "unknown"),
                    "dataset": dataset,
                    "code": code,
                    "success": 0,
                    "request": {},
                    "message": f"{type(exc).__name__}: {exc}",
                    "rows_returned": 0,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                }
                self.store.save_data_update_attempt(attempt)
                result.attempts.append(attempt)
        return ProviderData(message=f"没有可用数据源：{dataset}/{code}")

    def sync_data(self, request: DataSyncRequest) -> DataSyncResult:
        if request.mode not in {"direct", "middleware"}:
            raise ValueError(f"不支持的数据连接方式：{request.mode}")
        if request.update_policy not in {"incremental", "refresh", "force_refresh"}:
            raise ValueError(f"不支持的数据更新方式：{request.update_policy}")
        codes = self._codes(request)
        result = DataSyncResult()
        if not codes:
            result.status = "PARTIAL"
            result.warnings.append("没有选择需要更新的代码")
            return result
        job_id = self.store.create_data_update_job(
            mode=request.mode, datasets=request.datasets, codes=codes, update_policy=request.update_policy
        )
        result.job_id = job_id
        try:
            for code in codes:
                identity = self._identity(code)
                for dataset in request.datasets:
                    start = request.start
                    if dataset == "market" and request.update_policy == "incremental" and not start:
                        latest = self.store.latest_market_date(identity.code)
                        if latest:
                            start = (pd.Timestamp(latest) + pd.Timedelta(days=1)).date().isoformat()
                    local_request = DataSyncRequest(
                        datasets=[dataset], codes=[code], start=start, end=request.end,
                        mode=request.mode, update_policy=request.update_policy,
                    )
                    fetched = self._fetch(dataset, code, local_request, job_id, result)
                    if fetched.data.empty:
                        result.warnings.append(f"{dataset}/{code}：{fetched.message}")
                        continue
                    snapshot = self.store.save_data_snapshot(
                        code=identity.code,
                        data_type=dataset,
                        source=fetched.source or "unknown",
                        as_of=fetched.as_of,
                        payload=fetched.raw if fetched.raw is not None else fetched.data,
                        request=fetched.request,
                        freshness_days=1 if dataset == "market" else 7,
                        official=fetched.official,
                        fields=fetched.fields_returned,
                    )
                    if dataset == "market":
                        counts = self.store.upsert_market_daily(
                            fetched.data,
                            code=identity.code,
                            asset_type=identity.asset_type,
                            source=fetched.source or "unknown",
                            snapshot_id=snapshot["snapshot_id"],
                            available_at=fetched.as_of,
                        )
                        result.rows_added += counts["added"]
                        result.rows_updated += counts["updated"]
                    elif dataset == "metadata":
                        metadata = fetched.data.iloc[0].to_dict()
                        result.rows_added += self.store.upsert_asset_metadata(
                            metadata,
                            code=identity.code,
                            asset_type=identity.asset_type,
                            source=fetched.source or "unknown",
                            as_of=fetched.as_of,
                            snapshot_id=snapshot["snapshot_id"],
                        )
            for factor_id in request.build_factors:
                built = self.build_factor_values(
                    [factor_id], asset_type="ETF", horizon="medium", codes=codes, as_of_date=request.end
                )
                result.factors_built.extend([factor_id] if built.status == "COMPLETED" else [])
                result.warnings.extend(built.warnings)
            result.status = "COMPLETED" if not result.warnings else "PARTIAL"
            self.store.finish_data_update_job(
                job_id, status=result.status, rows_added=result.rows_added, rows_updated=result.rows_updated,
            )
        except Exception as exc:
            result.status = "FAILED"
            result.warnings.append(f"{type(exc).__name__}: {exc}")
            self.store.finish_data_update_job(
                job_id, status="FAILED", rows_added=result.rows_added, rows_updated=result.rows_updated, error=str(exc),
            )
        return result

    def import_existing_local_data(self, *, data_dir: str | Path | None = None) -> DataSyncResult:
        source_dir = Path(data_dir) if data_dir else self.local.data_dir
        request = DataSyncRequest(codes=["__local_import__"])
        result = DataSyncResult(job_id=self.store.create_data_update_job(mode="local_import", datasets=["market"], codes=[], update_policy="refresh"))
        try:
            files = [("fund_daily.csv", "ETF"), ("stock_daily.csv", "STOCK"), ("index_daily.csv", "INDEX")]
            for filename, asset_type in files:
                path = source_dir / filename
                if not path.exists():
                    continue
                frame = pd.read_csv(path)
                if "ts_code" not in frame.columns:
                    continue
                for code, group in frame.groupby(frame["ts_code"].astype(str).str.upper()):
                    normalized = normalize_code(code)[0]
                    counts = self.store.upsert_market_daily(
                        group, code=normalized, asset_type=asset_type, source="local_csv", available_at=None
                    )
                    result.rows_added += counts["added"]
                    result.rows_updated += counts["updated"]
            result.status = "COMPLETED" if result.rows_added else "PARTIAL"
            if not result.rows_added:
                result.warnings.append("没有找到可导入的行情 CSV")
            self.store.finish_data_update_job(result.job_id, status=result.status, rows_added=result.rows_added, rows_updated=result.rows_updated)
        except Exception as exc:
            result.status = "FAILED"
            result.warnings.append(f"{type(exc).__name__}: {exc}")
            self.store.finish_data_update_job(result.job_id, status="FAILED", error=str(exc))
        return result

    def query_data(self, dataset: str, **kwargs: Any) -> DataQueryResult:
        if dataset == "market":
            rows = self.store.query_market_daily(**kwargs)
            as_of = rows["trade_date"].max() if not rows.empty else None
            return DataQueryResult(dataset, rows, len(rows), as_of)
        if dataset == "catalog":
            frame = pd.DataFrame(self.store.list_data_catalog(code=kwargs.get("code")))
            return DataQueryResult(dataset, frame, len(frame), None)
        if dataset == "factor":
            frame = pd.DataFrame(self.store.list_data_catalog(dataset="factor", code=kwargs.get("code")))
            return DataQueryResult(dataset, frame, len(frame), None)
        return DataQueryResult(dataset, warnings=[f"暂不支持查询数据集：{dataset}"])

    def build_factor_values(
        self,
        factor_ids: list[str],
        *,
        asset_type: str,
        horizon: str,
        codes: list[str] | None = None,
        as_of_date: str | None = None,
    ) -> FactorBuildResult:
        output_dir = self.root / "factor_values"
        output_dir.mkdir(parents=True, exist_ok=True)
        market = self.store.query_market_daily(
            asset_type=asset_type.upper(),
            end=as_of_date,
            limit=10_000_000,
        )
        if not market.empty:
            market["trade_date"] = pd.to_datetime(market["trade_date"], errors="coerce")
            market = market.dropna(subset=["trade_date"])
        if codes:
            normalized = {normalize_code(code)[0] for code in codes}
            market = market.loc[market["code"].isin(normalized)].copy()
        output: list[FactorBuildResult] = []
        formulas = {
            "momentum_60d": ("close.pct_change(60)", lambda group: group["close"].pct_change(60)),
            "momentum_120d": ("close.pct_change(120)", lambda group: group["close"].pct_change(120)),
            "low_volatility_20d": ("-close.pct_change().rolling(20).std()", lambda group: -group["close"].pct_change().rolling(20).std()),
            "liquidity_turnover": (
                "log1p(amount).rolling(20).mean()",
                lambda group: pd.Series(group["amount"], index=group.index).pipe(pd.to_numeric, errors="coerce").clip(lower=0).pipe(np.log1p).rolling(20).mean(),
            ),
        }
        for factor_id in factor_ids:
            if factor_id not in formulas:
                output.append(FactorBuildResult(factor_id, warnings=[f"暂不支持自动生成因子：{factor_id}"]))
                continue
            if market.empty:
                output.append(FactorBuildResult(factor_id, warnings=["SQLite 中没有可用行情，请先更新数据。"]))
                continue
            formula, calculator = formulas[factor_id]
            rows: list[pd.DataFrame] = []
            for code, group in market.groupby("code"):
                group = group.sort_values("trade_date").copy()
                group["value"] = calculator(group)
                group = group.dropna(subset=["value"])
                if not group.empty:
                    rows.append(pd.DataFrame({
                        "date": group["trade_date"].dt.date.astype(str),
                        "asset_code": code,
                        "value": group["value"].astype(float),
                        "available_at": group["trade_date"].dt.date.astype(str),
                    }))
            if not rows:
                output.append(FactorBuildResult(factor_id, warnings=["行情样本不足以生成该因子。"]))
                continue
            frame = pd.concat(rows, ignore_index=True).sort_values(["date", "asset_code"])
            path = output_dir / f"{factor_id}.parquet"
            manifest_path = output_dir / f"{factor_id}.manifest.json"
            frame.to_parquet(path, index=False)
            manifest = {
                "factor_id": factor_id,
                "formula": formula,
                "formula_hash": formula_md5(formula),
                "value_semantics": "asset_exposure",
                "source": "sqlite:data_market_daily",
                "asset_type": asset_type.upper(),
                "horizon": horizon,
            }
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            self.store.save_factor_snapshot({
                "factor_id": factor_id,
                "asset_type": asset_type.upper(),
                "horizon": horizon,
                "as_of": str(frame["date"].max()),
                "file_path": str(path),
                "manifest_path": str(manifest_path),
                "row_count": len(frame),
                "content_hash": content_hash,
                "formula_hash": manifest["formula_hash"],
                "value_semantics": manifest["value_semantics"],
            })
            output.append(FactorBuildResult(
                factor_id, str(path), str(manifest_path), len(frame), str(frame["date"].max()), "COMPLETED"
            ))
        if len(output) == 1:
            return output[0]
        combined = FactorBuildResult(
            "multiple",
            status="COMPLETED" if all(item.status == "COMPLETED" for item in output) else "PARTIAL",
            row_count=sum(item.row_count for item in output),
            as_of=max((item.as_of for item in output if item.as_of), default=None),
        )
        combined.warnings = [warning for item in output for warning in item.warnings]
        return combined


__all__ = [
    "DataManager", "DataSyncRequest", "DataSyncResult", "DataQueryResult", "DataSourceHealth",
    "FactorBuildResult", "HttpMiddlewareProvider", "MiddlewareDataProvider",
]
