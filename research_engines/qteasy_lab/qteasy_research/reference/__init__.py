"""项目B 参考维度供应层（动态 Beta / 宏观压力 / 通用参考维度输出）。

向系统A单向输出通用数学参考数据（decision_ref_package），供人工参考；
机器输出不含系统A策略名，全部维度 ``approval_policy="REFERENCE_ONLY"``。
"""

from __future__ import annotations

from qteasy_research.reference.config import (
    BACKTEST_REPORT_DIR,
    BENCHMARKS,
    DATA_ASOF_MAX_AGE_DAYS,
    GRID_REFERENCE_PATH,
    HEARTBEAT_MAX_AGE_DAYS,
    INTEGRATION_DIR,
    OUTPUTS_DIR,
    STRATEGY_CONTRACT_PATH,
    SYSTEM_A_ROOT,
)
from qteasy_research.reference.duration_phase import build_duration_phase
from qteasy_research.reference.grid_reference import build_grid_reference
from qteasy_research.reference.red_flag import assess_red_flags, load_risk_thresholds
from qteasy_research.reference.hedge_efficiency import build_hedge_efficiency
from qteasy_research.reference.human_machine_compare import (
    b_signal,
    build_hmc_report,
    classify_direction,
    monthly_returns_from_prices,
    parse_human_override_log,
    post_intervention_performance,
    render_hmc_markdown,
    weight_delta,
)
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
from qteasy_research.reference.param_sweep import round_trip_cost_bps, sweep_spread_grid
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
from qteasy_research.reference.stress_simulator import build_stress_simulator
from qteasy_research.reference.volatility_cone import (
    build_volatility_cone,
    current_vol_rank,
    suggest_reference_spread,
)
# ---- 阶段五：组合分析（L3/L4） ----
from qteasy_research.reference.portfolio_analysis import (
    APPROVAL_POLICY,
    DEFAULT_FACTORS,
    PORTFOLIO_ANALYSIS_DIR,
    PORTFOLIO_ANALYSIS_SCHEMA,
    align_close_panel,
    analyze_portfolio,
    assemble_exposure_matrix,
    asset_risk_contributions,
    build_macro_factor_returns,
    build_price_factor_returns,
    correlation_matrix,
    daily_returns_panel,
    efficient_frontier,
    equal_weight_weights,
    factor_covariance,
    factor_model_cov,
    factor_risk_contributions,
    idiosyncratic_variance,
    inverse_vol_weights,
    max_drawdown,
    portfolio_factor_exposure,
    portfolio_return,
    portfolio_vol,
    render_summary_md,
    result_to_dict,
    risk_parity_weights,
    strategy_analysis_inputs,
    write_outputs,
)
# ---- 阶段一：因子 tear sheet / 阶段四：宏观监控 / 策略回测（补齐导出） ----
from qteasy_research.reference.backtest_engine import (
    BacktestContract,
    BacktestResult,
    ContractAsset,
    ContractStrategy,
    grid_target_weight,
    load_price_frames,
    parse_contract,
    render_markdown,
    run_backtest,
    write_backtest_outputs,
)
from qteasy_research.reference.factor_tear import (
    FACTOR_IDS,
    build_tear_sheet,
    compute_factor_panel_from_csv,
    compute_forward_returns,
    cross_sectional_ic_series,
    load_factor_panel_from_parquet,
    load_price_panels,
    quantile_returns,
    run_tear_sheets,
    turnover,
)
from qteasy_research.reference.macro_monitoring import (
    CORR_MIN_OVERLAP,
    CORRELATION_WINDOW_DAYS,
    HIGH_CORR_THRESHOLD,
    STRESS_WINDOW_MONTHS,
    build_correlation,
    build_monthly_returns,
    build_stress_scenarios,
    compute_macro_fitness,
    current_macro_state,
    load_monitoring_frames,
    render_correlation_md,
    render_macro_fitness_md,
    render_stress_md,
    run_macro_monitoring,
    write_correlation,
    write_macro_fitness,
    write_stress,
)

__all__ = [
    # config 常量
    "BACKTEST_REPORT_DIR", "BENCHMARKS", "DATA_ASOF_MAX_AGE_DAYS",
    "GRID_REFERENCE_PATH", "HEARTBEAT_MAX_AGE_DAYS",
    "INTEGRATION_DIR", "OUTPUTS_DIR", "STRATEGY_CONTRACT_PATH", "SYSTEM_A_ROOT",
    # duration_phase（阶段二）
    "build_duration_phase",
    # grid_reference（B1-1）
    "build_grid_reference",
    # hedge_efficiency（B1-2）
    "build_hedge_efficiency",
    # human_machine_compare（人机对比月报）
    "b_signal", "build_hmc_report", "classify_direction", "monthly_returns_from_prices",
    "parse_human_override_log", "post_intervention_performance", "render_hmc_markdown",
    "weight_delta",
    # metadata
    "build_header", "embed_header_csv", "embed_header_any", "now_iso",
    "parse_header_csv", "today_iso", "validate_freshness", "write_parquet_with_meta",
    # param_sweep（阶段三）
    "round_trip_cost_bps", "sweep_spread_grid",
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
    # stress_simulator（阶段三）
    "build_stress_simulator",
    # trader_fingerprint（B1-3）
    "analyze_trader_fingerprint", "render_fingerprint_markdown",
    # volatility_cone
    "build_volatility_cone", "current_vol_rank", "suggest_reference_spread",
    # portfolio_analysis（阶段五 L3/L4）
    "APPROVAL_POLICY", "DEFAULT_FACTORS", "PORTFOLIO_ANALYSIS_DIR",
    "PORTFOLIO_ANALYSIS_SCHEMA",
    "align_close_panel", "analyze_portfolio", "assemble_exposure_matrix",
    "asset_risk_contributions", "build_macro_factor_returns",
    "build_price_factor_returns", "correlation_matrix", "daily_returns_panel",
    "efficient_frontier", "equal_weight_weights", "factor_covariance",
    "factor_model_cov", "factor_risk_contributions", "idiosyncratic_variance",
    "inverse_vol_weights", "max_drawdown", "portfolio_factor_exposure",
    "portfolio_return", "portfolio_vol", "render_summary_md", "result_to_dict",
    "risk_parity_weights", "strategy_analysis_inputs", "write_outputs",
    # backtest_engine / factor_tear / macro_monitoring（补齐导出）
    "BacktestContract", "BacktestResult", "ContractAsset", "ContractStrategy",
    "grid_target_weight", "load_price_frames", "parse_contract", "render_markdown",
    "run_backtest", "write_backtest_outputs",
    "FACTOR_IDS", "build_tear_sheet", "compute_factor_panel_from_csv",
    "compute_forward_returns", "cross_sectional_ic_series",
    "load_factor_panel_from_parquet", "load_price_panels", "quantile_returns",
    "run_tear_sheets", "turnover",
    "CORR_MIN_OVERLAP", "CORRELATION_WINDOW_DAYS", "HIGH_CORR_THRESHOLD",
    "STRESS_WINDOW_MONTHS", "build_correlation", "build_monthly_returns",
    "build_stress_scenarios", "compute_macro_fitness", "current_macro_state",
    "load_monitoring_frames", "render_correlation_md", "render_macro_fitness_md",
    "render_stress_md", "run_macro_monitoring", "write_correlation",
    "write_macro_fitness", "write_stress",
]
