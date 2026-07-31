"""研究档案展示层的中文字段和常见枚举翻译。

原始数据字段保持 Tushare/CSV 的英文键名，只有报告和桌面端展示时使用本模块。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd


METADATA_FIELD_LABELS: dict[str, str] = {
    "ts_code": "标准代码",
    "name": "基金名称",
    "management": "基金管理人",
    "custodian": "基金托管人",
    "trustee": "受托机构",
    "fund_type": "基金分类",
    "type": "基金类型",
    "invest_type": "投资类型/底层资产",
    "benchmark": "跟踪基准/业绩比较基准",
    "found_date": "成立日期",
    "due_date": "到期日期",
    "issue_date": "发行日期",
    "list_date": "上市日期",
    "delist_date": "终止上市日期",
    "purc_startdate": "申购开始日期",
    "redm_startdate": "赎回开始日期",
    "issue_amount": "发行规模",
    "m_fee": "管理费率",
    "c_fee": "托管费率",
    "p_value": "基金面值",
    "min_amount": "最低申购金额",
    "duration_year": "存续期限（年）",
    "exp_return": "预期收益率",
    "status": "基金状态",
    "market": "交易市场/市场属性",
}

_DATE_FIELDS = {
    "found_date", "due_date", "issue_date", "list_date", "delist_date",
    "purc_startdate", "redm_startdate",
}
_PERCENT_FIELDS = {"m_fee", "c_fee", "exp_return"}
_STATUS_LABELS = {
    "L": "上市/正常交易（L）",
    "D": "终止/已清盘（D）",
    "I": "发行中（I）",
}
_MARKET_LABELS = {
    "E": "场内交易（E）",
    "O": "场外交易（O）",
}


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return str(value).strip().lower() in {"", "none", "nan", "nat", "null"}


def metadata_field_label(key: str) -> str:
    """返回基础资料字段的中文名称；未知字段保留原键名以便追溯。"""
    return METADATA_FIELD_LABELS.get(key, f"其他资料（{key}）")


def _format_date(value: Any) -> str:
    text = str(value).strip()
    if text.isdigit() and len(text) == 8:
        try:
            return datetime.strptime(text, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            pass
    parsed = pd.to_datetime(value, errors="coerce")
    return "未知" if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")


def metadata_value_label(key: str, value: Any) -> str:
    """按字段语义格式化基础资料值，避免把日期和费率原样展示。"""
    if _missing(value):
        return "未知"
    if key in _DATE_FIELDS:
        return _format_date(value)
    if key in _PERCENT_FIELDS:
        try:
            return f"{float(value):.2f}%"
        except (TypeError, ValueError):
            return str(value)
    if key == "status":
        return _STATUS_LABELS.get(str(value).upper(), str(value))
    if key == "market":
        return _MARKET_LABELS.get(str(value).upper(), str(value))
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return str(value)


def metadata_display_rows(metadata: dict[str, Any] | None) -> list[tuple[str, str]]:
    """按稳定的中文字段顺序生成展示行，未知字段放在最后。"""
    metadata = metadata or {}
    ordered_keys = [key for key in METADATA_FIELD_LABELS if key in metadata]
    ordered_keys.extend(sorted(key for key in metadata if key not in METADATA_FIELD_LABELS))
    return [
        (metadata_field_label(key), metadata_value_label(key, metadata.get(key)))
        for key in ordered_keys
        if not _missing(metadata.get(key)) and key not in {"ts_code", "name"}
    ]
