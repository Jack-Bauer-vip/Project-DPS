"""未来数据中台的因子数据契约。

当前实现仍由 AKShare/Tushare/本地快照负责供数。本模块只定义研究模块
需要的数据形状，使后续接入 SQLite、Parquet、DuckDB 或本地 HTTP 服务时
不需要重写因子研究逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import pandas as pd

from qteasy_research.pretrade.schemas import DataQualityMetadata


@dataclass
class FactorObservation:
    factor_id: str
    asset_code: str | None
    observation_date: str
    value: float | None
    unit: str | None = None
    source: str = ""
    release_date: str | None = None
    available_at: str | None = None
    vintage_id: str | None = None
    revision_id: str | None = None
    completeness: float | None = None
    publish_lag: int | None = None
    quality_level: str = "C"


@dataclass
class FactorDataResponse:
    observations: pd.DataFrame = field(default_factory=pd.DataFrame)
    source: str = ""
    as_of_date: str | None = None
    quality: DataQualityMetadata = field(default_factory=DataQualityMetadata)
    warnings: list[str] = field(default_factory=list)


class FactorDataProvider(Protocol):
    """历史因子快照接口；研究模块按 available_at 做 point-in-time 过滤。"""

    name: str

    def get_observations(
        self,
        factor_ids: list[str],
        assets: list[str] | None = None,
        start: str | None = None,
        end: str | None = None,
        as_of_date: str | None = None,
    ) -> FactorDataResponse: ...

    def get_factor_snapshot(self, factor_id: str, as_of_date: str) -> FactorDataResponse: ...


def filter_point_in_time(observations: pd.DataFrame, as_of_date: str | None) -> pd.DataFrame:
    """只保留研究时点已经可获得的数据，不填补缺失值。"""

    if observations.empty or not as_of_date or "available_at" not in observations.columns:
        return observations.copy()
    frame = observations.copy()
    available = pd.to_datetime(frame["available_at"], errors="coerce")
    cutoff = pd.Timestamp(as_of_date)
    return frame.loc[available.isna() | (available <= cutoff)].copy()


def make_observation_quality(row: dict[str, Any]) -> DataQualityMetadata:
    """从中台行记录创建统一质量对象，便于直连 Provider 逐步补充元数据。"""

    return DataQualityMetadata(
        source=str(row.get("source") or ""),
        observation_date=row.get("observation_date"),
        release_date=row.get("release_date"),
        available_at=row.get("available_at") or row.get("release_date"),
        revision_id=row.get("revision_id"),
        vintage_id=row.get("vintage_id"),
        completeness=row.get("completeness"),
        publish_lag=row.get("publish_lag"),
        revision_frequency=row.get("revision_frequency"),
        quality_level=str(row.get("quality_level") or "C"),
        quality_score=row.get("quality_score"),
        warnings=list(row.get("warnings") or []),
    )
