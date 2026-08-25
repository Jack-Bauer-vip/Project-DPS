"""策略级别回测引擎（阶段四）：基于 A 侧策略规则契约的轻量事件驱动模拟器。

- **只读** A 侧契约（``strategy_contract.json``）与 B 本地行情/宏观数据；
  **只写** B 本地 ``reports/backtest/``（M-003：不写共享目录、不写 A）。
- 引擎机制 = 轻量事件驱动模拟器（不复用 qteasy BacktestEngine）。
- 防未来函数硬约束：T 日收盘出信号 → T+1 日收盘成交；首日建仓按契约目标视为
  先验（T0 收盘成交）；宏观 phase 用 ``< T`` 的月末（point-in-time）。
- 四类规则：barbell / mid_line（+宏观 tilt）/ grid（网格中枢 max×0.5）/
  short_term（SKIPPED 占位）。
- 机器产出全 ASCII，``strategy_id``/``asset_id`` 标识，零中文策略名。
  所有 B 侧假设在报告 Assumptions 标注 ``B-side; awaiting A confirmation``。
"""

from __future__ import annotations

import json
import math
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qteasy_research.backtesting.metrics import MetricsResult, PerformanceMetrics
from qteasy_research.reference.asset_pool import align_pool_price_history
from qteasy_research.reference.config import (
    BENCHMARKS,
    GRID_REFERENCE_PATH,
    SYSTEM_B_DATA_ROOT,
)
from qteasy_research.reference.duration_phase import build_duration_phase
from qteasy_research.reference.macro_scenarios import build_monthly_scenario_table
from qteasy_research.reference.metadata import embed_header_csv, today_iso

# ============================================================
# 常量（集中定义，报告 Assumptions 引用 = 单一事实源）
# ============================================================

# 网格档距三级兜底：契约 suggested_spread → grid_reference_table → 此值。
DEFAULT_GRID_SPREAD = 0.05
# 网格中枢两侧档数（B侧假设；用户确认 A）。
GRID_LEVELS_PER_SIDE = 4
# mid_line 收紧期（phase=late）宏观倾斜偏移率（B侧假设）。
MACRO_TILT_RATE = 0.05
# 最小整手（买入向下取整；测试可注入 1）。
TRADE_LOT_SIZE = 100
# 回测窗口全历史起点（用户裁定：2018-01 至今）。
BACKTEST_START_DEFAULT = "2018-01-02"
# 未知 rebalance_frequency 兜底（design doc §3 约定）。
DEFAULT_REBALANCE_FREQ = "monthly"
# 再平衡阈值缺省（契约未提供时视为 0，即每次全量再平衡）。
DEFAULT_REBALANCE_THRESHOLD = 0.0

# 重平衡频率枚举（契约 v1.0 仅 weekly/daily；引擎容忍 monthly/quarterly/never）。
REBALANCE_FREQ_ALIASES = ("daily", "weekly", "monthly", "quarterly", "never")
# decision_rule 枚举（契约 v1.0 为超集，含 defensive）。
DECISION_RULES = ("barbell", "mid_line", "grid", "short_term", "defensive")
# mid_line 防御资产集（role 含 红利/低波/黄金/避险/债 或 asset_id 命中下表）。
DEFENSIVE_ASSETS = {"512890.SH", "518880.SH", "515180.SH", "515450.SH"}
_DEFENSIVE_KEYWORDS = ("红利", "低波", "黄金", "避险", "债", "defensive", "gold", "bond")

# 报告元数据头常量。
_SCHEMA_VERSION = "1.0"
_SOURCE_SYSTEM = "systemB"


# ============================================================
# 数据结构
# ============================================================

@dataclass(frozen=True)
class GridConfig:
    """契约嵌套 ``assets[].grid_config``（A 侧导出，两段步长网格参数）。

    - ``anchor_price``：网格中轴（60d VWAP/SMA/中点，A 侧导出）；None 时回退
      首日收盘。
    - 常规段档距 ``regular_spread``、边缘段档距 ``edge_spread``。
    - 每侧总格数 = ``regular_levels_per_side + edge_levels_per_side``。
    """
    anchor_price: float | None
    regular_spread: float
    edge_spread: float
    regular_levels_per_side: int
    edge_levels_per_side: int

    @property
    def total_levels_per_side(self) -> int:
        return self.regular_levels_per_side + self.edge_levels_per_side


@dataclass(frozen=True)
class ContractAsset:
    """契约内单标的（``assets[]`` 元素）。"""
    asset_id: str
    role: str | None
    enabled: bool
    min_weight: float
    target_weight: float | None  # target_weight_configured=false → None（不虚构）
    max_weight: float
    target_weight_configured: bool
    suggested_spread: float | None = None  # schema 1.1 预留，当前恒 None
    # 契约 asset 级嵌套 grid_config（P0-B，A 侧导出）：字段全在且合法时生效，
    # 否则 ``grid_config`` 属性返回 None → 回测完全回退旧网格行为。
    anchor_price: float | None = None
    regular_spread: float | None = None
    edge_spread: float | None = None
    regular_levels_per_side: int | None = None
    edge_levels_per_side: int | None = None

    @property
    def grid_config(self) -> GridConfig | None:
        """两段步长网格参数；字段不齐/非法 → None（缺省回退旧行为）。"""
        if self.regular_spread is None or self.edge_spread is None:
            return None
        if self.regular_levels_per_side is None or self.edge_levels_per_side is None:
            return None
        if self.regular_spread <= 0 or self.edge_spread <= 0:
            return None
        if self.regular_levels_per_side < 0 or self.edge_levels_per_side < 0:
            return None
        if self.regular_levels_per_side + self.edge_levels_per_side <= 0:
            return None
        return GridConfig(
            anchor_price=self.anchor_price,
            regular_spread=self.regular_spread,
            edge_spread=self.edge_spread,
            regular_levels_per_side=int(self.regular_levels_per_side),
            edge_levels_per_side=int(self.edge_levels_per_side),
        )


@dataclass(frozen=True)
class ContractStrategy:
    """契约内单策略（``strategies[]`` 元素）。"""
    strategy_id: str
    decision_rule: str
    enabled: bool
    use_target_ratio: bool
    rebalance_frequency: str
    rebalance_threshold_abs: float
    asset_rebalance_threshold_abs: float
    signal_filters: list | None
    preferences: dict
    assets: list[ContractAsset]
    # 资本桶（strategy_master 列，A 侧契约 strategies[] 已导出；B 侧网格建议引擎按
    # capital_bucket=="grid" 过滤）。缺失/旧契约 → None（回退 decision_rule 判断）。
    capital_bucket: str | None = None
    # 策略模板×实例改造第一刀（schema 1.0 扩展，A 侧可选）：B 侧当前不消费，仅透传承载。
    template_id: str | None = None
    account_id: str | None = None
    # 网格推荐组合（B2）：A 侧契约 strategies[] 已有，B 侧承载供资金预算参考。
    target_capital_weight: float = 0.0
    risk_budget: float | None = None

    @property
    def enabled_assets(self) -> tuple[str, ...]:
        """启用标的：enabled 且（target_weight!=0 或 decision_rule==grid）。"""
        result: list[str] = []
        for asset in self.assets:
            if not asset.enabled:
                continue
            if self.decision_rule == "grid":
                result.append(asset.asset_id)
            elif asset.target_weight is not None and asset.target_weight > 0:
                result.append(asset.asset_id)
        return tuple(result)


@dataclass(frozen=True)
class CostConfig:
    """交易成本模型（来自契约 ``shared_config.backtest``）。"""
    initial_cash: float
    cost_rate: float
    slippage_rate: float
    stamp_duty_rate: float = 0.0  # ETF 恒 0；字段保留承接未来股票标的


@dataclass
class BacktestContract:
    """解析后的完整契约。"""
    schema_version: str
    contract_type: str
    generated_at: str
    generated_by: str
    strategies: list[ContractStrategy]
    cost: CostConfig
    # 契约 shared_config.grid（A 侧 strategy_params.json 新增 "grid" 段）；缺省 None。
    shared_grid: dict | None = None


@dataclass(frozen=True)
class TradeRecord:
    """单笔成交记录。"""
    trade_date: str
    asset_id: str
    side: str  # BUY / SELL
    shares: int
    price: float
    notional: float
    fee: float
    cash_impact: float  # 正=现金减少（买），负=现金增加（卖）


@dataclass
class BacktestResult:
    """单策略回测结果。"""
    strategy_id: str
    decision_rule: str
    status: str  # OK / SKIPPED / ERROR
    nav: pd.DataFrame = field(default_factory=pd.DataFrame)  # trade_date,equity,strategy_return,drawdown,benchmark_close,benchmark_return
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)
    metrics: MetricsResult | None = None
    turnover: float | None = None  # 年化换手率（单边）
    rebalance_dates: list[str] = field(default_factory=list)
    grid_spreads: dict[str, float] = field(default_factory=dict)  # grid: {asset_id: resolved_spread}
    grid_spread_sources: dict[str, str] = field(default_factory=dict)  # grid: {asset_id: contract/grid_ref/fallback}
    data_coverage: dict[str, tuple[str, str]] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    error: str | None = None


# ============================================================
# 契约解析
# ============================================================

def parse_contract(path: str | Path | None = None) -> BacktestContract:
    """解析 A 侧策略规则契约 JSON 为 ``BacktestContract``。

    - ``schema_version`` 不在 {"1.0", "1.1"} → 抛 ``ValueError``（严格）；
      schema 1.1 支持资产级 ``grid_config`` 嵌套（A 侧已导出）。
    - ``target_weight_configured=false`` → ``target_weight=None``（不虚构权重）。
    - ``signal_filters`` null → ``None``（语义=不过滤）。
    - 未知 ``rebalance_frequency`` → ``monthly`` + warning。
    - 契约缺失 → 显式 ``FileNotFoundError``（B 只读，不虚构）。

    参数：
        path: 契约 JSON 路径；默认 ``config.STRATEGY_CONTRACT_PATH``。
    """
    from qteasy_research.reference.config import STRATEGY_CONTRACT_PATH

    contract_path = Path(path) if path is not None else STRATEGY_CONTRACT_PATH
    if not contract_path.exists():
        raise FileNotFoundError(
            f"策略契约不存在（B 只读，不虚构）：{contract_path}"
        )
    with open(contract_path, encoding="utf-8") as handle:
        raw = json.load(handle)

    schema_version = str(raw.get("schema_version", ""))
    if schema_version not in {"1.0", "1.1"}:
        raise ValueError(
            f"不支持的契约 schema_version={schema_version!r}（引擎仅支持 1.0 / 1.1）"
        )

    strategies = [
        _parse_strategy(item) for item in raw.get("strategies", [])
    ]
    shared = raw.get("shared_config", {})
    backtest = shared.get("backtest", {})
    cost = CostConfig(
        initial_cash=float(backtest.get("initial_cash", 100_000.0)),
        cost_rate=float(backtest.get("cost_rate", 0.0)),
        slippage_rate=float(backtest.get("slippage_rate", 0.0)),
        stamp_duty_rate=0.0,  # ETF 免印花税
    )
    shared_grid = shared.get("grid")
    if not isinstance(shared_grid, dict):
        shared_grid = None
    return BacktestContract(
        schema_version=schema_version,
        contract_type=str(raw.get("contract_type", "")),
        generated_at=str(raw.get("generated_at", "")),
        generated_by=str(raw.get("generated_by", "")),
        strategies=strategies,
        cost=cost,
        shared_grid=shared_grid,
    )


def _parse_strategy(raw: dict) -> ContractStrategy:
    """解析单个策略节点（容错缺失可选字段）。"""
    assets = [_parse_asset(item) for item in raw.get("assets", [])]
    filters = raw.get("signal_filters")
    if isinstance(filters, list):
        filters = [dict(item) for item in filters]
    else:
        filters = None  # null → 不过滤
    # 资本桶（strategy_master 列）：契约里有就取字符串，缺失/类型不对 → None（不抛错，
    # 旧契约/测试 fixture 无该字段时回退 decision_rule 判断）。
    capital_bucket = raw.get("capital_bucket")
    if not isinstance(capital_bucket, str):
        capital_bucket = None
    # 策略模板×实例扩展字段（schema 1.0 可选）：契约里有就取字符串，缺失/类型不对 → None（不抛错）。
    template_id = raw.get("template_id")
    if not isinstance(template_id, str):
        template_id = None
    account_id = raw.get("account_id")
    if not isinstance(account_id, str):
        account_id = None
    # 网格推荐组合（B2）：target_capital_weight（默认 0.0，非法值不抛错）、
    # risk_budget（None=未配置，不虚构）。
    raw_weight = raw.get("target_capital_weight")
    try:
        target_capital_weight = float(raw_weight) if raw_weight is not None else 0.0
    except (TypeError, ValueError):
        target_capital_weight = 0.0
    risk_budget = _as_float(raw.get("risk_budget"))
    return ContractStrategy(
        strategy_id=str(raw.get("strategy_id", "")),
        decision_rule=str(raw.get("decision_rule", "mid_line")),
        enabled=bool(raw.get("enabled", True)),
        use_target_ratio=bool(raw.get("use_target_ratio", False)),
        rebalance_frequency=resolve_rebalance_frequency(
            str(raw.get("rebalance_frequency", ""))
        ),
        rebalance_threshold_abs=float(
            raw.get("rebalance_threshold_abs", DEFAULT_REBALANCE_THRESHOLD)
        ),
        asset_rebalance_threshold_abs=float(
            raw.get("asset_rebalance_threshold_abs", DEFAULT_REBALANCE_THRESHOLD)
        ),
        signal_filters=filters,
        preferences=dict(raw.get("preferences", {})),
        assets=assets,
        capital_bucket=capital_bucket,
        template_id=template_id,
        account_id=account_id,
        target_capital_weight=target_capital_weight,
        risk_budget=risk_budget,
    )


def _as_float(value: Any) -> float | None:
    """数字/可转 float 值 → float；否则 None（不抛错）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    """整数值 → int；否则 None（不抛错）。"""
    number = _as_float(value)
    if number is None or not np.isfinite(number):
        return None
    return int(number)


def _parse_asset(raw: dict) -> ContractAsset:
    """解析单个标节点；``target_weight_configured=false`` → target=None。"""
    configured = bool(raw.get("target_weight_configured", True))
    target = raw.get("target_weight")
    target_weight = float(target) if (configured and target is not None) else None
    # 契约 asset 级嵌套 grid_config（A 侧导出）：缺失/非 dict → 全 None（缺省回退）。
    anchor_price = regular_spread = edge_spread = None
    regular_levels_per_side = edge_levels_per_side = None
    gcfg = raw.get("grid_config")
    if isinstance(gcfg, dict):
        anchor_price = _as_float(gcfg.get("anchor_price"))
        regular_spread = _as_float(gcfg.get("regular_spread"))
        edge_spread = _as_float(gcfg.get("edge_spread"))
        regular_levels_per_side = _as_int(gcfg.get("regular_levels_per_side"))
        edge_levels_per_side = _as_int(gcfg.get("edge_levels_per_side"))
    return ContractAsset(
        asset_id=str(raw.get("asset_id", "")),
        role=raw.get("role"),
        enabled=bool(raw.get("enabled", True)),
        min_weight=float(raw.get("min_weight", 0.0)),
        target_weight=target_weight,
        max_weight=float(raw.get("max_weight", 1.0)),
        target_weight_configured=configured,
        suggested_spread=(
            float(raw["suggested_spread"])
            if "suggested_spread" in raw
            and isinstance(raw["suggested_spread"], (int, float))
            else None
        ),
        anchor_price=anchor_price,
        regular_spread=regular_spread,
        edge_spread=edge_spread,
        regular_levels_per_side=regular_levels_per_side,
        edge_levels_per_side=edge_levels_per_side,
    )


def _enabled_strategies(contract: BacktestContract) -> list[ContractStrategy]:
    """过滤 ``enabled=false`` 的策略。"""
    return [s for s in contract.strategies if s.enabled]


def resolve_rebalance_frequency(freq: str) -> str:
    """解析重平衡频率；未知值 → ``monthly`` + warning（design doc §3 容错）。"""
    value = freq.strip().lower()
    if value in REBALANCE_FREQ_ALIASES:
        return value
    if value:
        warnings.warn(
            f"未知 rebalance_frequency={freq!r}，按 {DEFAULT_REBALANCE_FREQ} 处理",
            UserWarning,
            stacklevel=2,
        )
    return DEFAULT_REBALANCE_FREQ


# ============================================================
# 数据层
# ============================================================

def _read_pre_close_map(data_dir: str | Path | None = None) -> dict[str, pd.Series]:
    """读本地 fund_daily 的 pre_close，返回 ``{asset_id: Series(trade_date: pre_close)}``。

    用于检测除权/拆分跳空并构建后复权价。本地文件无 pre_close 列 → 空 dict
    （该标的保持原始 close）。
    """
    root = Path(data_dir) if data_dir else SYSTEM_B_DATA_ROOT
    path = root / "fund_daily.csv"
    if not path.exists():
        return {}
    try:
        raw = pd.read_csv(path)
    except Exception:
        return {}
    needed = {"ts_code", "trade_date", "pre_close"}
    if not needed.issubset(raw.columns):
        return {}
    result: dict[str, pd.Series] = {}
    for asset_id, group in raw.groupby("ts_code"):
        sub = group[["trade_date", "pre_close"]].copy()
        sub["trade_date"] = pd.to_datetime(sub["trade_date"], errors="coerce")
        sub["pre_close"] = pd.to_numeric(sub["pre_close"], errors="coerce")
        sub = sub.dropna().sort_values("trade_date")
        sub = sub.drop_duplicates("trade_date", keep="last")
        if sub.empty:
            continue
        result[str(asset_id).upper()] = sub.set_index("trade_date")["pre_close"]
    return result


def _apply_hfq(cleaned: pd.DataFrame, pre_close: pd.Series) -> pd.DataFrame:
    """对单标的 close 做后复权（hfq），消除拆分/分红除权跳空。

    后复权（锚定最新价）：逐日累计调整因子。当日 ``pre_close`` 与昨日 close
    不一致即判定除权/拆分，此后价格整体乘 ``昨收 / pre_close`` 上调，使除权
    日的真实收益（相对 pre_close）保留、跳空被消除。目标权重是比例，复权价
    不改变权重语义。
    """
    close = cleaned.set_index("trade_date")["close"].astype(float)
    pre = pre_close.reindex(close.index)
    hfq = close.copy()
    cum = 1.0
    prev_close: float | None = None
    for date in close.index:
        current = float(close[date])
        pc = pre.get(date)
        if prev_close is not None and pc is not None and np.isfinite(pc):
            if abs(pc - prev_close) > 1e-12:
                cum *= prev_close / pc
        hfq[date] = current * cum
        prev_close = current
    out = cleaned.copy()
    out["close"] = hfq.reindex(cleaned["trade_date"]).values
    return out


def load_price_frames(
    assets: list[str] | pd.DataFrame,
    data_dir: str | Path | None = None,
    online_ok: bool = False,
) -> dict[str, pd.DataFrame]:
    """加载启用标的行情，返回 ``{asset_id: DataFrame(trade_date, close)}``。

    - 包装 ``asset_pool.align_pool_price_history``；assets 可为 asset_id 列表或
      DataFrame（含 ``asset_id`` 列）。
    - 默认 ``online_ok=False``：回测确定性优先，不做在线补齐。
    - 返回帧统一 ``trade_date, close`` 列（升序、去重、close>0）。
    - 本地 fund_daily 含 pre_close 时对 close 做**后复权**（消除拆分/分红除权
      跳空，估值用；权重比例不受影响）。契约 ``adj_type="none"`` 为 A 侧约定
      原始 close，B 侧估值复权属 B 侧假设，报告 Assumptions 已标注。
    """
    if isinstance(assets, (list, tuple)):
        frame = pd.DataFrame({"asset_id": list(assets)})
    else:
        frame = assets[["asset_id"]].copy()
    aligned = align_pool_price_history(
        frame, data_dir=data_dir, online_ok=online_ok
    )
    pre_close_map = _read_pre_close_map(data_dir)
    result: dict[str, pd.DataFrame] = {}
    for asset_id, raw in aligned.items():
        if raw.empty:
            result[asset_id] = pd.DataFrame(columns=["trade_date", "close"])
            continue
        cleaned = raw[["trade_date", "close"]].copy()
        cleaned["trade_date"] = pd.to_datetime(cleaned["trade_date"], errors="coerce")
        cleaned["close"] = pd.to_numeric(cleaned["close"], errors="coerce")
        cleaned = cleaned.dropna(subset=["trade_date", "close"])
        cleaned = cleaned[cleaned["close"] > 0]
        cleaned = cleaned.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
        # 后复权估值：本地 fund_daily 含 pre_close 时消除拆分/除权跳空。
        pre = pre_close_map.get(asset_id)
        if pre is not None and not pre.empty and not cleaned.empty:
            cleaned = _apply_hfq(cleaned, pre)
        result[asset_id] = cleaned[["trade_date", "close"]].reset_index(drop=True)
    return result


def build_trading_calendar(frames: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    """全部资产交易日并集（升序去重），作为全局交易日历。"""
    all_dates: list[pd.Timestamp] = []
    for asset_id, frame in frames.items():
        if frame is None or frame.empty:
            continue
        all_dates.extend(pd.to_datetime(frame["trade_date"], errors="coerce").dropna().tolist())
    if not all_dates:
        return pd.DatetimeIndex([])
    index = pd.DatetimeIndex(sorted(set(all_dates)))
    return index


def align_asset_prices(
    frames: dict[str, pd.DataFrame],
    calendar: pd.DatetimeIndex,
) -> pd.DataFrame:
    """对齐各标的价格到全局日历：``index=trade_date, columns=asset_id, value=close``。

    - 标的上线前/未上市保持 NaN（持现金，不虚构价格）。
    - 上市后**停牌/缺数缺口用前收价回填（ffill）**：缺失日若跳过估值，持仓资产
      当天不估值会令 NAV 假崩一天（回归：three_musketeers 2021-10-22 512890 缺
      数，equity 从 139636 假崩到 79043）。
    """
    aligned = pd.DataFrame(index=calendar)
    for asset_id, frame in frames.items():
        if frame is None or frame.empty:
            continue
        sub = frame.copy()
        sub["trade_date"] = pd.to_datetime(sub["trade_date"], errors="coerce")
        sub = sub.dropna(subset=["trade_date"])
        series = (
            sub.set_index("trade_date")["close"]
            .reindex(calendar)
            .ffill()
        )
        aligned[asset_id] = series.values
    return aligned


def load_benchmark_returns(
    calendar: pd.DatetimeIndex,
    data_dir: str | Path | None = None,
) -> tuple[pd.Series | None, str | None]:
    """加载基准（000300.SH）日收益对齐到日历。

    返回 ``(returns, warning)``：基准缺失 → ``(None, warning)``，调用方降级
    benchmark 字段为 None + 报告标注（不 ERROR，保证报告生成）。
    """
    root = Path(data_dir) if data_dir else SYSTEM_B_DATA_ROOT
    index_path = root / "index_daily.csv"
    if not index_path.exists():
        return None, f"基准行情缺失（{index_path.name} 不存在），benchmark 字段置 None"
    frame = pd.read_csv(index_path)
    code = BENCHMARKS["equity_cn"]  # 000300.SH
    subset = frame.loc[frame["ts_code"].astype(str).str.upper() == code.upper()].copy()
    if subset.empty:
        return None, f"基准 {code} 在 index_daily.csv 无数据，benchmark 字段置 None"
    subset["trade_date"] = pd.to_datetime(subset["trade_date"], errors="coerce")
    subset["close"] = pd.to_numeric(subset["close"], errors="coerce")
    subset = subset.dropna(subset=["trade_date", "close"])
    subset = subset[subset["close"] > 0]
    subset = subset.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    closes = subset.set_index("trade_date")["close"].reindex(calendar)
    returns = closes.pct_change().fillna(0.0)
    returns.name = "benchmark_return"
    return returns, None


# ============================================================
# 网格档距三级取值
# ============================================================

def load_grid_reference(path: str | Path | None = None) -> dict[str, float]:
    """读取 B 侧网格参考表，返回 ``{asset_id: suggested_reference_spread}``。

    - 首行含 ``# schema_version=...`` 注释 → ``pd.read_csv(comment="#")``。
    - 缺失/损坏 → 空 dict（调用方退化为兜底 5% + warning）。
    - 只取正数、非 NaN 值。
    """
    grid_path = Path(path) if path is not None else GRID_REFERENCE_PATH
    if not grid_path.exists():
        return {}
    try:
        frame = pd.read_csv(grid_path, comment="#")
    except Exception:
        return {}
    if "asset_id" not in frame.columns or "suggested_reference_spread" not in frame.columns:
        return {}
    result: dict[str, float] = {}
    for _, row in frame.iterrows():
        asset_id = str(row["asset_id"]).strip()
        value = row["suggested_reference_spread"]
        try:
            spread = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(spread) and spread > 0:
            result[asset_id] = spread
    return result


def resolve_grid_spread(
    asset_id: str,
    contract_asset: ContractAsset | None,
    grid_ref: dict[str, float],
) -> tuple[float, str]:
    """网格档距三级取值，返回 ``(spread, source)``。

    1) 契约 ``asset.suggested_spread`` 为正数 → source="contract"
    2) grid_ref[asset_id] 为正数 → source="grid_ref"
    3) 否则 → ``DEFAULT_GRID_SPREAD``，source="fallback"
    """
    if contract_asset is not None and contract_asset.suggested_spread is not None:
        if contract_asset.suggested_spread > 0:
            return contract_asset.suggested_spread, "contract"
    if asset_id in grid_ref:
        return grid_ref[asset_id], "grid_ref"
    return DEFAULT_GRID_SPREAD, "fallback"


# ============================================================
# 成本模型
# ============================================================

def execution_price(side: str, close: float, cost: CostConfig) -> float:
    """成交价：BUY 加滑点，SELL 减滑点。"""
    if side == "BUY":
        return close * (1.0 + cost.slippage_rate)
    return close * (1.0 - cost.slippage_rate)


def buy_cash_out(shares: int, price: float, cost: CostConfig) -> float:
    """买入现金支出（含滑点价 + 佣金；ETF 印花税 0）。"""
    notional = shares * price
    fee = notional * cost.cost_rate
    stamp = notional * cost.stamp_duty_rate
    return notional + fee + stamp


def sell_cash_in(shares: int, price: float, cost: CostConfig) -> float:
    """卖出现金收入（含滑点价 − 佣金；ETF 印花税 0）。"""
    notional = shares * price
    fee = notional * cost.cost_rate
    stamp = notional * cost.stamp_duty_rate
    return notional - fee - stamp


def trade_fee(shares: int, price: float, cost: CostConfig) -> float:
    """单笔费用（佣金 + 印花税）。"""
    notional = shares * price
    return notional * cost.cost_rate + notional * cost.stamp_duty_rate


def round_lot(shares: float, lot: int = TRADE_LOT_SIZE) -> int:
    """买入向下取整到整手；``lot=1`` 时返回整数原值。"""
    if lot <= 1:
        return int(shares)
    return int(math.floor(shares / lot) * lot)


# ============================================================
# 重平衡调度
# ============================================================

def rebalance_dates(
    freq: str,
    calendar: pd.DatetimeIndex,
    start: str | pd.Timestamp | None = None,
) -> list[pd.Timestamp]:
    """按频率确定调仓日期（升序）。

    - ``daily`` → calendar 中 >= start 的全部日期。
    - ``weekly`` → 按 ISO 周分组取每组最后一天。
    - ``monthly`` → 月末最后一个交易日（``resample("ME")``）。
    - ``quarterly`` → 3/6/9/12 月末最后一个交易日。
    - ``never`` → 空列表（仅首日建仓，不调仓）。
    """
    if calendar is None or len(calendar) == 0:
        return []
    start_ts = pd.Timestamp(start) if start is not None else calendar[0]
    mask = calendar >= start_ts
    subset = calendar[mask]
    if len(subset) == 0:
        return []

    if freq == "daily":
        return list(subset)
    if freq == "never":
        return []
    if freq in ("weekly", "monthly", "quarterly"):
        series = pd.Series(subset, index=subset)
        if freq == "weekly":
            # ISO 周必须按 (年, 周) 复合键分组，否则跨年时周数重复导致乱序/合并
            # （如 2020 W1 与 2019 W1 归同组），重平衡日期几乎失效。升序返回每周最后交易日。
            iso = series.index.isocalendar()
            frame = pd.DataFrame({
                "date": subset,
                "year": iso.year.values,
                "week": iso.week.values,
            })
            last = frame.groupby(["year", "week"], sort=True)["date"].last()
            return [pd.Timestamp(value) for value in last.tolist()]
        # monthly/quarterly 统一按月末聚合；quarterly 只保留 3/6/9/12 月末。
        month_ends = series.resample("ME").last()
        if freq == "quarterly":
            month_ends = month_ends[month_ends.index.month.isin([3, 6, 9, 12])]
        return [pd.Timestamp(value) for value in month_ends.dropna().tolist()]

    # 未知频率（解析层已兜底 monthly，此处防御）。
    return list(subset)


# ============================================================
# 信号过滤
# ============================================================

def apply_signal_filters(
    filters: list[dict] | None,
    phase: str | None,
    date: pd.Timestamp,
) -> tuple[bool, list[str]]:
    """调仓前检查信号过滤条件；返回 ``(是否放行, 说明)``。

    - ``None``/空列表 → 放行。
    - 已知字段仅 ``macro_regime.phase``（``operator ∈ {eq, neq}``）。
    - **未知字段 → ``filter_unknown``，不阻塞**（design doc §3「不硬判」）。
    """
    if not filters:
        return True, []
    notes: list[str] = []
    for filt in filters:
        if not isinstance(filt, dict):
            notes.append("filter_unknown: invalid filter entry")
            continue
        field = str(filt.get("field", ""))
        operator = str(filt.get("operator", "")).lower()
        value = filt.get("value")
        if field != "macro_regime.phase":
            notes.append(f"filter_unknown: {field}")
            continue
        if operator not in ("eq", "neq"):
            notes.append(f"filter_unknown: operator={operator}")
            continue
        matched = (phase == value) if operator == "eq" else (phase != value)
        if not matched:
            notes.append(
                f"filter_blocked: {field} {operator} {value} "
                f"(phase_asof={phase}, date={date.date()})"
            )
            return False, notes
    return True, notes


# ============================================================
# 宏观 phase 点内查找
# ============================================================

def build_phase_lookup(
    data_root: str | Path | None = None,
) -> dict[str, str]:
    """构建逐月末 phase 点内查找表 ``{month_end_iso: phase}``。

    对每月末 slice（``month < T``）再 ``build_duration_phase``，得到截至该月末的
    trailing-run 阶段，严格前置（防未来）。失败/空表 → 空 dict（phase=None）。
    """
    root = Path(data_root) if data_root else SYSTEM_B_DATA_ROOT
    try:
        macro_table = build_monthly_scenario_table(root)
    except Exception:
        return {}
    if macro_table is None or macro_table.empty:
        return {}
    table = macro_table.sort_values("month")
    lookup: dict[str, str] = {}
    for month_end in pd.to_datetime(table["month"], errors="coerce").dropna():
        subset = table[pd.to_datetime(table["month"]) <= month_end]
        if subset.empty:
            continue
        duration = build_duration_phase(subset)
        phase = duration.get("phase")
        if phase is not None:
            lookup[month_end.strftime("%Y-%m-%d")] = str(phase)
    return lookup


def phase_asof(lookup: dict[str, str], date: pd.Timestamp) -> str | None:
    """返回 ``< date`` 的最大月末对应 phase（严格前置，防未来）。

    - ``date`` 恰为月末 → 取上一月末（点内，不包含当月）。
    - 无前置月末 → None（无宏观信号）。
    """
    if not lookup:
        return None
    date_iso = pd.Timestamp(date).strftime("%Y-%m-%d")
    eligible = {k: v for k, v in lookup.items() if k < date_iso}
    if not eligible:
        return None
    latest = max(eligible.keys())
    return eligible[latest]


# ============================================================
# 目标权重计算（四规则分支）
# ============================================================

def _defensive(asset: ContractAsset) -> bool:
    """是否防御资产（role 关键词或 asset_id 命中 DEFENSIVE_ASSETS）。"""
    if asset.asset_id in DEFENSIVE_ASSETS:
        return True
    role = (asset.role or "").lower()
    return any(keyword in role for keyword in _DEFENSIVE_KEYWORDS)


def compute_target_barbell(
    strategy: ContractStrategy,
    assets: dict[str, ContractAsset],
) -> dict[str, float]:
    """barbell：目标 = 契约 target_weight（configured=True 且 >0 才计入）。"""
    target: dict[str, float] = {}
    for asset in strategy.assets:
        if asset.target_weight is not None and asset.target_weight > 0:
            target[asset.asset_id] = asset.target_weight
    return target


def compute_target_midline(
    strategy: ContractStrategy,
    assets: dict[str, ContractAsset],
    phase: str | None,
    enable_macro_tilt: bool = True,
) -> tuple[dict[str, float], list[str]]:
    """mid_line：契约 target + 宏观 tilt（仅 phase=late 收紧期）。

    - ``tilt = preferences.macro_fit × MACRO_TILT_RATE``（B侧假设）。
    - 防御资产按目标权重比例增持 tilt 总额；风险资产按目标权重比例减持。
    - 权重 clamp 到 [min, max]；夹不住时记入 assumptions。
    """
    notes: list[str] = []
    raw_target: dict[str, float] = {}
    for asset in strategy.assets:
        if asset.target_weight is not None and asset.target_weight > 0:
            raw_target[asset.asset_id] = asset.target_weight

    tilt_total = 0.0
    if enable_macro_tilt and phase == "late":
        macro_fit = float(strategy.preferences.get("macro_fit", 0.0))
        tilt_total = macro_fit * MACRO_TILT_RATE

    target = dict(raw_target)
    if tilt_total <= 0 or not target:
        return target, notes

    defensive_ids = [
        asset_id for asset_id in target
        if _defensive(assets.get(asset_id) or _lookup_asset(strategy, asset_id))
    ]
    risk_ids = [asset_id for asset_id in target if asset_id not in defensive_ids]
    if not defensive_ids or not risk_ids:
        return target, notes

    defensive_sum = sum(raw_target[a] for a in defensive_ids)
    risk_sum = sum(raw_target[a] for a in risk_ids)

    # 风险资产减持合计 = tilt_total；防御资产增持合计 = tilt_total。
    asset_map = _asset_map(strategy)
    for asset_id in risk_ids:
        share = raw_target[asset_id] / risk_sum if risk_sum > 0 else 0.0
        target[asset_id] = raw_target[asset_id] - tilt_total * share
    for asset_id in defensive_ids:
        share = raw_target[asset_id] / defensive_sum if defensive_sum > 0 else 0.0
        target[asset_id] = raw_target[asset_id] + tilt_total * share

    # clamp 到 [min, max]。
    for asset_id, value in list(target.items()):
        spec = asset_map.get(asset_id)
        if spec is not None:
            lower = spec.min_weight
            upper = spec.max_weight
            clamped = min(max(value, lower), upper)
            if clamped != value and (value > upper or value < lower):
                notes.append(
                    f"tilt_limited_by_weight_bounds: {asset_id} {value:.4f}->{clamped:.4f}"
                )
            target[asset_id] = clamped
    return target, notes


def _asset_map(strategy: ContractStrategy) -> dict[str, ContractAsset]:
    return {asset.asset_id: asset for asset in strategy.assets}


def _lookup_asset(strategy: ContractStrategy, asset_id: str) -> ContractAsset | None:
    for asset in strategy.assets:
        if asset.asset_id == asset_id:
            return asset
    return None


def grid_target_weight(asset: ContractAsset) -> float:
    """网格中枢 = max_weight × 0.5（用户裁定）。"""
    return asset.max_weight * 0.5


# ============================================================
# 模拟器主循环
# ============================================================

def run_backtest(
    strategy: ContractStrategy,
    contract: BacktestContract,
    *,
    data_root: str | Path | None = None,
    grid_reference: str | Path | dict[str, float] | None = None,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    initial_cash: float | None = None,
    online_ok: bool = False,
    enable_macro_tilt: bool = True,
    lot: int = TRADE_LOT_SIZE,
    phase_lookup: dict[str, str] | None = None,
) -> BacktestResult:
    """对单策略运行事件驱动回测。

    防未来函数：T 收盘信号 → T+1 收盘成交；首日建仓按契约目标 T0 收盘成交
    （先验）；宏观 phase 用 ``< T`` 月末。网格超买按可用现金缩放。
    """
    result = BacktestResult(
        strategy_id=strategy.strategy_id,
        decision_rule=strategy.decision_rule,
        status="OK",
        assumptions=_base_assumptions(strategy, contract),
    )

    # short_term：无启用资产 → SKIPPED 占位。
    if strategy.decision_rule == "short_term":
        result.status = "SKIPPED"
        result.error = "no_enabled_assets"
        return result

    enabled = strategy.enabled_assets
    if not enabled:
        result.status = "SKIPPED"
        result.error = "no_enabled_assets"
        return result

    cash = float(initial_cash if initial_cash is not None else contract.cost.initial_cash)

    # ---- 数据层 ----
    try:
        frames = load_price_frames(list(enabled), data_dir=data_root, online_ok=online_ok)
    except Exception as exc:
        result.status = "ERROR"
        result.error = f"load_price_frames failed: {type(exc).__name__}: {exc}"
        return result

    empty_frames = [aid for aid in enabled if frames.get(aid, pd.DataFrame()).empty]
    if len(empty_frames) == len(enabled):
        result.status = "ERROR"
        result.error = "all_enabled_assets_no_data"
        return result

    calendar = build_trading_calendar(frames)
    if len(calendar) == 0:
        result.status = "ERROR"
        result.error = "empty_trading_calendar"
        return result

    start_ts = pd.Timestamp(start) if start is not None else pd.Timestamp(BACKTEST_START_DEFAULT)
    end_ts = pd.Timestamp(end) if end is not None else calendar[-1]
    window = calendar[(calendar >= start_ts) & (calendar <= end_ts)]
    if len(window) == 0:
        result.status = "ERROR"
        result.error = "empty_window"
        return result

    prices = align_asset_prices(frames, window)

    # 数据覆盖（每标的 first~last）。
    for asset_id in enabled:
        if asset_id not in prices.columns:
            continue
        sub = prices[asset_id].dropna()
        if len(sub) > 0:
            result.data_coverage[asset_id] = (
                sub.index[0].strftime("%Y-%m-%d"),
                sub.index[-1].strftime("%Y-%m-%d"),
            )

    # 基准收益（缺失 → 降级 None + 标注，不 ERROR）。
    bench_returns, bench_warning = load_benchmark_returns(window, data_dir=data_root)
    if bench_warning:
        result.assumptions.append(f"B-side; awaiting A confirmation: {bench_warning}")

    # ---- 宏观 phase 点内查找 ----
    if phase_lookup is None:
        phase_lookup = build_phase_lookup(data_root)

    # ---- 网格档距解析（grid 策略） ----
    grid_spreads: dict[str, float] = {}
    grid_sources: dict[str, str] = {}
    if strategy.decision_rule == "grid":
        grid_ref_map: dict[str, float] = {}
        if isinstance(grid_reference, dict):
            grid_ref_map = grid_reference
        elif grid_reference is not None:
            grid_ref_map = load_grid_reference(grid_reference)
        else:
            grid_ref_map = load_grid_reference(GRID_REFERENCE_PATH)
            if not grid_ref_map:
                result.assumptions.append(
                    "B-side; awaiting A confirmation: grid_reference_table.csv 缺失/空，"
                    "网格档距退化为兜底 5%"
                )
        asset_map = _asset_map(strategy)
        for asset_id in enabled:
            spread, source = resolve_grid_spread(
                asset_id, asset_map.get(asset_id), grid_ref_map
            )
            grid_spreads[asset_id] = spread
            grid_sources[asset_id] = source
    result.grid_spreads = grid_spreads
    result.grid_spread_sources = grid_sources

    # ---- 首日建仓（T0 收盘成交，先验） ----
    positions: dict[str, int] = {}
    for asset_id in enabled:
        positions[asset_id] = 0
    # 成交记录贯穿全程（含首日建仓，审计完整性）。
    trades: list[TradeRecord] = []
    first_date = window[0]
    first_prices = prices.loc[first_date]

    if strategy.decision_rule == "grid":
        for asset_id in enabled:
            price = _close_at(first_prices, asset_id)
            if price is None or price <= 0:
                continue
            target_notional = grid_target_weight(_asset_map(strategy)[asset_id]) * cash
            shares_float = target_notional / price
            shares = round_lot(shares_float, lot)
            max_by_cash = round_lot(cash / (price * (1 + contract.cost.slippage_rate)), lot)
            shares = min(shares, max_by_cash)
            if shares <= 0:
                continue
            fill = execution_price("BUY", price, contract.cost)
            out = buy_cash_out(shares, fill, contract.cost)
            if out > cash:
                shares = max(0, round_lot((cash / (fill * (1 + contract.cost.cost_rate))), lot))
                out = buy_cash_out(shares, fill, contract.cost) if shares > 0 else 0.0
            positions[asset_id] += shares
            cash -= out
            trades.append(TradeRecord(
                trade_date=first_date.strftime("%Y-%m-%d"),
                asset_id=asset_id, side="BUY", shares=shares, price=round(fill, 6),
                notional=round(shares * fill, 4),
                fee=round(trade_fee(shares, fill, contract.cost), 4),
                cash_impact=round(out, 4),
            ))
    else:
        # barbell / mid_line：按契约目标建仓。
        if strategy.decision_rule == "barbell":
            target = compute_target_barbell(strategy, _asset_map(strategy))
        else:
            target, tilt_notes = compute_target_midline(
                strategy, _asset_map(strategy), phase_asof(phase_lookup, first_date),
                enable_macro_tilt=enable_macro_tilt,
            )
        total_target = sum(target.values())
        for asset_id, weight in target.items():
            if weight <= 0:
                continue
            price = _close_at(first_prices, asset_id)
            if price is None or price <= 0:
                continue
            notional = weight * cash
            if total_target > 1:
                notional = notional / total_target
            shares = round_lot(notional / price, lot)
            fill = execution_price("BUY", price, contract.cost)
            max_by_cash = round_lot(cash / (fill * (1 + contract.cost.cost_rate)), lot)
            shares = min(shares, max_by_cash)
            if shares <= 0:
                continue
            out = buy_cash_out(shares, fill, contract.cost)
            if out > cash:
                shares = max(0, round_lot((cash / (fill * (1 + contract.cost.cost_rate))), lot))
                out = buy_cash_out(shares, fill, contract.cost) if shares > 0 else 0.0
            positions[asset_id] += shares
            cash -= out
            trades.append(TradeRecord(
                trade_date=first_date.strftime("%Y-%m-%d"),
                asset_id=asset_id, side="BUY", shares=shares, price=round(fill, 6),
                notional=round(shares * fill, 4),
                fee=round(trade_fee(shares, fill, contract.cost), 4),
                cash_impact=round(out, 4),
            ))

    # ---- 网格状态 ----
    grid_anchor: dict[str, float] = {}
    grid_index: dict[str, int] = {}
    if strategy.decision_rule == "grid":
        asset_map_for_anchor = _asset_map(strategy)
        for asset_id in enabled:
            # 契约嵌套 grid_config 带 anchor_price 时锚=配置中轴；否则回退首日收盘。
            cfg = asset_map_for_anchor[asset_id].grid_config
            anchor = cfg.anchor_price if (cfg is not None and cfg.anchor_price is not None) else None
            if anchor is None:
                price = _close_at(first_prices, asset_id)
                if price is not None and price > 0:
                    anchor = price
            if anchor is not None:
                grid_anchor[asset_id] = anchor
            grid_index[asset_id] = 0

    # ---- 主循环 ----
    rebalance_days = []
    if strategy.decision_rule != "grid":
        rebalance_days = rebalance_dates(
            strategy.rebalance_frequency, window, start=start_ts
        )
    result.rebalance_dates = [d.strftime("%Y-%m-%d") for d in rebalance_days]

    pending: list[dict[str, Any]] = []
    nav_rows: list[dict[str, Any]] = []
    last_equity: float | None = None

    for i, date in enumerate(window):
        # ---- 信号阶段（T 收盘） ----
        if strategy.decision_rule == "grid":
            day_trades = _grid_signal(
                strategy, prices, date, positions, cash, contract, grid_spreads,
                grid_anchor, grid_index, lot,
            )
            pending.extend(day_trades)
        elif date in rebalance_days:
            phase = phase_asof(phase_lookup, date)
            passed, notes = apply_signal_filters(strategy.signal_filters, phase, date)
            if passed:
                if strategy.decision_rule == "barbell":
                    target = compute_target_barbell(strategy, _asset_map(strategy))
                else:
                    target, _ = compute_target_midline(
                        strategy, _asset_map(strategy), phase,
                        enable_macro_tilt=enable_macro_tilt,
                    )
                pending.extend(
                    _rebalance_deltas(
                        strategy, target, positions, prices.loc[date], contract, lot,
                        cash=cash,
                    )
                )

        # ---- 执行阶段（T+1 收盘） ----
        if i + 1 < len(window) and pending:
            next_date = window[i + 1]
            next_prices = prices.loc[next_date]
            new_positions = dict(positions)
            for order in pending:
                asset_id = order["asset_id"]
                side = order["side"]
                price = _close_at(next_prices, asset_id)
                if price is None or price <= 0:
                    continue
                if side == "BUY":
                    fill = execution_price("BUY", price, contract.cost)
                    requested = order["shares"]
                    max_by_cash = round_lot(
                        cash / (fill * (1 + contract.cost.cost_rate)), lot
                    )
                    shares = min(requested, max_by_cash)
                    shares = round_lot(shares, lot)
                    if shares <= 0:
                        continue
                    out = buy_cash_out(shares, fill, contract.cost)
                    if out > cash:
                        continue
                    new_positions[asset_id] += shares
                    cash -= out
                    trades.append(TradeRecord(
                        trade_date=next_date.strftime("%Y-%m-%d"),
                        asset_id=asset_id, side="BUY", shares=shares, price=round(fill, 6),
                        notional=round(shares * fill, 4), fee=round(trade_fee(shares, fill, contract.cost), 4),
                        cash_impact=round(out, 4),
                    ))
                else:  # SELL
                    fill = execution_price("SELL", price, contract.cost)
                    shares = min(order["shares"], new_positions.get(asset_id, 0))
                    if shares <= 0:
                        continue
                    inn = sell_cash_in(shares, fill, contract.cost)
                    new_positions[asset_id] -= shares
                    cash += inn
                    trades.append(TradeRecord(
                        trade_date=next_date.strftime("%Y-%m-%d"),
                        asset_id=asset_id, side="SELL", shares=shares, price=round(fill, 6),
                        notional=round(shares * fill, 4), fee=round(trade_fee(shares, fill, contract.cost), 4),
                        cash_impact=-round(inn, 4),
                    ))
            positions = new_positions
            pending = []

        # ---- 日终估值（T 收盘） ----
        equity = cash
        day_prices = prices.loc[date]
        for asset_id in enabled:
            price = _close_at(day_prices, asset_id)
            if price is not None and positions.get(asset_id, 0) > 0:
                equity += positions[asset_id] * price
        if last_equity is not None:
            strategy_return = equity / last_equity - 1.0
        else:
            strategy_return = 0.0
        last_equity = equity
        nav_rows.append({
            "trade_date": date.strftime("%Y-%m-%d"),
            "equity": round(equity, 4),
            "strategy_return": round(strategy_return, 8),
        })

    if not nav_rows:
        result.status = "ERROR"
        result.error = "no_nav_rows"
        return result

    # ---- 绩效汇总 ----
    nav = pd.DataFrame(nav_rows)
    nav["trade_date"] = pd.to_datetime(nav["trade_date"])
    returns = nav["strategy_return"].values
    bench_aligned = None
    if bench_returns is not None:
        bench_aligned = bench_returns.reindex(nav["trade_date"]).fillna(0.0).values
    metrics = PerformanceMetrics.calculate(returns, bench_aligned)

    nav["drawdown"] = nav["equity"] / nav["equity"].cummax() - 1.0
    if bench_returns is not None:
        nav["benchmark_return"] = bench_returns.reindex(nav["trade_date"]).fillna(0.0).values
        nav["benchmark_close"] = (1.0 + nav["benchmark_return"]).cumprod()
    else:
        nav["benchmark_return"] = 0.0
        nav["benchmark_close"] = np.nan

    result.nav = nav
    result.trades = pd.DataFrame([t.__dict__ for t in trades])
    result.metrics = metrics
    result.turnover = _compute_turnover(trades, nav)
    return result


def _close_at(row: pd.Series, asset_id: str) -> float | None:
    """取单日某标的 close；NaN/缺失 → None。"""
    try:
        value = row.get(asset_id)
    except Exception:
        return None
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return float(value)


def _grid_signal(
    strategy: ContractStrategy,
    prices: pd.DataFrame,
    date: pd.Timestamp,
    positions: dict[str, int],
    cash: float,
    contract: BacktestContract,
    grid_spreads: dict[str, float],
    grid_anchor: dict[str, float],
    grid_index: dict[str, int],
    lot: int,
) -> list[dict[str, Any]]:
    """网格每日 T 收盘信号：判定档位穿越，生成 BUY/SELL 订单。

    - ``raw = (close − anchor) / (anchor × regular_spread)``；``raw>=1`` 卖、
      ``raw<=-1`` 买，按 ``floor(|raw|)`` 档数。锚价信号日更新（B侧假设）。
    - **两段步长**（契约嵌套 ``grid_config``）：|raw| 档数在常规段
      （1..regular_levels_per_side）每档用 regular_spread；进入边缘段（>常规档）
      每档用 edge_spread。每侧总格数 = regular + edge；缺省（无 grid_config）
      完全回退旧行为（锚=首日收盘、单档距 ``resolve_grid_spread``、
      ``GRID_LEVELS_PER_SIDE=4``、``step_notional`` 用 2×每侧总格数）。
    """
    orders: list[dict[str, Any]] = []
    asset_map = _asset_map(strategy)
    for asset_id in positions:
        close = _close_at(prices.loc[date], asset_id)
        if close is None or close <= 0:
            continue
        asset = asset_map.get(asset_id)
        gcfg = asset.grid_config if asset is not None else None
        if asset_id not in grid_anchor:
            # 上市晚于回测起点的标的：上市当日按中枢 max×0.5 建仓（先验），
            # 并初始化锚价（有 grid_config 用配置中轴，否则当日收盘）；
            # 当日不触发档位信号，次日进入档位交易。
            # （修复：此前 grid_anchor 仅回测首日初始化，上市晚的标的永不触网。）
            if gcfg is not None and gcfg.anchor_price is not None:
                grid_anchor[asset_id] = gcfg.anchor_price
            else:
                grid_anchor[asset_id] = close
            grid_index[asset_id] = 0
            target_notional = grid_target_weight(asset) * cash
            shares = round_lot(target_notional / close, lot)
            if shares > 0:
                orders.append({"asset_id": asset_id, "side": "BUY", "shares": shares})
            continue
        anchor = grid_anchor[asset_id]
        if gcfg is not None:
            regular_spread = gcfg.regular_spread
            edge_spread = gcfg.edge_spread
            regular_levels = gcfg.regular_levels_per_side
            edge_levels = gcfg.edge_levels_per_side
            total_levels = gcfg.total_levels_per_side
        else:
            spread = grid_spreads.get(asset_id, DEFAULT_GRID_SPREAD)
            if spread <= 0:
                continue
            regular_spread = spread
            edge_spread = spread
            regular_levels = GRID_LEVELS_PER_SIDE
            edge_levels = 0
            total_levels = GRID_LEVELS_PER_SIDE
        if regular_spread <= 0 or anchor <= 0:
            continue
        raw = (close - anchor) / (anchor * regular_spread)
        # 每侧总格数（常规+边缘）→ 两段合计仓位份数。
        step_notional = asset.max_weight * cash / (2 * total_levels)
        if raw >= 1:
            levels = int(math.floor(raw))
            regular_n = min(levels, regular_levels)
            edge_n = levels - regular_n
            factor = (1.0 + regular_spread) ** regular_n
            if edge_n > 0:
                factor *= (1.0 + edge_spread) ** edge_n
            shares = round_lot(step_notional / close, lot)
            if shares > 0:
                orders.append({"asset_id": asset_id, "side": "SELL", "shares": shares})
            grid_anchor[asset_id] = anchor * factor
            grid_index[asset_id] += levels
        elif raw <= -1:
            levels = int(math.floor(-raw))
            regular_n = min(levels, regular_levels)
            edge_n = levels - regular_n
            factor = (1.0 - regular_spread) ** regular_n
            if edge_n > 0:
                factor *= (1.0 - edge_spread) ** edge_n
            shares = round_lot(step_notional / close, lot)
            if shares > 0:
                orders.append({"asset_id": asset_id, "side": "BUY", "shares": shares})
            grid_anchor[asset_id] = anchor * factor
            grid_index[asset_id] -= levels
    return orders


def _rebalance_deltas(
    strategy: ContractStrategy,
    target: dict[str, float],
    positions: dict[str, int],
    day_prices: pd.Series,
    contract: BacktestContract,
    lot: int,
    cash: float = 0.0,
) -> list[dict[str, Any]]:
    """按目标权重与当前持仓差生成订单（含整手、不做现金约束前置——执行时处理）。

    权重基准 = 总资产（现金 + 持仓市值）。目标权重是组合总资产的占比：若只按持仓
    市值计算，target 权重 <100% 或部分标的无数据时会每周反复减持直至清仓
    （回归：three_musketeers 2018 早期 518880 反复 SELL 归零后触发 NaN）。
    """
    orders: list[dict[str, Any]] = []
    portfolio_value = float(cash)
    for asset_id in positions:
        price = _close_at(day_prices, asset_id)
        if price is not None:
            portfolio_value += positions[asset_id] * price
    if portfolio_value <= 0:
        # 防御兜底：仅累加有效价（忽略 NaN），避免 fallback 污染为 NaN。
        valid = [_close_at(day_prices, a) for a in positions]
        portfolio_value = sum(v for v in valid if v is not None)

    total_target = sum(target.values()) or 1.0
    for asset_id in positions:
        weight = target.get(asset_id, 0.0) / total_target
        current_value = 0.0
        price = _close_at(day_prices, asset_id)
        if price is not None:
            current_value = positions[asset_id] * price
        target_value = weight * portfolio_value
        delta_value = target_value - current_value
        if price is None or abs(delta_value) < 1e-9:
            continue
        if delta_value > 0:
            shares = round_lot(delta_value / price, lot)
            if shares > 0:
                orders.append({"asset_id": asset_id, "side": "BUY", "shares": shares})
        else:
            shares = int(round(-delta_value / price))
            if shares > 0:
                orders.append({"asset_id": asset_id, "side": "SELL", "shares": shares})
    return orders


def _compute_turnover(trades: list[TradeRecord], nav: pd.DataFrame) -> float:
    """年化换手率（单边）= Σ|成交额| / (nav 均值 × 年数)。"""
    if not trades or nav is None or len(nav) == 0:
        return 0.0
    total_notional = sum(abs(t.notional) for t in trades)
    avg_nav = float(nav["equity"].mean())
    years = len(nav) / 252.0
    if avg_nav <= 0 or years <= 0:
        return 0.0
    return round(total_notional / avg_nav / years, 4)


def _base_assumptions(
    strategy: ContractStrategy,
    contract: BacktestContract,
) -> list[str]:
    """策略回测的基础 B 侧假设清单（进报告 Assumptions，全 ASCII）。"""
    return [
        "B-side; awaiting A confirmation: signal at T close, fill at T+1 close "
        "(no lookahead)",
        "B-side; awaiting A confirmation: initial position built at T0 close per "
        "contract target (a priori)",
        "B-side; awaiting A confirmation: assets before listing are held as cash "
        "(no synthetic prices)",
        "B-side; awaiting A confirmation: close is backward-adjusted (hfq) to remove "
        "split/dividend gaps; contract adj_type=none refers to raw close",
        "B-side; awaiting A confirmation: missing/suspended days are valued at "
        "previous close (ffill)",
        f"B-side; awaiting A confirmation: cost model cost_rate={contract.cost.cost_rate} "
        f"+ slippage={contract.cost.slippage_rate}, ETF stamp duty = 0",
        f"B-side; awaiting A confirmation: lot size = {TRADE_LOT_SIZE} "
        "(buy rounds down to lot)",
    ]


# ============================================================
# 绩效与报告
# ============================================================

def _ascii_duration(value: object) -> str:
    """metrics 最大回撤持续天数做 ASCII 映射（metrics.py 输出含中文）。"""
    if value is None:
        return "n/a"
    text = str(value)
    if text == "尚未恢复":
        return "not recovered"
    if "交易日" in text:
        return text.replace("交易日", " trading days")
    return text


def _metrics_dict(metrics: MetricsResult | None) -> dict[str, float | str]:
    if metrics is None:
        return {}
    return {
        "total_return": round(metrics.total_return, 6),
        "annual_return": round(metrics.annual_return, 6),
        "annual_volatility": round(metrics.annual_volatility, 6),
        "sharpe_ratio": round(metrics.sharpe_ratio, 4),
        "sortino_ratio": round(metrics.sortino_ratio, 4),
        "calmar_ratio": round(metrics.calmar_ratio, 4),
        "max_drawdown": round(metrics.max_drawdown, 6),
        "max_drawdown_duration": _ascii_duration(metrics.max_drawdown_duration),
        "win_rate": round(metrics.win_rate, 6),
        "benchmark_return": round(metrics.benchmark_return, 6),
        "benchmark_annual_return": round(metrics.benchmark_annual_return, 6),
        "alpha": round(metrics.alpha, 6),
        "beta": round(metrics.beta, 4),
        "information_ratio": round(metrics.information_ratio, 4),
    }


def render_markdown(result: BacktestResult) -> str:
    """渲染 Markdown 回测报告。全 ASCII 标识（strategy_id/asset_id/000300.SH）。"""
    lines: list[str] = []
    lines.append(f"# Backtest Report: {result.strategy_id}")
    lines.append("")
    lines.append(f"- decision_rule: {result.decision_rule}")
    lines.append(f"- status: {result.status}")
    if result.error:
        lines.append(f"- error: {result.error}")
    if result.nav is not None and not result.nav.empty:
        lines.append(f"- window: {result.nav['trade_date'].iloc[0].strftime('%Y-%m-%d')} "
                     f"~ {result.nav['trade_date'].iloc[-1].strftime('%Y-%m-%d')}")
    lines.append(f"- benchmark: {BENCHMARKS['equity_cn']}")
    lines.append("")

    if result.status == "SKIPPED":
        lines.append(f"## Note")
        lines.append("")
        lines.append(f"Strategy skipped: {result.error}.")
        lines.append("")
        lines.append("## Assumptions")
        for assumption in result.assumptions:
            lines.append(f"- {assumption}")
        return "\n".join(lines)

    # 概况表
    lines.append("## Overview")
    lines.append("")
    lines.append("| item | value |")
    lines.append("|---|---|")
    lines.append(f"| strategy_id | {result.strategy_id} |")
    lines.append(f"| decision_rule | {result.decision_rule} |")
    lines.append(f"| status | {result.status} |")
    if result.nav is not None and not result.nav.empty:
        lines.append(f"| window_start | {result.nav['trade_date'].iloc[0].strftime('%Y-%m-%d')} |")
        lines.append(f"| window_end | {result.nav['trade_date'].iloc[-1].strftime('%Y-%m-%d')} |")
    lines.append(f"| rebalance_dates | {len(result.rebalance_dates)} |")
    lines.append(f"| turnover_annual | {result.turnover if result.turnover is not None else 'n/a'} |")
    lines.append("")

    # 绩效表
    lines.append("## Performance")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    metrics = _metrics_dict(result.metrics)
    for key, value in metrics.items():
        if isinstance(value, float):
            lines.append(f"| {key} | {value:.6f} |")
        else:
            lines.append(f"| {key} | {value} |")
    lines.append("")

    # 网格档距
    if result.grid_spreads:
        lines.append("## Grid Spread")
        lines.append("")
        lines.append("| asset_id | spread | source |")
        lines.append("|---|---|---|")
        for asset_id in sorted(result.grid_spreads.keys()):
            lines.append(
                f"| {asset_id} | {result.grid_spreads[asset_id]:.4f} | "
                f"{result.grid_spread_sources.get(asset_id, 'n/a')} |"
            )
        lines.append("")

    # 数据覆盖
    lines.append("## Data Coverage")
    lines.append("")
    lines.append("| asset_id | first_date | last_date |")
    lines.append("|---|---|---|")
    for asset_id, (first, last) in sorted(result.data_coverage.items()):
        lines.append(f"| {asset_id} | {first} | {last} |")
    lines.append("")

    # NAV 曲线（月度末尾 ASCII 表）
    if result.nav is not None and not result.nav.empty:
        lines.append("## NAV (monthly last)")
        lines.append("")
        lines.append("| trade_date | equity |")
        lines.append("|---|---|")
        _month_key = result.nav["trade_date"].dt.strftime("%Y-%m")
        month_last = result.nav.iloc[_month_key.drop_duplicates(keep="last").index]
        for _, row in month_last.iterrows():
            lines.append(f"| {row['trade_date'].strftime('%Y-%m-%d')} | {row['equity']:.2f} |")
        lines.append("")

    # 交易摘要
    if result.trades is not None and not result.trades.empty:
        lines.append("## Trades (first/last 10)")
        lines.append("")
        lines.append("| trade_date | asset_id | side | shares | price | notional | fee |")
        lines.append("|---|---|---|---|---|---|---|")
        head = result.trades.head(10)
        for _, row in head.iterrows():
            lines.append(
                f"| {row['trade_date']} | {row['asset_id']} | {row['side']} | "
                f"{row['shares']} | {row['price']:.4f} | {row['notional']:.2f} | {row['fee']:.4f} |"
            )
        if len(result.trades) > 20:
            lines.append("| ... | ... | ... | ... | ... | ... | ... |")
        tail = result.trades.tail(10)
        for _, row in tail.iterrows():
            lines.append(
                f"| {row['trade_date']} | {row['asset_id']} | {row['side']} | "
                f"{row['shares']} | {row['price']:.4f} | {row['notional']:.2f} | {row['fee']:.4f} |"
            )
        lines.append("")

    # 假设
    lines.append("## Assumptions")
    lines.append("")
    for assumption in result.assumptions:
        lines.append(f"- {assumption}")
    lines.append("")
    lines.append("> B-side output; awaiting A confirmation. "
                 "This report is generated by systemB from the A-side strategy contract.")
    return "\n".join(lines)


def write_backtest_outputs(
    result: BacktestResult,
    output_root: str | Path,
    run_month: str | None = None,
) -> dict[str, Path]:
    """写三件套：nav.csv / trades.csv / {strategy_id}_{YYYYMM}.md。返回路径 dict。"""
    output_dir = Path(output_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    month = run_month or today_iso()[:7]

    header = (
        f"schema_version={_SCHEMA_VERSION}; source_system={_SOURCE_SYSTEM}; "
        f"generated_date={today_iso()}; strategy_id={result.strategy_id}"
    )
    written: dict[str, Path] = {}

    nav_path = output_dir / f"{result.strategy_id}_nav.csv"
    header_dict = {
        "schema_version": _SCHEMA_VERSION,
        "source_system": _SOURCE_SYSTEM,
        "generated_date": today_iso(),
        "strategy_id": result.strategy_id,
    }
    if result.nav is not None and not result.nav.empty:
        nav_csv = result.nav.copy()
        nav_csv["trade_date"] = nav_csv["trade_date"].dt.strftime("%Y-%m-%d")
        embed_header_csv(nav_path, header_dict, nav_csv)
        written["nav"] = nav_path
    else:
        nav_path.write_text(f"# {header}\n", encoding="utf-8")
        written["nav"] = nav_path

    trades_path = output_dir / f"{result.strategy_id}_trades.csv"
    if result.trades is not None and not result.trades.empty:
        result.trades.to_csv(trades_path, index=False)
    else:
        trades_path.write_text("trade_date,asset_id,side,shares,price,notional,fee,cash_impact\n",
                               encoding="utf-8")
    written["trades"] = trades_path

    md_path = output_dir / f"{result.strategy_id}_{month}.md"
    md_path.write_text(render_markdown(result), encoding="utf-8")
    written["md"] = md_path
    return written


__all__ = [
    "BACKTEST_START_DEFAULT",
    "DEFAULT_GRID_SPREAD",
    "DEFAULT_REBALANCE_FREQ",
    "GRID_LEVELS_PER_SIDE",
    "MACRO_TILT_RATE",
    "TRADE_LOT_SIZE",
    "BacktestContract",
    "BacktestResult",
    "ContractAsset",
    "ContractStrategy",
    "CostConfig",
    "GridConfig",
    "TradeRecord",
    "apply_signal_filters",
    "build_phase_lookup",
    "build_trading_calendar",
    "buy_cash_out",
    "compute_target_barbell",
    "compute_target_midline",
    "execution_price",
    "grid_target_weight",
    "load_benchmark_returns",
    "load_grid_reference",
    "load_price_frames",
    "parse_contract",
    "phase_asof",
    "rebalance_dates",
    "render_markdown",
    "resolve_grid_spread",
    "resolve_rebalance_frequency",
    "round_lot",
    "run_backtest",
    "sell_cash_in",
    "trade_fee",
    "write_backtest_outputs",
]
