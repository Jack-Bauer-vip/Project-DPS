"""投前研究引擎的稳定数据结构。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ResearchStatus(StrEnum):
    IDENTIFYING = "IDENTIFYING"
    FETCHING_DATA = "FETCHING_DATA"
    DATA_QUALITY_CHECK = "DATA_QUALITY_CHECK"
    QUANTITATIVE_ANALYSIS = "QUANTITATIVE_ANALYSIS"
    INSTRUMENT_ANALYSIS = "INSTRUMENT_ANALYSIS"
    ONLINE_RESEARCH = "ONLINE_RESEARCH"
    REPORTING = "REPORTING"
    REVIEW = "REVIEW"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ResearchProjectStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    CLOSED = "CLOSED"
    ARCHIVED = "ARCHIVED"


class ResearchProjectType(StrEnum):
    ASSET_PROFILE = "ASSET_PROFILE"
    STRATEGY_PORTFOLIO = "STRATEGY_PORTFOLIO"


@dataclass
class AssetIdentity:
    code: str
    asset_type: str = "UNKNOWN"
    exchange: str | None = None
    name: str | None = None
    benchmark: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    resolved: bool = False


@dataclass
class ResearchConfig:
    code: str
    benchmark: str | None = None
    horizon: str = "medium"
    force_refresh: bool = False
    llm_provider: str | None = None
    llm_model: str | None = None
    network_research: bool = True
    output_dir: str | None = None
    project_id: str | None = None
    update_policy: str = "reuse"
    data_mode: str = "direct"
    evidence_research: bool = True
    local_data_provider: str | None = None
    indicator_config: dict[str, Any] = field(default_factory=dict)
    as_of_date: str | None = None
    factor_params: dict[str, Any] = field(default_factory=dict)
    transaction_cost: dict[str, Any] = field(default_factory=dict)


@dataclass
class StageRecord:
    name: str
    status: str
    message: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResearchRunResult:
    run_id: str
    research_case_id: str
    asset_identity: AssetIdentity
    project_id: str | None = None
    parent_run_id: str | None = None
    version_no: int = 1
    is_frozen: bool = False
    deleted_at: str | None = None
    deleted_by: str | None = None
    delete_reason: str | None = None
    run_status: str = ResearchStatus.IDENTIFYING.value
    data_as_of: str | None = None
    source_status: list[dict[str, Any]] = field(default_factory=list)
    quantitative_metrics: dict[str, Any] = field(default_factory=dict)
    benchmark_analysis: dict[str, Any] = field(default_factory=dict)
    instrument_analysis: dict[str, Any] = field(default_factory=dict)
    macro_analysis: dict[str, Any] = field(default_factory=dict)
    portfolio_fit: dict[str, Any] = field(default_factory=dict)
    strategy_fit: dict[str, Any] = field(default_factory=dict)
    risks: list[dict[str, Any]] = field(default_factory=list)
    hard_blocks: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    missing_items: list[str] = field(default_factory=list)
    stages: list[StageRecord] = field(default_factory=list)
    report: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)
    cache_status: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchReportDraft:
    draft_id: str
    run_id: str
    project_id: str | None = None
    manual_summary: str = ""
    manual_conclusion: str = ""
    risk_judgment: str = ""
    falsification_conditions: str = ""
    followup_plan: str = ""
    author: str = "user"
    status: str = "DRAFT"
    created_at: str | None = None
    updated_at: str | None = None
    deleted_at: str | None = None


@dataclass
class ResearchExportResult:
    run_id: str
    paths: dict[str, str] = field(default_factory=dict)
    formats_requested: list[str] = field(default_factory=list)
    formats_generated: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    generated_at: str | None = None


@dataclass
class ResearchProject:
    project_id: str
    name: str
    code: str
    objective: str | None = None
    horizon: str = "medium"
    project_type: str = ResearchProjectType.ASSET_PROFILE.value
    strategy_name: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    status: str = ResearchProjectStatus.DRAFT.value
    created_at: str | None = None
    updated_at: str | None = None
    closed_at: str | None = None
    archived_at: str | None = None


@dataclass
class ResearchNote:
    note_id: str
    project_id: str
    content: str
    title: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    deleted_at: str | None = None


@dataclass
class ResearchDecision:
    decision_id: str
    project_id: str
    content: str
    decision_type: str = "manual_confirmation"
    author: str = "user"
    created_at: str | None = None
    updated_at: str | None = None
    deleted_at: str | None = None


@dataclass
class ResearchAssetReference:
    reference_id: str
    project_id: str
    asset_project_id: str
    asset_code: str
    asset_version_no: int | None = None
    role: str = "candidate"
    weight_limit: float | None = None
    created_at: str | None = None
    deleted_at: str | None = None


@dataclass
class AssetProfile:
    """一个标的唯一的基础档案；组合只引用它，不复制固定资料。"""

    code: str
    name: str | None = None
    asset_type: str = "UNKNOWN"
    exchange: str | None = None
    benchmark: str | None = None
    fixed_metadata: dict[str, Any] = field(default_factory=dict)
    source: str | None = None
    as_of: str | None = None
    profile_version: int = 1
    collected_at: str | None = None


@dataclass
class AssetDynamicSnapshot:
    code: str
    run_id: str
    as_of: str | None
    quantitative_metrics: dict[str, Any] = field(default_factory=dict)
    benchmark_analysis: dict[str, Any] = field(default_factory=dict)
    cache_status: dict[str, Any] = field(default_factory=dict)


@dataclass
class AssetFactorResult:
    code: str
    horizon: str
    as_of: str | None
    formula_version: str
    factors: list[dict[str, Any]] = field(default_factory=list)
    composite_score: float | None = None
    state: str = "unavailable"
    consistency: str = "unavailable"
    summary: str = ""


@dataclass
class PortfolioAssetContext:
    context_id: str
    project_id: str
    code: str
    horizon: str = "medium"
    weight: float = 0.0
    role: str = "candidate"
    buy_condition: str = ""
    sell_condition: str = ""
    take_profit_condition: str = ""
    stop_loss_condition: str = ""
    hypothesis: str = ""
    enabled: bool = True
    created_at: str | None = None
    updated_at: str | None = None
    deleted_at: str | None = None


@dataclass
class DataQualityMetadata:
    """因子观测的可获得性和可信度元数据。

    ``available_at`` 是研究回放的关键字段：回测只能使用该时间点已经发布
    的观测，不能只看 observation_date。
    """

    source: str = ""
    observation_date: str | None = None
    release_date: str | None = None
    available_at: str | None = None
    revision_id: str | None = None
    vintage_id: str | None = None
    completeness: float | None = None
    publish_lag: int | None = None
    revision_frequency: str | None = None
    quality_level: str = "C"
    quality_score: float | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class TransactionCostConfig:
    """可配置的研究成本模型，不将股票税费写死在指标代码中。"""

    commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.0005
    half_spread_rate: float = 0.0002
    impact_coefficient: float = 0.0005
    min_order_value: float = 0.0
    lot_size: int = 1
    etf_stamp_tax: bool = False


@dataclass
class FactorDefinition:
    factor_id: str
    name: str
    category: str
    hypothesis: str
    formula: str
    source: str = ""
    raw_fields: list[str] = field(default_factory=list)
    frequency: str = "daily"
    availability: str = ""
    horizons: list[str] = field(default_factory=list)
    expected_direction: str = "positive"
    formula_version: str = "factor-v1"
    enabled: bool = True


@dataclass
class FactorResearchResult:
    factor_id: str
    horizon: str
    formula_version: str
    sample_count: int = 0
    ic: float | None = None
    rank_ic: float | None = None
    icir: float | None = None
    group_spread: float | None = None
    gross_return: float | None = None
    net_return: float | None = None
    turnover: float | None = None
    max_drawdown: float | None = None
    quality_level: str = "C"
    status: str = "insufficient_data"
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class AssetFactorExposure:
    asset_code: str
    factor_id: str
    horizon: str
    exposure_direction: str = "unknown"
    exposure_strength: float | None = None
    rolling_beta: float | None = None
    conditional_return_difference: float | None = None
    rank_group_spread: float | None = None
    stability_score: float | None = None
    data_confidence: float | None = None
    status: str = "insufficient_data"
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class FactorMatchResult:
    asset_code: str
    horizon: str
    score: float | None = None
    state: str = "unavailable"
    regime: str = "unknown"
    supporting_factors: list[dict[str, Any]] = field(default_factory=list)
    conflicting_factors: list[dict[str, Any]] = field(default_factory=list)
    missing_items: list[str] = field(default_factory=list)
    decay_warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
