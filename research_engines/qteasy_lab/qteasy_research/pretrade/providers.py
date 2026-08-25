"""投前研究数据 Provider：本地优先，在线数据作为可选补充。"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from qteasy_research.pretrade.schemas import AssetIdentity
from qteasy_research.pretrade.symbols import resolve_identity


class ProviderUnavailable(RuntimeError):
    """数据源未配置、不可用或当前接口没有权限。"""


@dataclass
class ProviderData:
    data: pd.DataFrame = field(default_factory=pd.DataFrame)
    source: str = ""
    as_of: str | None = None
    message: str = ""
    official: bool = False
    request: dict[str, Any] = field(default_factory=dict)
    raw: Any = None
    fields_returned: list[str] = field(default_factory=list)
    fields_missing: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.data.empty


class MarketDataProvider(Protocol):
    name: str

    def get_price_history(self, identity: AssetIdentity) -> ProviderData: ...

    def get_benchmark_history(self, code: str) -> ProviderData: ...

    def get_metadata(self, identity: AssetIdentity) -> ProviderData: ...


@dataclass
class AssetDataSnapshot:
    code: str
    asset_type: str
    as_of: str | None
    price_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    benchmark_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    metadata: dict[str, Any] = field(default_factory=dict)
    exposure: dict[str, Any] = field(default_factory=dict)
    fundamentals: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    source_status: list[dict[str, Any]] = field(default_factory=list)


class CleanDataProvider(Protocol):
    """未来本地清洗数据系统的最小接入协议。"""

    name: str

    def get_asset_snapshot(
        self,
        code: str,
        *,
        fields: list[str],
        as_of: str | None = None,
    ) -> AssetDataSnapshot: ...


class EvidenceProvider(Protocol):
    """联网证据搜索接口；研究编排层不依赖具体搜索服务。"""

    name: str

    def search_research_evidence(
        self,
        query: str,
        *,
        as_of: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]: ...


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _clean_record(record: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in record.items():
        if pd.isna(value):
            cleaned[key] = None
        elif hasattr(value, "item"):
            cleaned[key] = value.item()
        else:
            cleaned[key] = value
    return cleaned


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    aliases = {
        "日期": "trade_date",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "vol",
        "成交额": "amount",
        "代码": "ts_code",
    }
    frame = frame.rename(columns=aliases).copy()
    if "trade_date" in frame:
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    for column in ("open", "high", "low", "close", "pre_close", "vol", "amount"):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "close" not in frame:
        return pd.DataFrame()
    return frame.dropna(subset=["trade_date", "close"]).sort_values("trade_date")


class LocalCsvProvider:
    name = "local_csv"

    def __init__(self, data_dir: str | Path | None = None) -> None:
        default = Path(__file__).resolve().parents[2] / "data"
        self.data_dir = Path(data_dir) if data_dir else default

    def _fund_basic(self) -> pd.DataFrame:
        return _read_csv(self.data_dir / "fund_basic.csv")

    def _stock_basic(self) -> pd.DataFrame:
        return _read_csv(self.data_dir / "stock_basic.csv")

    def _fund_metadata(self) -> dict[str, dict[str, Any]]:
        frame = self._fund_basic()
        if frame.empty or "ts_code" not in frame:
            return {}
        return {
            str(code): _clean_record(record)
            for code, record in frame.set_index("ts_code").to_dict("index").items()
        }

    def _stock_metadata(self) -> dict[str, dict[str, Any]]:
        frame = self._stock_basic()
        if frame.empty or "ts_code" not in frame:
            return {}
        return {
            str(code): _clean_record(record)
            for code, record in frame.set_index("ts_code").to_dict("index").items()
        }

    def resolve(self, raw_code: str) -> AssetIdentity:
        return resolve_identity(
            raw_code,
            fund_metadata=self._fund_metadata(),
            stock_metadata=self._stock_metadata(),
        )

    def get_metadata(self, identity: AssetIdentity) -> ProviderData:
        metadata = identity.metadata.copy()
        if not metadata:
            return ProviderData(source=self.name, message="本地没有标的基础资料")
        return ProviderData(
            data=pd.DataFrame([metadata]),
            source=self.name,
            as_of=None,
            message="读取本地基础资料",
            official=False,
        )

    def get_price_history(self, identity: AssetIdentity) -> ProviderData:
        table = "fund_daily.csv" if identity.asset_type in {"ETF", "LOF", "QDII"} else "stock_daily.csv"
        frame = _normalize_frame(_read_csv(self.data_dir / table))
        if not frame.empty and "ts_code" in frame:
            frame = frame[frame["ts_code"].astype(str).str.upper() == identity.code]
        if frame.empty:
            return ProviderData(source=self.name, message=f"本地没有 {identity.code} 的 {table}")
        return ProviderData(
            data=frame.reset_index(drop=True),
            request={"provider": self.name, "code": identity.code},
            fields_returned=list(frame.columns),
            source=self.name,
            as_of=frame["trade_date"].max().strftime("%Y-%m-%d"),
            message=f"读取本地 {table}",
        )

    def get_benchmark_history(self, code: str) -> ProviderData:
        if not isinstance(code, str) or not code:
            return ProviderData(source=self.name, message="基准代码为空")
        frame = _normalize_frame(_read_csv(self.data_dir / "index_daily.csv"))
        if not frame.empty and "ts_code" in frame:
            frame = frame[frame["ts_code"].astype(str).str.upper() == code.upper()]
        if frame.empty:
            return ProviderData(source=self.name, message=f"本地没有基准 {code}")
        return ProviderData(
            data=frame.reset_index(drop=True),
            request={"provider": self.name, "code": code},
            fields_returned=list(frame.columns),
            source=self.name,
            as_of=frame["trade_date"].max().strftime("%Y-%m-%d"),
            message=f"读取本地指数行情 {code}",
        )


class ResearchSnapshotProvider:
    """读取研究引擎已经保存的标准化快照，作为本地兜底源。"""

    name = "research_snapshot"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _latest_frame(self, code: str, data_type: str) -> tuple[pd.DataFrame, dict[str, Any] | None]:
        from qteasy_research.pretrade.storage import ResearchStore

        snapshots = ResearchStore(self.root).list_snapshots(code, data_type=data_type)
        for snapshot in snapshots:
            path = Path(str(snapshot.get("payload_path", "")))
            if not path.exists():
                continue
            payload = ResearchStore.read_json(path)
            if payload.get("kind") == "dataframe":
                return pd.DataFrame(payload.get("records", [])), snapshot
        return pd.DataFrame(), None

    def get_price_history(self, identity: AssetIdentity) -> ProviderData:
        frame, snapshot = self._latest_frame(identity.code, "market")
        frame = _normalize_frame(frame)
        if frame.empty:
            return ProviderData(source=self.name, message=f"研究快照中没有 {identity.code} 行情")
        return ProviderData(
            data=frame.reset_index(drop=True),
            source=self.name,
            as_of=(snapshot or {}).get("as_of") or frame["trade_date"].max().strftime("%Y-%m-%d"),
            message="复用研究快照行情",
            request={"snapshot_id": (snapshot or {}).get("snapshot_id"), "data_type": "market"},
            fields_returned=list(frame.columns),
        )

    def get_benchmark_history(self, code: str) -> ProviderData:
        frame, snapshot = self._latest_frame(code, "benchmark")
        frame = _normalize_frame(frame)
        if frame.empty:
            return ProviderData(source=self.name, message=f"研究快照中没有基准 {code}")
        return ProviderData(
            data=frame.reset_index(drop=True),
            source=self.name,
            as_of=(snapshot or {}).get("as_of") or frame["trade_date"].max().strftime("%Y-%m-%d"),
            message="复用研究快照基准行情",
            request={"snapshot_id": (snapshot or {}).get("snapshot_id"), "data_type": "benchmark"},
            fields_returned=list(frame.columns),
        )

    def get_metadata(self, identity: AssetIdentity) -> ProviderData:
        frame, snapshot = self._latest_frame(identity.code, "metadata")
        if frame.empty:
            return ProviderData(source=self.name, message=f"研究快照中没有 {identity.code} 基础资料")
        return ProviderData(
            data=frame,
            source=self.name,
            as_of=(snapshot or {}).get("as_of"),
            message="复用研究快照基础资料",
            request={"snapshot_id": (snapshot or {}).get("snapshot_id"), "data_type": "metadata"},
            fields_returned=list(frame.columns),
        )


class SqliteProvider:
    """Read normalized data written by DataManager from research.sqlite3."""

    name = "sqlite_db"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def get_price_history(self, identity: AssetIdentity) -> ProviderData:
        from qteasy_research.pretrade.storage import ResearchStore

        frame = ResearchStore(self.root).query_market_daily(
            code=identity.code, asset_type=identity.asset_type, limit=10_000_000
        )
        if frame.empty:
            return ProviderData(source=self.name, message=f"SQLite 中没有 {identity.code} 行情")
        frame = frame.rename(columns={"volume": "vol"})
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
        return ProviderData(
            data=frame,
            source=self.name,
            as_of=frame["trade_date"].max().date().isoformat(),
            message="读取 SQLite 标准化行情",
            request={"code": identity.code, "data_type": "market"},
            fields_returned=list(frame.columns),
        )

    def get_benchmark_history(self, code: str) -> ProviderData:
        from qteasy_research.pretrade.storage import ResearchStore

        frame = ResearchStore(self.root).query_market_daily(code=code, limit=10_000_000)
        if frame.empty:
            return ProviderData(source=self.name, message=f"SQLite 中没有基准 {code} 行情")
        frame = frame.rename(columns={"volume": "vol"})
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
        return ProviderData(
            data=frame,
            source=self.name,
            as_of=frame["trade_date"].max().date().isoformat(),
            message="读取 SQLite 标准化基准行情",
            request={"code": code, "data_type": "market"},
            fields_returned=list(frame.columns),
        )

    def get_metadata(self, identity: AssetIdentity) -> ProviderData:
        from qteasy_research.pretrade.storage import ResearchStore

        frame = ResearchStore(self.root).query_asset_metadata(code=identity.code)
        if frame.empty:
            return ProviderData(source=self.name, message=f"SQLite 中没有 {identity.code} 基础资料")
        metadata = {str(row["field_name"]): row["field_value"] for _, row in frame.iterrows()}
        return ProviderData(
            data=pd.DataFrame([metadata]),
            source=self.name,
            as_of=str(frame["as_of"].dropna().max()) if frame["as_of"].notna().any() else None,
            message="读取 SQLite 标准化基础资料",
            request={"code": identity.code, "data_type": "metadata"},
            fields_returned=list(metadata),
        )


class TushareProvider:
    name = "tushare"

    def __init__(self, token: str | None = None, base_url: str | None = None) -> None:
        self.token = (token or os.getenv("TUSHARE_TOKEN", "")).strip()
        self.base_url = (base_url or os.getenv("TUSHARE_API_URL", "https://ts.gyzcloud.top/api")).rstrip("/")

    def _api(self):
        if not self.token:
            raise ProviderUnavailable("未配置 TUSHARE_TOKEN")
        try:
            import tushare as ts
        except ImportError as exc:
            raise ProviderUnavailable("未安装 tushare") from exc
        # Pass the token directly so the SDK does not need to create or update
        # the user's tk.csv file during provider initialization.
        api = ts.pro_api(self.token)
        # Third-party Tushare-compatible services expose the same SDK surface
        # but use a different HTTP endpoint.
        if self.base_url:
            api._DataApi__http_url = self.base_url
        return api

    def get_metadata(self, identity: AssetIdentity) -> ProviderData:
        api = self._api()
        numeric = identity.code.split(".", 1)[0]
        if identity.asset_type in {"ETF", "LOF", "QDII"}:
            frame = api.fund_basic(ts_code=identity.code)
        else:
            frame = api.stock_basic(ts_code=identity.code)
        if frame is None or frame.empty:
            return ProviderData(source=self.name, message="Tushare 基础资料为空")
        frame = frame.copy()
        frame = frame.where(pd.notna(frame), None)
        if frame.empty:
            return ProviderData(source=self.name, message="Tushare 基础资料为空")
        return ProviderData(data=frame, source=self.name, message=f"Tushare 基础资料 {numeric}", official=True)

    def get_price_history(self, identity: AssetIdentity) -> ProviderData:
        api = self._api()
        if identity.asset_type in {"ETF", "LOF", "QDII"}:
            frame = api.fund_daily(ts_code=identity.code)
        else:
            frame = api.daily(ts_code=identity.code)
        raw = frame.copy() if frame is not None else pd.DataFrame()
        frame = _normalize_frame(frame)
        return ProviderData(
            data=frame,
            source=self.name,
            as_of=frame["trade_date"].max().strftime("%Y-%m-%d") if not frame.empty else None,
            message="Tushare 历史行情",
            official=True,
            raw=raw,
            request={"ts_code": identity.code, "endpoint": "fund_daily" if identity.asset_type in {"ETF", "LOF", "QDII"} else "daily"},
            fields_returned=list(frame.columns),
        )

    def get_benchmark_history(self, code: str) -> ProviderData:
        raw = self._api().index_daily(ts_code=code)
        frame = _normalize_frame(raw)
        return ProviderData(
            data=frame,
            source=self.name,
            as_of=frame["trade_date"].max().strftime("%Y-%m-%d") if not frame.empty else None,
            message="Tushare 基准行情",
            official=True,
            raw=raw,
            request={"ts_code": code, "endpoint": "index_daily"},
            fields_returned=list(frame.columns),
        )


class AkshareProvider:
    name = "akshare"

    def _ak(self):
        try:
            import akshare as ak
        except ImportError as exc:
            raise ProviderUnavailable("未安装 akshare") from exc
        return ak

    def get_metadata(self, identity: AssetIdentity) -> ProviderData:
        # AKShare 接口变化较快；基础资料由后续专项适配器补齐。
        return ProviderData(source=self.name, message="AKShare 基础资料适配尚未提供")

    def get_price_history(self, identity: AssetIdentity) -> ProviderData:
        ak = self._ak()
        numeric = identity.code.split(".", 1)[0]
        if identity.asset_type in {"ETF", "LOF", "QDII"}:
            frame = ak.fund_etf_hist_em(symbol=numeric, period="daily", adjust="")
        else:
            frame = ak.stock_zh_a_hist(symbol=numeric, period="daily", adjust="")
        raw = frame.copy() if frame is not None else pd.DataFrame()
        frame = _normalize_frame(frame)
        return ProviderData(
            data=frame,
            raw=raw,
            request={"symbol": numeric, "endpoint": "fund_etf_hist_em" if identity.asset_type in {"ETF", "LOF", "QDII"} else "stock_zh_a_hist"},
            fields_returned=list(frame.columns),
            source=self.name,
            as_of=frame["trade_date"].max().strftime("%Y-%m-%d") if not frame.empty else None,
            message="AKShare 历史行情",
        )

    def get_benchmark_history(self, code: str) -> ProviderData:
        return ProviderData(source=self.name, message="AKShare 基准适配尚未提供")


class DProvider:
    """Bern 数据中台（D）行情 Provider：纯后台首选源，失败自动降级原链。

    服务 B 的 A 股 ETF / 指数日线（统一数据知识层·第二刀 §5.3）。行情
    命中即 ``official=True``；``get_metadata`` 不提供 ETF 静态资料（D 只
    覆盖行情，静态资料由原链继续服务）。
    """

    name = "bern_data"

    # D 分发接口未指定 limit 时仅返回最近 200 条；研究需全量历史，取接口上限。
    _DEFAULT_LIMIT = 100000

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self.base_url = (
            base_url
            or os.getenv("D_BASE_URL", "")
            or os.getenv("BERN_DATA_BASE_URL", "")
            or "http://127.0.0.1:8765/api/v1"
        ).rstrip("/")
        self.api_key = (
            api_key
            or os.getenv("D_API_KEY", "")
            or os.getenv("BERN_DATA_API_KEY", "")
            or os.getenv("API_TOKEN", "")
        ).strip()

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        import requests

        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        url = f"{self.base_url}{path}"
        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise ProviderUnavailable(f"Bern 数据中台不可用：{type(exc).__name__}: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("code") != 200:
            raise ProviderUnavailable(f"Bern 数据中台返回异常：{payload}")
        return payload.get("data") or []

    def _d_to_frame(self, records: list[dict[str, Any]], ts_code: str) -> pd.DataFrame:
        frame = pd.DataFrame(records or [])
        frame = frame.rename(columns={"date": "trade_date", "volume": "vol"})
        frame["ts_code"] = ts_code
        return _normalize_frame(frame)

    def _to_d_symbol(self, code: str) -> str:
        """``000300.SH → sh000300``；SPY/TLT/GLD/HSI 等原样大写。"""
        raw = (code or "").strip()
        match = re.match(r"^(\d{6})[.](SH|SZ|BJ)$", raw.upper())
        if match:
            return f"{match.group(2).lower()}{match.group(1)}"
        return raw.upper()

    def get_price_history(self, identity: AssetIdentity) -> ProviderData:
        table = "fund_etf_daily" if identity.asset_type in {"ETF", "LOF", "QDII"} else "stock_daily"
        code = identity.code.split(".", 1)[0]
        records = self._get_json(f"/data/{table}", {"code": code, "limit": self._DEFAULT_LIMIT})
        frame = self._d_to_frame(records, identity.code)
        if frame.empty:
            return ProviderData(source=self.name, message=f"Bern 数据中台没有 {identity.code} 行情")
        return ProviderData(
            data=frame.reset_index(drop=True),
            source=self.name,
            as_of=frame["trade_date"].max().strftime("%Y-%m-%d"),
            message="Bern 数据中台历史行情",
            official=True,
            request={"provider": self.name, "table": table, "code": code},
            fields_returned=list(frame.columns),
        )

    def get_benchmark_history(self, code: str) -> ProviderData:
        symbol = self._to_d_symbol(code)
        records = self._get_json("/data/index_daily", {"code": symbol, "limit": self._DEFAULT_LIMIT})
        frame = self._d_to_frame(records, code)
        if frame.empty:
            return ProviderData(source=self.name, message=f"Bern 数据中台没有基准 {code}")
        return ProviderData(
            data=frame.reset_index(drop=True),
            source=self.name,
            as_of=frame["trade_date"].max().strftime("%Y-%m-%d"),
            message="Bern 数据中台基准行情",
            official=True,
            request={"provider": self.name, "table": "index_daily", "symbol": symbol},
            fields_returned=list(frame.columns),
        )

    def get_metadata(self, identity: AssetIdentity) -> ProviderData:
        return ProviderData(source=self.name, message="D 不提供 ETF 静态资料")


class CompositeProvider:
    """按顺序尝试数据源，并保留每次尝试的可审计记录。"""

    def __init__(self, providers: list[MarketDataProvider]) -> None:
        self.providers = providers
        self.attempts: list[dict[str, Any]] = []

    def _try(self, operation: str, *args: Any) -> ProviderData:
        for provider in self.providers:
            started = time.perf_counter()
            try:
                result = getattr(provider, operation)(*args)
                self.attempts.append({
                    "source": provider.name,
                    "operation": operation,
                    "ok": result.ok,
                    "message": result.message,
                    "as_of": result.as_of,
                    "request": result.request,
                    "fields_returned": result.fields_returned,
                    "fields_missing": result.fields_missing,
                    "official": result.official,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                })
                if result.ok:
                    return result
            except Exception as exc:
                self.attempts.append({
                    "source": provider.name,
                    "operation": operation,
                    "ok": False,
                    "message": f"{type(exc).__name__}: {exc}",
                    "request": {},
                    "fields_returned": [],
                    "fields_missing": [],
                    "official": False,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                })
        return ProviderData(message=f"没有数据源完成 {operation}")

    def get_price_history(self, identity: AssetIdentity) -> ProviderData:
        return self._try("get_price_history", identity)

    def get_benchmark_history(self, code: str) -> ProviderData:
        return self._try("get_benchmark_history", code)

    def get_metadata(self, identity: AssetIdentity) -> ProviderData:
        return self._try("get_metadata", identity)
