"""qteasy批量数据下载工具。"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

import pandas as pd


def download_symbols(
    api_function: Callable[..., pd.DataFrame],
    symbols: Sequence[str],
    start_date: str,
    end_date: str,
    *,
    pause_seconds: float = 0.2,
) -> tuple[pd.DataFrame, list[dict[str, str]]]:
    """
    逐个下载证券数据并合并。

    Parameters
    ----------
    api_function:
        例如 pro.index_daily、pro.fund_daily、pro.daily。
    symbols:
        证券代码列表。
    start_date:
        开始日期，YYYYMMDD。
    end_date:
        结束日期，YYYYMMDD。
    pause_seconds:
        每次请求之间的暂停时间。

    Returns
    -------
    合并后的DataFrame，以及失败记录列表。
    """

    frames: list[pd.DataFrame] = []
    failures: list[dict[str, str]] = []

    total = len(symbols)

    for position, symbol in enumerate(symbols, start=1):
        print(f"[{position}/{total}] 正在下载 {symbol}")

        try:
            data = api_function(
                ts_code=symbol,
                start_date=start_date,
                end_date=end_date,
            )

            if data is None or data.empty:
                failures.append(
                    {
                        "symbol": symbol,
                        "error": "返回数据为空",
                    }
                )
                continue

            required_columns = {"ts_code", "trade_date"}
            missing_columns = required_columns.difference(data.columns)

            if missing_columns:
                failures.append(
                    {
                        "symbol": symbol,
                        "error": (
                            f"缺少字段："
                            f"{sorted(missing_columns)}"
                        ),
                    }
                )
                continue

            frames.append(data)

        except Exception as exc:
            failures.append(
                {
                    "symbol": symbol,
                    "error": str(exc),
                }
            )

        if pause_seconds > 0:
            time.sleep(pause_seconds)

    if not frames:
        return pd.DataFrame(), failures

    combined = pd.concat(
        frames,
        ignore_index=True,
        copy=False,
    )

    combined = (
        combined
        .drop_duplicates(
            subset=["ts_code", "trade_date"],
            keep="last",
        )
        .sort_values(["ts_code", "trade_date"])
        .reset_index(drop=True)
    )

    return combined, failures