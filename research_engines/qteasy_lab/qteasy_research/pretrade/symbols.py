"""证券代码标准化和本地可用标的识别。"""

from __future__ import annotations

import re
from typing import Any

from qteasy_research.pretrade.schemas import AssetIdentity


_CODE_RE = re.compile(r"^(?P<code>\d{6})(?:[.]?(?P<exchange>SH|SZ|BJ))?$", re.I)


def normalize_code(value: str) -> tuple[str, str | None]:
    """把纯数字或带后缀代码统一为 ``123456.SH``。"""
    raw = value.strip().upper().replace(" ", "")
    match = _CODE_RE.match(raw)
    if not match:
        raise ValueError(f"无法识别证券代码：{value!r}")

    code = match.group("code")
    exchange = match.group("exchange")
    if exchange is None:
        if code.startswith(("51", "56", "58", "60", "68")):
            exchange = "SH"
        elif code.startswith(("15", "16", "00", "30")):
            exchange = "SZ"
        elif code.startswith(("83", "87", "88")):
            exchange = "BJ"
    return f"{code}.{exchange}" if exchange else code, exchange


def infer_asset_type(code: str, *, in_fund_basic: bool = False) -> str:
    numeric = code.split(".", 1)[0]
    if in_fund_basic or numeric.startswith(("15", "16", "51", "56", "58")):
        return "ETF"
    if numeric.startswith(("60", "68", "00", "30", "83", "87", "88")):
        return "STOCK"
    return "UNKNOWN"


def resolve_identity(
    raw_code: str,
    *,
    fund_metadata: dict[str, dict[str, Any]] | None = None,
    stock_metadata: dict[str, dict[str, Any]] | None = None,
) -> AssetIdentity:
    code, exchange = normalize_code(raw_code)
    fund_metadata = fund_metadata or {}
    stock_metadata = stock_metadata or {}
    metadata = fund_metadata.get(code) or stock_metadata.get(code) or {}
    asset_type = infer_asset_type(code, in_fund_basic=code in fund_metadata)
    name = metadata.get("name") or metadata.get("fullname")
    return AssetIdentity(
        code=code,
        asset_type=asset_type,
        exchange=exchange,
        name=name,
        benchmark=metadata.get("benchmark"),
        metadata=metadata,
        resolved=bool(metadata) or asset_type != "UNKNOWN",
    )
