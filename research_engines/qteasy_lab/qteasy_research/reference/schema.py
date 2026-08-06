"""决策参考数据包（``decision_ref_package.json``）的数据结构。

``approval_policy`` 固定为 ``REFERENCE_ONLY``：所有维度仅供人工参考，
风险型字段需桌面端人工批准后才可进入系统A交易参数，**永不自动 APPROVED**。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from qteasy_research.reference.config import SCHEMA_VERSION


@dataclass
class AssetDimensions:
    """单资产的全维度参考数据（与具体策略解耦的通用数学维度）。"""

    asset_id: str
    name: str | None = None
    # 年化波动率（各窗口当前值）
    volatility: dict[str, float] = field(default_factory=dict)
    # 当前波动率在历史分布中的百分位（0~1）
    volatility_percentile: dict[str, float] = field(default_factory=dict)
    # 波动率锥：{"60d": {"p5":…, "p50":…, "p95":…}}
    cone: dict[str, dict[str, float]] = field(default_factory=dict)
    # 区间收益
    returns: dict[str, float] = field(default_factory=dict)
    var95: float | None = None
    es95: float | None = None
    max_drawdown: float | None = None
    # 动态 Beta：{"SPY": {"20d":…, "60d":…}, "000300.SH": {…}}
    beta: dict[str, dict[str, float]] = field(default_factory=dict)
    # 滚动 Beta 时间序列（可选，控制包体积，仅保留 latest N 点）
    rolling_beta_series: dict[str, list[dict]] = field(default_factory=dict)
    # 宏观压力测试（B1-2）：{"rate_up": {pnl_pct, corr, sample_count, …}}
    macro_stress: dict[str, dict] = field(default_factory=dict)
    # 估值分位（如可用；当前阶段可留空）
    valuation: dict[str, float] | None = None
    # 风险红牌建议（阶段二启用，先留空；approval_required=true）
    red_flag: dict | None = None
    data_quality: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "name": self.name,
            "volatility": self.volatility,
            "volatility_percentile": self.volatility_percentile,
            "cone": self.cone,
            "returns": self.returns,
            "var95": self.var95,
            "es95": self.es95,
            "max_drawdown": self.max_drawdown,
            "beta": self.beta,
            "rolling_beta_series": self.rolling_beta_series,
            "macro_stress": self.macro_stress,
            "valuation": self.valuation,
            "red_flag": self.red_flag,
            "data_quality": self.data_quality,
        }


@dataclass
class DecisionRefPackage:
    """参考数据包：包级元数据头 + 资产维度列表。"""

    generated_date: str
    data_asof: str
    assets: list[AssetDimensions] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    source_system: str = "systemB"
    approval_policy: str = "REFERENCE_ONLY"
    macro_regime: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_system": self.source_system,
            "generated_date": self.generated_date,
            "data_asof": self.data_asof,
            "approval_policy": self.approval_policy,
            "macro_regime": self.macro_regime,
            "assets": [asset.to_dict() for asset in self.assets],
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DecisionRefPackage":
        return cls(
            generated_date=str(payload.get("generated_date", "")),
            data_asof=str(payload.get("data_asof", "")),
            assets=[AssetDimensions(**asset) for asset in payload.get("assets", [])],
            schema_version=str(payload.get("schema_version", SCHEMA_VERSION)),
            source_system=str(payload.get("source_system", "systemB")),
            approval_policy=str(payload.get("approval_policy", "REFERENCE_ONLY")),
            macro_regime=dict(payload.get("macro_regime", {})),
            warnings=list(payload.get("warnings", [])),
        )
