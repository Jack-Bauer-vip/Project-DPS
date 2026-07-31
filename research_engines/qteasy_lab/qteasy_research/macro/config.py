"""
宏观因子配置与参数管理。

所有可调参数集中在此文件，便于回测优化时批量修改。
"""

from __future__ import annotations

from pathlib import Path

# ============================================================
# 数据路径
# ============================================================

# 宏观数据本地存储路径（CSV文件，每个因子一个）
MACRO_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "macro"

# ============================================================
# 因子定义
# ============================================================
# 每个因子包含：
#   name       : 内部标识名
#   label      : 中文显示名
#   api_func   : AKShare 接口函数名（字符串）
#   frequency  : 原始数据频率（'monthly' / 'daily'）
#   direction  : 预期影响方向（'positive'=数值越高对风险资产越有利）
#   unit       : 单位
#   category   : 因子类别（growth / inflation / rate / credit / currency / commodity）

FACTOR_DEFINITIONS = [
    {
        "name": "PMI",
        "label": "制造业PMI",
        "api_func": "macro_china_pmi",
        "frequency": "monthly",
        "direction": "positive",
        "unit": "%",
        "category": "growth",
    },
    {
        "name": "CPI",
        "label": "居民消费价格指数",
        "api_func": "macro_china_cpi",
        "frequency": "monthly",
        "direction": "negative",
        "unit": "%",
        "category": "inflation",
    },
    {
        "name": "PPI",
        "label": "工业生产者出厂价格指数",
        "api_func": "macro_china_ppi",
        "frequency": "monthly",
        "direction": "negative",
        "unit": "%",
        "category": "inflation",
    },
    {
        "name": "M1",
        "label": "货币供应量M1同比",
        "api_func": "macro_china_money_supply",
        "frequency": "monthly",
        "direction": "positive",
        "unit": "%",
        "category": "growth",
    },
    {
        "name": "CN10Y",
        "label": "中国10年期国债收益率",
        "api_func": "bond_zh_us_rate",
        "frequency": "daily",
        "direction": "negative",
        "unit": "%",
        "category": "rate",
    },
    {
        "name": "CN10Y2Y",
        "label": "中国国债期限利差(10Y-2Y)",
        "api_func": "bond_zh_us_rate",
        "frequency": "daily",
        "direction": "positive",
        "unit": "%",
        "category": "rate",
    },
    {
        "name": "SHIBOR3M",
        "label": "SHIBOR 3个月",
        "api_func": "macro_china_shibor_all",
        "frequency": "daily",
        "direction": "negative",
        "unit": "%",
        "category": "rate",
    },
    {
        "name": "COMMODITY",
        "label": "南华商品指数",
        "api_func": "macro_china_commodity_price_index",
        "frequency": "daily",
        "direction": "positive",
        "unit": "指数",
        "category": "commodity",
    },
    {
        "name": "GDP",
        "label": "国内生产总值(同比)",
        "api_func": "macro_china_gdp",
        "frequency": "monthly",
        "direction": "positive",
        "unit": "%",
        "category": "growth",
    },
    {
        "name": "LPR",
        "label": "贷款市场报价利率(LPR)",
        "api_func": "macro_china_lpr",
        "frequency": "monthly",
        "direction": "negative",
        "unit": "%",
        "category": "rate",
    },
]

# ============================================================
# 可调参数（评分系统 + 回测优化用）
# ============================================================

PARAMS = {
    # 回溯窗口（月数），用于 Z-score 计算
    "lookback": 24,
    # Z-score 状态划分阈值
    "z_threshold": 0.5,
    # 状态计算方法: "z_score" | "ma_cross"
    "state_method": "z_score",
    # 预测未来 N 个月
    "forward_period": 1,
    # 各因子权重（优化对象）
    "weights": {
        "PMI": 0.20,
        "CPI": 0.15,
        "PPI": 0.10,
        "M1": 0.10,
        "CN10Y": 0.15,
        "CN10Y2Y": 0.10,
        "SHIBOR3M": 0.05,
        "COMMODITY": 0.05,
        "GDP": 0.05,
        "LPR": 0.05,
    },
    # 概率生成方式: "historical_freq" | "logistic"
    "prob_method": "historical_freq",
    # 超配阈值（>threshold 超配，<1-threshold 低配）
    "decision_threshold": 0.60,
}

# 参数候选区间用于 walk-forward 实验，不自动选择历史收益最高的单点。
PARAM_CANDIDATES = {
    "lookback": [24, 36, 60],
    "z_threshold": [0.5, 0.75, 1.0],
    "forward_period": [1, 3, 6],
    "state_method": ["z_score", "quantile", "ma_cross"],
    "recession_window": [12, 24],
}

FACTOR_MONITORING_RULES = {
    "rolling_ic_window": 12,
    "ic_warning_std": 1.5,
    "negative_ic_streak": 6,
    "oos_sharpe_ratio_floor": 0.5,
    "minimum_oos_months_for_stable": 24,
    "vif_threshold": 10.0,
    "individual_weight_cap": 0.25,
}

DATA_QUALITY_LEVELS = {
    "A": "官方来源、发布时间明确、历史修订完整",
    "B": "权威第三方、发布时间基本完整",
    "C": "字段可用但存在缺失或修订不完整",
    "D": "仅供参考，不进入正式评分",
}

# ============================================================
# 资产池定义
# ============================================================

# 按 category 分组的默认资产
ASSET_POOL = {
    "stock": {
        "name": "沪深300",
        "etf_code": "510300.SH",
    },
    "bond": {
        "name": "国债ETF",
        "etf_code": "511010.SH",
    },
    "gold": {
        "name": "黄金ETF",
        "etf_code": "518880.SH",
    },
}


def get_factor_names() -> list[str]:
    """返回所有因子名称列表。"""
    return [f["name"] for f in FACTOR_DEFINITIONS]


def get_factor_config(name: str) -> dict | None:
    """按名称查找因子配置。"""
    for f in FACTOR_DEFINITIONS:
        if f["name"] == name:
            return f
    return None
