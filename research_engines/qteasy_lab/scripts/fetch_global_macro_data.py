"""Fetch and normalize the small global macro/ETF research data set."""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests


FRED_GRAPH_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
REQUEST_HEADERS = {"User-Agent": "qteasy-research/0.1"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载全球宏观与代表性 ETF 数据到本地快照")
    parser.add_argument("--config", type=Path, default=Path("data/raw/global_data_sources.json"))
    parser.add_argument("--start", default="2010-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    parser.add_argument("--series", nargs="*", default=None)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument(
        "--prefer-network", action="store_true",
        help="即使存在本地 CSV 也强制走网络抓取（用于生成新数据快照）",
    )
    parser.add_argument(
        "--local-only", action="store_true",
        help="离线模式：仅使用本地 CSV，缺失即失败，不联网",
    )
    args = parser.parse_args(argv)
    if args.local_only and args.prefer_network:
        parser.error("--local-only 与 --prefer-network 不能同时使用")
    return args


def _next_business_day(value: str) -> str:
    return (pd.Timestamp(value) + pd.offsets.BDay(1)).date().isoformat()


def _next_calendar_day(value: str) -> str:
    return (pd.Timestamp(value) + timedelta(days=1)).date().isoformat()


def _request_json(url: str, *, params: dict[str, Any], timeout: int) -> dict[str, Any]:
    response = requests.get(url, params=params, timeout=timeout, headers=REQUEST_HEADERS)
    response.raise_for_status()
    return response.json()


def _fetch_fred(item: dict[str, Any], *, start: str, end: str, timeout: int) -> tuple[pd.DataFrame, bytes, str, list[str]]:
    api_key = os.getenv("FRED_API_KEY")
    if api_key:
        payload = _request_json(
            FRED_API_URL,
            params={
                "series_id": item["api_code"],
                "api_key": api_key,
                "file_type": "json",
                "observation_start": start,
                "observation_end": end,
            },
            timeout=timeout,
        )
        frame = pd.DataFrame(payload.get("observations", []))
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        quality = item.get("quality_level", "A")
        # 不带 realtime_start 参数查询最新版时，FRED 会把全部观测的 realtime_start
        # 回填为查询日（表示"属于当前实时版本"），并不是该观测可被获取的时点。
        # 若直接用作 available_at，会导致历史观测在点内过滤时全部失效。
        # 统一采用观测日的下一工作日作为可用时点，与 graph 分支和本地 CSV 合成一致。
        if "date" in frame.columns:
            frame["available_at"] = frame["date"].map(_next_business_day)
        elif "realtime_start" in frame.columns:
            frame["available_at"] = frame["realtime_start"]
    else:
        response = requests.get(
            FRED_GRAPH_URL,
            params={"id": item["api_code"], "cosd": start, "coed": end},
            timeout=timeout,
            headers=REQUEST_HEADERS,
        )
        response.raise_for_status()
        raw = response.content
        frame = pd.read_csv(pd.io.common.BytesIO(raw))
        frame = frame.rename(columns={frame.columns[0]: "date", frame.columns[1]: "value"})
        frame["available_at"] = frame["date"].map(_next_business_day)
        quality = "B"
    frame = frame.rename(columns={"date": "observation_date"})
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna(subset=["observation_date", "value"])
    frame["quality_level"] = quality
    return frame[["observation_date", "available_at", "value", "quality_level"]], raw, quality, []


def _fetch_fred_local_csv(
    item: dict[str, Any], csv_path: Path, *, end: str
) -> tuple[pd.DataFrame, None, str, list[str]]:
    """从手动下载的 FRED CSV 导入本地快照。

    返回与 _fetch_fred 相同的四元组，raw 恒为 None（手动 CSV 本身即为原始产物）。

    质量等级完全由 available_at 档位推导，不信任文件自带的 quality_level 列：
    - 文件含 available_at 列 → 原样使用，质量取配置值（A）；
    - 仅含 realtime_start → 派生 available_at，质量降为 B 并记录警告；
    - 都没有 → 按下个工作日合成 available_at，质量降为 C 并记录警告。
    任何缺少 available_at 的手动文件都必须降级并记录警告，不能无提示地视为官方实时快照。
    """
    warnings: list[str] = []
    try:
        frame = pd.read_csv(csv_path, encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"未找到本地 CSV：{csv_path}") from exc
    except (OSError, pd.errors.ParserError) as exc:
        raise ValueError(f"{csv_path.name} 无法解析：{exc}") from exc
    if frame.empty:
        raise ValueError(f"{csv_path.name} 为空文件，没有数据行")

    # 日期列归一化：observation_date → DATE → date
    date_col = next((col for col in ("observation_date", "DATE", "date") if col in frame.columns), None)
    if date_col is None:
        raise ValueError(f"{csv_path.name} 缺少日期列（支持 observation_date/DATE/date）")
    frame["observation_date"] = pd.to_datetime(frame[date_col], errors="coerce")

    # 值列检测：value → api_code（FRED graph CSV 的值列表头是系列名）→ 首个非元数据列
    meta_cols = {"observation_date", "DATE", "date", "available_at", "realtime_start", "realtime_end"}
    value_col = next(
        (col for col in ("value", item.get("api_code")) if col and col in frame.columns),
        None,
    )
    if value_col is None:
        value_col = next((col for col in frame.columns if col not in meta_cols), None)
    if value_col is None:
        raise ValueError(f"{csv_path.name} 缺少数值列（支持 value/{item.get('api_code')}）")
    frame["value"] = pd.to_numeric(frame[value_col], errors="coerce")

    # 先剔除无效日期与数值，再合成 available_at，避免对 NaT 调用 _next_business_day 崩溃
    frame = frame.dropna(subset=["observation_date", "value"])

    # available_at 三档分档，同时决定质量等级与警告
    if "available_at" in frame.columns:
        frame["available_at"] = pd.to_datetime(frame["available_at"], errors="coerce")
        quality = item.get("quality_level", "A")
    elif "realtime_start" in frame.columns:
        frame["available_at"] = pd.to_datetime(frame["realtime_start"], errors="coerce")
        quality = "B"
        warnings.append(f"{csv_path.name} 缺少 available_at，已从 realtime_start 派生，质量等级降为 B。")
    else:
        frame["available_at"] = pd.to_datetime(
            frame["observation_date"].map(_next_business_day), errors="coerce"
        )
        quality = "C"
        warnings.append(f"{csv_path.name} 缺少 available_at，已按下个工作日合成，质量等级降为 C。")
    frame = frame.dropna(subset=["available_at"])

    # 只裁剪数据截至日期，不做起始下限裁剪，保留更早历史以增加月末重采样的样本量
    frame = frame.loc[frame["observation_date"] <= pd.Timestamp(end)]
    frame = frame.sort_values("observation_date").drop_duplicates("observation_date", keep="last")
    if frame.empty:
        raise ValueError(f"{csv_path.name} 解析后没有 {end} 以前的有效数据行")

    frame["observation_date"] = frame["observation_date"].dt.strftime("%Y-%m-%d")
    frame["available_at"] = frame["available_at"].dt.strftime("%Y-%m-%d")
    frame["quality_level"] = quality
    return frame[["observation_date", "available_at", "value", "quality_level"]], None, quality, warnings


def _fetch_yahoo(item: dict[str, Any], *, start: str, end: str, timeout: int) -> tuple[pd.DataFrame, bytes, str, list[str]]:
    start_ts = int(pd.Timestamp(start, tz="UTC").timestamp())
    end_ts = int((pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)).timestamp())
    response = requests.get(
        YAHOO_CHART_URL.format(ticker=item["api_code"]),
        params={
            "period1": start_ts,
            "period2": end_ts,
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        },
        timeout=timeout,
        headers=REQUEST_HEADERS,
    )
    response.raise_for_status()
    raw = response.content
    payload = response.json()
    result = (payload.get("chart", {}).get("result") or [{}])[0]
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quote = ((indicators.get("adjclose") or [{}])[0].get("adjclose") or [])
    if not quote:
        quote = ((indicators.get("quote") or [{}])[0].get("close") or [])
    size = min(len(timestamps), len(quote))
    if size == 0:
        raise ValueError(f"Yahoo Finance 未返回 {item['series_id']} 的价格数据")
    frame = pd.DataFrame({
        "observation_date": pd.to_datetime(timestamps[:size], unit="s", utc=True).date.astype(str),
        "value": quote[:size],
    })
    frame["available_at"] = frame["observation_date"].map(_next_calendar_day)
    frame["quality_level"] = item.get("quality_level", "B")
    frame = frame.dropna(subset=["value"])
    return frame[["observation_date", "available_at", "value", "quality_level"]], raw, item.get("quality_level", "B"), []


def normalize_series(item: dict[str, Any], frame: pd.DataFrame, *, collected_at: str) -> pd.DataFrame:
    output = frame.copy()
    output["series_id"] = item["series_id"]
    output["unit"] = item["unit"]
    output["source"] = item["source"]
    output["collected_at"] = collected_at
    output["observation_date"] = pd.to_datetime(output["observation_date"], errors="coerce").dt.date.astype("string")
    output["available_at"] = pd.to_datetime(output["available_at"], errors="coerce").dt.date.astype("string")
    return output[["series_id", "observation_date", "available_at", "value", "unit", "source", "quality_level", "collected_at"]].sort_values("observation_date")


def fetch_all(args: argparse.Namespace) -> dict[str, Any]:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    wanted = set(args.series or [])
    items = [item for item in config["series"] if not wanted or item["series_id"] in wanted]
    raw_dir = args.output_dir / "raw" / "global_macro"
    processed_dir = args.output_dir / "processed" / "global_macro"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    collected_at = pd.Timestamp.now(tz="UTC").isoformat()
    manifest_path = processed_dir / "manifest.json"
    previous_results: dict[str, dict[str, Any]] = {}
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            previous_results = {
                str(item.get("series_id")): item
                for item in previous.get("results", [])
                if item.get("series_id")
            }
        except (OSError, json.JSONDecodeError):
            previous_results = {}
    results: list[dict[str, Any]] = []
    for item in items:
        try:
            csv_path = raw_dir / f"{item['series_id']}.csv"
            # 本地优先仅针对 FRED：存在本地 CSV 且未强制网络时从文件导入
            if item["source"] == "FRED" and csv_path.exists() and not args.prefer_network:
                frame, raw, quality, warnings = _fetch_fred_local_csv(item, csv_path, end=args.end)
                import_mode = "local_csv"
                source_file = str(csv_path)
            elif item["source"] == "FRED":
                if args.local_only:
                    raise FileNotFoundError(f"离线模式（--local-only）未找到本地 CSV：{csv_path}")
                frame, raw, quality, warnings = _fetch_fred(item, start=args.start, end=args.end, timeout=args.timeout)
                import_mode = "network"
                source_file = None
            elif item["source"] == "YahooFinance":
                if args.local_only:
                    raise FileNotFoundError(f"离线模式（--local-only）不支持 {item['series_id']} 的本地导入")
                frame, raw, quality, warnings = _fetch_yahoo(item, start=args.start, end=args.end, timeout=args.timeout)
                import_mode = "network"
                source_file = None
            else:
                raise ValueError(f"不支持的数据源：{item['source']}")
            # 手动 CSV 本身即原始产物，网络路径才写回 .raw 响应字节
            if raw is not None:
                (raw_dir / f"{item['series_id']}.raw").write_bytes(raw)
            # 本地导入按文件修改时间生成快照，网络抓取用本次运行时间
            series_collected_at = (
                pd.Timestamp.fromtimestamp(csv_path.stat().st_mtime, tz="UTC").isoformat()
                if import_mode == "local_csv" else collected_at
            )
            normalized = normalize_series(item, frame, collected_at=series_collected_at)
            normalized.to_csv(processed_dir / f"{item['series_id']}.csv", index=False, encoding="utf-8-sig")
            entry: dict[str, Any] = {
                "series_id": item["series_id"],
                "status": "success",
                "rows": len(normalized),
                "as_of": normalized["observation_date"].max() if not normalized.empty else None,
                "quality_level": quality,
                "import_mode": import_mode,
                "warnings": warnings,
            }
            if source_file:
                entry["source_file"] = source_file
            results.append(entry)
        except Exception as exc:
            failed = {"series_id": item["series_id"], "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            if previous_results.get(item["series_id"], {}).get("status") == "success":
                failed["last_success"] = previous_results[item["series_id"]]
            results.append(failed)
    manifest = {
        "collected_at": collected_at,
        "start": args.start,
        "end": args.end,
        "requested_series": [item["series_id"] for item in items],
        "results": results,
        "previous_results": previous_results,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(fetch_all(parse_args()), ensure_ascii=False, indent=2))
