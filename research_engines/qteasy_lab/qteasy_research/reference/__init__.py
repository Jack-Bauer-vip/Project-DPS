"""项目B 参考维度供应层（动态 Beta / 宏观压力 / 通用参考维度输出）。

向系统A单向输出通用数学参考数据（decision_ref_package），供人工参考；
机器输出不含系统A策略名，全部维度 ``approval_policy="REFERENCE_ONLY"``。
"""

from __future__ import annotations

from qteasy_research.reference.config import (
    BENCHMARKS,
    DATA_ASOF_MAX_AGE_DAYS,
    HEARTBEAT_MAX_AGE_DAYS,
    INTEGRATION_DIR,
    OUTPUTS_DIR,
    SYSTEM_A_ROOT,
)
from qteasy_research.reference.duration_phase import build_duration_phase
from qteasy_research.reference.grid_reference import build_grid_reference
from qteasy_research.reference.red_flag import assess_red_flags, load_risk_thresholds
from qteasy_research.reference.hedge_efficiency import build_hedge_efficiency
from qteasy_research.reference.metadata import (
    build_header,
    embed_header_csv,
    embed_header_any,
    now_iso,
    parse_header_csv,
    today_iso,
    validate_freshness,
    write_parquet_with_meta,
)
from qteasy_research.reference.pipeline import run_pipeline
from qteasy_research.reference.rolling_beta import (
    multi_benchmark_beta,
    rolling_beta,
    rolling_beta_summary,
)
from qteasy_research.reference.schema import AssetDimensions, DecisionRefPackage
from qteasy_research.reference.shared_dir import (
    IntegrationDir,
    IntegrationDirMissing,
)
from qteasy_research.reference.trader_fingerprint import (
    analyze_trader_fingerprint,
    render_fingerprint_markdown,
)
from qteasy_research.reference.volatility_cone import (
    build_volatility_cone,
    current_vol_rank,
    suggest_reference_spread,
)

__all__ = [
    # config 常量
    "BENCHMARKS", "DATA_ASOF_MAX_AGE_DAYS", "HEARTBEAT_MAX_AGE_DAYS",
    "INTEGRATION_DIR", "OUTPUTS_DIR", "SYSTEM_A_ROOT",
    # duration_phase（阶段二）
    "build_duration_phase",
    # grid_reference（B1-1）
    "build_grid_reference",
    # hedge_efficiency（B1-2）
    "build_hedge_efficiency",
    # metadata
    "build_header", "embed_header_csv", "embed_header_any", "now_iso",
    "parse_header_csv", "today_iso", "validate_freshness", "write_parquet_with_meta",
    # pipeline
    "run_pipeline",
    # red_flag（阶段二）
    "assess_red_flags", "load_risk_thresholds",
    # rolling_beta
    "multi_benchmark_beta", "rolling_beta", "rolling_beta_summary",
    # schema
    "AssetDimensions", "DecisionRefPackage",
    # shared_dir
    "IntegrationDir", "IntegrationDirMissing",
    # trader_fingerprint（B1-3）
    "analyze_trader_fingerprint", "render_fingerprint_markdown",
    # volatility_cone
    "build_volatility_cone", "current_vol_rank", "suggest_reference_spread",
]
