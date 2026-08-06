"""元数据头（``generated_date`` / ``data_asof``）与新鲜度校验工具。

CSV 首行 ``# generated_date=…; data_asof=…; …``；parquet 用 pyarrow key-value
元数据；``validate_freshness`` 为 B/A 共用的单一实现，防两侧规则漂移。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pandas as pd

from qteasy_research.reference.config import DATA_ASOF_MAX_AGE_DAYS, SCHEMA_VERSION


def today_iso() -> str:
    """本地日期 ISO 字符串（Y-m-d）。"""
    return dt.date.today().isoformat()


def now_iso() -> str:
    """本地时间 UTC ISO 字符串（含时区）。"""
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def build_header(
    *,
    generated_date: str,
    data_asof: str,
    schema_version: str = SCHEMA_VERSION,
    source: str = "systemB",
) -> dict[str, str]:
    """构造元数据头字典。"""
    return {
        "schema_version": schema_version,
        "source_system": source,
        "generated_date": generated_date,
        "data_asof": data_asof,
    }


def embed_header_csv(path: Path, header: dict[str, str], frame: pd.DataFrame) -> None:
    """写 CSV：首行 ``# k=v; k=v; …`` 元数据头，随后正常表体。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = "; ".join(f"{k}={v}" for k, v in header.items())
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(f"# {line}\n")
        frame.to_csv(handle, index=False)


def parse_header_csv(path: Path) -> dict[str, str]:
    """读回 CSV 首行元数据头（无头则返回空字典）。"""
    with open(path, "r", encoding="utf-8") as handle:
        first = handle.readline().strip()
    if not first.startswith("#"):
        return {}
    header: dict[str, str] = {}
    for item in first.lstrip("# ").split(";"):
        if "=" in item:
            key, value = item.split("=", 1)
            header[key.strip()] = value.strip()
    return header


def write_parquet_with_meta(path: Path, frame: pd.DataFrame, header: dict[str, str]) -> None:
    """用 pyarrow 写 parquet 并携带 key-value 元数据头。"""
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(frame, preserve_index=False)
    meta = dict(table.schema.metadata or {})
    meta.update({key.encode(): str(value).encode() for key, value in header.items()})
    pq.write_table(table.replace_schema_metadata(meta), path)


def validate_freshness(
    data_asof: str,
    *,
    ref_date: str | None = None,
    max_age_days: int = DATA_ASOF_MAX_AGE_DAYS,
) -> tuple[bool, str]:
    """新鲜度校验：``data_asof`` 不得早于 ``ref_date`` 的 ``max_age_days`` 天。

    返回 ``(可用与否, 原因)``。B 侧写入前与 A 侧读取共用同一规则。
    """
    try:
        asof = pd.Timestamp(data_asof)
    except Exception:
        return False, f"无法解析 data_asof={data_asof!r}"
    ref = pd.Timestamp(ref_date or today_iso())
    age = int((ref - asof).days)
    if age < 0:
        return False, f"data_asof 晚于参考日期（{data_asof} > {ref.date()}），疑似时钟异常"
    if age > max_age_days:
        return False, f"data_asof={data_asof} 早于 {ref.date()} 达 {age} 天，超过 {max_age_days} 天阈值"
    return True, f"data_asof={data_asof} 新鲜（{age} 天，阈值 {max_age_days} 天）"


def embed_header_any(
    path: Path,
    header: dict[str, str],
    frame: pd.DataFrame | dict[str, Any] | None,
) -> None:
    """按文件后缀选择写入方式（csv / parquet / json）。"""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("CSV 写入需要 pandas.DataFrame")
        embed_header_csv(path, header, frame)
    elif suffix == ".parquet":
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("parquet 写入需要 pandas.DataFrame")
        write_parquet_with_meta(path, frame, header)
    elif suffix == ".json":
        import json

        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(header)
        if isinstance(frame, dict):
            payload.update(frame)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    else:
        raise ValueError(f"不支持的文件后缀：{suffix}")
