"""项目B参考维度供应层的路径与常量配置。

与 ``qteasy_research/config.py`` 分离：本模块持有系统A（``D:\\FF Project``）路径、
共享目录、心跳与通用参考维度相关的常量，避免 qteasy 初始化链路加载外部路径。
SYSTEM_A_ROOT 支持环境变量覆盖（便于测试隔离）。
"""

from __future__ import annotations

import os
from pathlib import Path

# 项目B 根目录（qteasy_lab）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ---- 系统A 路径（env 可覆盖；默认与系统A同机约定一致）----
SYSTEM_A_ROOT = Path(os.getenv("SYSTEM_A_ROOT", r"D:\FF Project"))
INTEGRATION_DIR = SYSTEM_A_ROOT / "data" / "integration"
SYSTEM_A_ASSET_POOL = SYSTEM_A_ROOT / "config" / "asset_pool.csv"
SYSTEM_A_TRADE_LEDGER = SYSTEM_A_ROOT / "config" / "actual_trade_ledger.csv"
SYSTEM_A_MANUAL_OVERRIDE = SYSTEM_A_ROOT / "config" / "manual_override.csv"
# 系统A按项目A第8节 A1-3 规划新增的人工干预审计日志（当前尚不存在，待A新增后纳入）。
SYSTEM_A_HUMAN_OVERRIDE_LOG = SYSTEM_A_ROOT / "data" / "logs" / "human_override_log.csv"
SYSTEM_A_RISK_PARAMS = SYSTEM_A_ROOT / "config" / "strategy_params.json"

# ---- 项目B 本地数据与产出 ----
SYSTEM_B_DATA_ROOT = PROJECT_ROOT / "data"
TRADER_FINGERPRINT_DIR = PROJECT_ROOT / "reports" / "trader_fingerprint"
OUTPUT_DIR = PROJECT_ROOT / "outputs"

# ---- 集成契约常量 ----
SCHEMA_VERSION = "1.0"
PIPELINE_VERSION = "0.1.0"
# 新鲜度阈值：data_asof 早于当前日期 2 天，系统A可丢弃。
DATA_ASOF_MAX_AGE_DAYS = 2
# 心跳阈值：系统A check_b_heartbeat() 以 3 天判断B是否在线，两侧对齐。
HEARTBEAT_MAX_AGE_DAYS = 3
HEARTBEAT_PATH = INTEGRATION_DIR / "b_heartbeat.json"

# ---- 参考维度窗口 ----
ROLLING_BETA_WINDOWS = (20, 60, 120, 252)
VOLATILITY_WINDOWS = (20, 60, 120, 252)
CONE_PERCENTILES = (5, 25, 50, 75, 95)

# 基准映射：B 本地可用的基准代码
# （000300.SH 在 index_daily；SPY/TLT/GLD 在 global_macro 标准化数据）。
BENCHMARKS = {
    "equity_cn": "000300.SH",
    "equity_global": "SPY",
    "bond_us": "TLT",
    "gold": "GLD",
}
