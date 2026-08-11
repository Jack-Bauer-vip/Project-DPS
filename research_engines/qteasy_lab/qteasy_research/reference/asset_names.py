"""系统A active 资产池展示名的 B 侧规范化。

背景（2026-08-11）：A 侧 ``config/asset_pool.csv`` 的 ``name`` 列存在历史截断与错字，
例如 ``稀有金属ETF嘉``（缺“实”）、``豆柏ETF华夏``（“柏”为“粕”之误）、
``标普500ETF南``（缺“方”）。B 侧不修改 A 配置（权限铁律），改在读取资产池时按
``asset_id`` 覆盖为完整正确的展示名，保证决策包 ``assets[].name`` 完整可读。

资产展示名允许中文（与“机器产出零中文策略名”纪律是两回事；本模块的代码标识、
字段名、日志保持 ASCII）。
"""

from __future__ import annotations

# 系统A active 资产池的规范展示名（asset_id -> 完整正确展示名）。
# 本表随 A 资产池变化维护；asset_id 未命中时透传 A 侧原始 name。
ASSET_DISPLAY_NAMES: dict[str, str] = {
    "562800.SH": "稀有金属ETF嘉实",      # 原"稀有金属ETF嘉"缺"实"
    "159985.SZ": "豆粕ETF华夏",          # 原"豆柏ETF华夏"错字（粕->柏）
    "588230.SH": "科创200ETF华泰柏瑞",   # 原"科创200ETF华"缺尾部（华泰柏瑞）
    "518880.SH": "黄金ETF华安易富",      # 原"黄金ETF华安"缺"易富"
    "159516.SZ": "半导体设备ETF",
    "515180.SH": "红利ETF易方达",
    "513050.SH": "中概互联网ETF",
    "512890.SH": "红利低波ETF华泰柏瑞",  # 原"红利低波ETF华"缺尾部（华泰柏瑞）
    "515450.SH": "红利低波50ETF",
    "513650.SH": "标普500ETF南方",       # 原"标普500ETF南"缺"方"
    "159941.SZ": "纳指ETF广发",
    "164824.SZ": "印度基金LOF",
    "513520.SH": "日经ETF华夏",
    "159131.SZ": "港股通信息技术",
}

# A 侧原始 name 的已知截断/错字形态（供测试断言规范表确实修正了这些值）。
# 注意：仅作测试参考；B 侧运行时不依赖 A 侧原始值（按 asset_id 覆盖）。
LEGACY_BAD_NAMES: dict[str, str] = {
    "562800.SH": "稀有金属ETF嘉",
    "159985.SZ": "豆柏ETF华夏",
    "588230.SH": "科创200ETF华",
    "518880.SH": "黄金ETF华安",
    "512890.SH": "红利低波ETF华",
    "513650.SH": "标普500ETF南",
}

# 已知错字子串（测试断言规范展示名不含错字清单）。
KNOWN_TYPO_SUBSTRINGS: tuple[str, ...] = ("豆柏",)


def resolve_display_name(asset_id: str, source_name: str | None) -> str | None:
    """按 ``asset_id`` 返回规范展示名；未命中时透传 A 侧原始 name。"""
    key = str(asset_id).strip()
    if key in ASSET_DISPLAY_NAMES:
        return ASSET_DISPLAY_NAMES[key]
    return source_name
