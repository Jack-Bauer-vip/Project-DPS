"""量化策略源码导入、静态分析和候选回测策略生成。

本模块不会执行用户上传的原始 TXT/IPYNB。解析阶段只读取文本和 Notebook
单元；生成阶段只生成受支持的策略模板。只有用户在桌面端明确点击确认后，
才会加载由本模块生成且哈希未被修改的候选文件进行回测。
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


MAX_SOURCE_BYTES = 2_000_000
SUPPORTED_SUFFIXES = {".txt", ".ipynb"}

_FAMILY_LABELS = {
    "equal_weight": "等权配置",
    "momentum": "动量/相对强弱",
    "risk_parity": "风险平价",
    "inverse_vol": "波动率倒数加权",
    "trend_following": "趋势跟随",
    "mean_reversion": "均值回归",
    "custom": "自定义策略",
}

_INDICATOR_LABELS = {
    "moving_average": "移动平均线",
    "momentum": "动量/收益率",
    "volatility": "波动率",
    "rsi": "RSI",
    "macd": "MACD",
    "bollinger": "布林带",
    "atr": "ATR",
    "adx": "ADX",
    "ranking": "横截面排名",
}

_DATA_LABELS = {
    "open": "开盘价",
    "high": "最高价",
    "low": "最低价",
    "close": "收盘价",
    "volume": "成交量",
    "amount": "成交额",
    "fund_daily": "基金日线",
    "stock_daily": "股票日线",
    "financial": "财务数据",
}


@dataclass
class StrategyAnalysis:
    import_id: str
    source_name: str
    source_path: str
    source_type: str
    source_hash: str
    source_bytes: int
    framework: str = "custom"
    strategy_family: str = "custom"
    confidence: float = 0.0
    summary: str = ""
    implementation_summary: str = ""
    indicators: list[str] = field(default_factory=list)
    data_dependencies: list[str] = field(default_factory=list)
    signal_rules: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    markdown_notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    runnable_template: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_source(path: Path) -> tuple[str, str, int]:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"策略文件不存在：{path}")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError("只支持 .txt 和 .ipynb 策略文件")
    size = path.stat().st_size
    if size > MAX_SOURCE_BYTES:
        raise ValueError(f"策略文件超过 {MAX_SOURCE_BYTES // 1_000_000} MB 限制")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if suffix == ".ipynb":
        try:
            notebook = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Notebook 文件无效：{exc}") from exc
        sections: list[str] = []
        for index, cell in enumerate(notebook.get("cells", []), start=1):
            cell_type = cell.get("cell_type", "unknown")
            source = "".join(cell.get("source", []))
            if source.strip():
                sections.append(f"# --- cell {index} ({cell_type}) ---\n{source}")
        return "\n\n".join(sections), digest, size
    return raw.decode("utf-8-sig", errors="replace"), digest, size


def _source_ast(source: str) -> tuple[ast.AST | None, list[str]]:
    try:
        return ast.parse(source), []
    except SyntaxError as exc:
        return None, [f"源码存在语法错误，已继续做文本级分析：第 {exc.lineno} 行 {exc.msg}"]


def _collect_ast_details(tree: ast.AST | None) -> tuple[list[str], list[str], list[str], dict[str, Any]]:
    if tree is None:
        return [], [], [], {}
    imports: set[str] = set()
    functions: list[str] = []
    classes: list[str] = []
    parameters: dict[str, Any] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and (
                    target.id.isupper()
                    or any(token in target.id.lower() for token in ("lookback", "window", "period", "threshold", "top_n"))
                ):
                    try:
                        parameters[target.id] = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        parameters[target.id] = "expression"
    return sorted(imports), sorted(set(functions)), sorted(set(classes)), parameters


def _find_terms(source: str, patterns: dict[str, tuple[str, ...]]) -> list[str]:
    lowered = source.lower()
    return [key for key, terms in patterns.items() if any(term.lower() in lowered for term in terms)]


def _extract_lines(source: str, patterns: tuple[str, ...], limit: int = 8) -> list[str]:
    result: list[str] = []
    for raw_line in source.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if line and any(term.lower() in lowered for term in patterns):
            result.append(line[:240])
        if len(result) >= limit:
            break
    return result


def _detect_family(source: str, indicators: list[str]) -> tuple[str, float]:
    lowered = source.lower()
    rules = [
        ("risk_parity", ("risk parity", "risk_parity", "风险平价", "equal risk"), 0.92),
        ("inverse_vol", ("inverse volatility", "inverse_vol", "波动率倒数", "1 / volatility"), 0.90),
        ("momentum", ("momentum", "lookback", "relative strength", "动量", "top_n", "roc("), 0.86),
        ("mean_reversion", ("mean reversion", "mean_reversion", "均值回归", "zscore", "z-score", "布林带"), 0.82),
        ("trend_following", ("trend following", "trend_following", "moving average crossover", "均线交叉", "golden cross", "death cross"), 0.78),
        ("equal_weight", ("equal weight", "equal_weight", "等权", "1.0 / len", "平均分配"), 0.72),
    ]
    for family, terms, confidence in rules:
        if any(term.lower() in lowered for term in terms):
            return family, confidence
    if "ranking" in indicators or "momentum" in indicators:
        return "momentum", 0.62
    if "moving_average" in indicators:
        return "trend_following", 0.58
    return "custom", 0.25


def _detect_framework(imports: list[str], source: str) -> str:
    joined = " ".join(imports).lower() + " " + source.lower()
    if "qteasy" in joined or "generalstg" in joined:
        return "qteasy"
    if "backtrader" in joined:
        return "backtrader"
    if "vectorbt" in joined:
        return "vectorbt"
    if "zipline" in joined:
        return "zipline"
    if "pandas" in joined or "numpy" in joined:
        return "pandas/numpy"
    return "custom"


def analyze_strategy_file(path: str | Path) -> StrategyAnalysis:
    source_path = Path(path).expanduser().resolve()
    source, source_hash, source_bytes = _read_source(source_path)
    tree, warnings = _source_ast(source)
    imports, functions, classes, parameters = _collect_ast_details(tree)
    indicator_keys = _find_terms(source, {
        "moving_average": ("moving average", "moving_average", "sma", "ema", "均线"),
        "momentum": ("momentum", "roc", "pct_change", "动量"),
        "volatility": ("volatility", "std(", "波动率"),
        "rsi": ("rsi", "相对强弱指标"),
        "macd": ("macd",),
        "bollinger": ("bollinger", "布林带"),
        "atr": ("atr", "平均真实波幅"),
        "adx": ("adx",),
        "ranking": ("rank(", "argsort", "排名", "top_n"),
    })
    data_keys = _find_terms(source, {
        "open": ("open", "开盘"),
        "high": ("high", "最高"),
        "low": ("low", "最低"),
        "close": ("close", "收盘", "price"),
        "volume": ("volume", "vol", "成交量"),
        "amount": ("amount", "成交额"),
        "fund_daily": ("fund_daily", "基金日线"),
        "stock_daily": ("stock_daily", "股票日线"),
        "financial": ("income", "balance_sheet", "财务", "fundamental"),
    })
    family, confidence = _detect_family(source, indicator_keys)
    framework = _detect_framework(imports, source)
    signal_rules = _extract_lines(source, ("buy", "sell", "long", "short", "entry", "exit", "realize", "target_weight", "signal", "买入", "卖出", "信号"))
    markdown_notes = _extract_lines(source, ("#", "##", "strategy", "策略", "说明", "逻辑", "目的"), limit=10)
    indicator_text = "、".join(_INDICATOR_LABELS[key] for key in indicator_keys) or "未识别出标准技术指标"
    data_text = "、".join(_DATA_LABELS[key] for key in data_keys) or "未识别出明确数据字段"
    family_text = _FAMILY_LABELS[family]
    summary = f"源码更接近“{family_text}”，识别置信度 {_confidence_text(confidence)}；主要使用 {indicator_text}。"
    implementation = f"检测到 {framework} 框架线索；包含 {len(classes)} 个类、{len(functions)} 个函数，数据依赖为 {data_text}。"
    if family == "custom":
        warnings.append("未识别为现有模板支持的策略家族，生成代码只能作为待人工补全的候选骨架。")
    runnable = family in {"equal_weight", "momentum", "risk_parity", "inverse_vol", "trend_following", "mean_reversion"}
    if not signal_rules:
        warnings.append("没有定位到明确的买入、卖出或目标仓位规则，需人工确认信号定义。")
    return StrategyAnalysis(
        import_id=uuid.uuid4().hex,
        source_name=source_path.name,
        source_path=str(source_path),
        source_type=source_path.suffix.lower().lstrip("."),
        source_hash=source_hash,
        source_bytes=source_bytes,
        framework=framework,
        strategy_family=family,
        confidence=confidence,
        summary=summary,
        implementation_summary=implementation,
        indicators=indicator_keys,
        data_dependencies=data_keys,
        signal_rules=signal_rules,
        imports=imports,
        functions=functions,
        classes=classes,
        parameters=parameters,
        markdown_notes=markdown_notes,
        warnings=warnings,
        runnable_template=runnable,
    )


def _confidence_text(value: float) -> str:
    return f"{value:.0%}"


def _safe_name(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_") or "imported_strategy"
    if name[0].isdigit():
        name = f"strategy_{name}"
    return name.lower()


def _template_code(analysis: StrategyAnalysis, class_name: str) -> str:
    family = analysis.strategy_family
    params = analysis.parameters
    if family == "momentum":
        lookback = int(params.get("LOOKBACK", params.get("lookback", 126))) if isinstance(params.get("LOOKBACK", params.get("lookback", 126)), (int, float)) else 126
        top_n = int(params.get("TOP_N", params.get("top_n", 3))) if isinstance(params.get("TOP_N", params.get("top_n", 3)), (int, float)) else 3
        return f'''"""由策略源码导入器生成的候选策略；请人工核对信号逻辑。"""

from qteasy_research.strategies.base import BaseStrategy
from qteasy_research.strategies.momentum import MomentumStrategy


class {class_name}(BaseStrategy):
    name = "IMPORTED_MOMENTUM"
    description = "根据导入源码识别出的动量候选策略"

    def __init__(self, lookback: int = {lookback}, top_n: int = {top_n}, **kwargs):
        super().__init__(**kwargs)
        self._delegate = MomentumStrategy(lookback=lookback, top_n=top_n)

    def build(self):
        return self._delegate.build()
'''
    if family == "risk_parity":
        return f'''"""由策略源码导入器生成的候选策略；请人工核对风险定义。"""

from qteasy_research.strategies.base import BaseStrategy
from qteasy_research.strategies.risk_parity import RiskParityStrategy


class {class_name}(BaseStrategy):
    name = "IMPORTED_RISK_PARITY"
    description = "根据导入源码识别出的风险平价候选策略"

    def __init__(self, window_length: int = 60, **kwargs):
        super().__init__(**kwargs)
        self._delegate = RiskParityStrategy(window_length=window_length)

    def build(self):
        return self._delegate.build()
'''
    if family == "inverse_vol":
        return f'''"""由策略源码导入器生成的候选策略；请人工核对波动率定义。"""

from qteasy_research.strategies.base import BaseStrategy
from qteasy_research.strategies.inverse_vol import InverseVolatilityStrategy


class {class_name}(BaseStrategy):
    name = "IMPORTED_INVERSE_VOL"
    description = "根据导入源码识别出的波动率倒数候选策略"

    def __init__(self, window_length: int = 60, **kwargs):
        super().__init__(**kwargs)
        self._delegate = InverseVolatilityStrategy(window_length=window_length)

    def build(self):
        return self._delegate.build()
'''
    if family == "equal_weight":
        return f'''"""由策略源码导入器生成的候选策略；请人工核对调仓规则。"""

from qteasy_research.strategies.base import BaseStrategy
from qteasy_research.strategies.equal_weight import EqualWeightStrategy


class {class_name}(BaseStrategy):
    name = "IMPORTED_EQUAL_WEIGHT"
    description = "根据导入源码识别出的等权候选策略"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._delegate = EqualWeightStrategy()

    def build(self):
        return self._delegate.build()
'''
    if family in {"trend_following", "mean_reversion"}:
        mode = "trend_following" if family == "trend_following" else "mean_reversion"
        return f'''"""由策略源码导入器生成的候选策略；这是规则近似版，必须人工核对。"""

import numpy as np
import qteasy as qt
from qteasy_research.strategies.base import BaseStrategy


class {class_name}(BaseStrategy):
    name = "IMPORTED_{mode.upper()}"
    description = "导入源码的{_FAMILY_LABELS[family]}近似候选实现"

    def __init__(self, window_length: int = 60, **kwargs):
        super().__init__(**kwargs)
        self.window_length = window_length

    def build(self):
        window = self.window_length
        mode = "{mode}"

        class _Generated(qt.GeneralStg):
            def __init__(inner_self):
                super().__init__(
                    name="IMPORTED_{mode.upper()}",
                    description="导入源码的规则近似实现",
                    data_types=[qt.StgData("close", freq="d", asset_type="FD", window_length=window)],
                )

            def realize(inner_self):
                close = np.asarray(inner_self.get_data("close_FD_d"), dtype=float)
                if close.ndim != 2 or close.shape[0] < window:
                    return np.zeros(close.shape[1] if close.ndim == 2 else 0)
                latest = close[-1]
                mean = np.nanmean(close[-window:], axis=0)
                std = np.nanstd(close[-window:], axis=0)
                if mode == "trend_following":
                    selected = np.isfinite(latest) & np.isfinite(mean) & (latest > mean)
                else:
                    zscore = (latest - mean) / np.where(std > 0, std, np.nan)
                    selected = np.isfinite(zscore) & (zscore < -1.0)
                weights = np.zeros(close.shape[1], dtype=float)
                if selected.any():
                    weights[selected] = 1.0 / selected.sum()
                return weights

        return _Generated()
'''
    return f'''"""待人工补全的策略候选骨架。原始源码未被执行。"""

from qteasy_research.strategies.base import BaseStrategy


class {class_name}(BaseStrategy):
    name = "IMPORTED_CUSTOM"
    description = "未识别策略家族，需要人工补全信号逻辑"

    def build(self):
        raise NotImplementedError("该策略尚未映射到 qteasy_research 的可回测信号，请人工补全后再运行。")
'''


def generate_strategy_candidate(analysis: StrategyAnalysis, output_dir: str | Path) -> dict[str, Any]:
    root = Path(output_dir).expanduser().resolve()
    import_dir = root / "strategy_imports" / analysis.import_id
    import_dir.mkdir(parents=True, exist_ok=True)
    class_name = "Imported" + "".join(part.title() for part in _safe_name(analysis.source_name).split("_")) + "Strategy"
    code = _template_code(analysis, class_name)
    code_path = import_dir / "generated_strategy.py"
    code_path.write_text(code, encoding="utf-8")
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    manifest = {
        "import_id": analysis.import_id,
        "analysis": analysis.to_dict(),
        "generated_code_path": str(code_path),
        "generated_code_hash": code_hash,
        "class_name": class_name,
        "runnable": analysis.runnable_template,
        "generated_at": pd_now(),
    }
    manifest_path = import_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**manifest, "manifest_path": str(manifest_path), "code": code}


def save_strategy_analysis(analysis: StrategyAnalysis, output_dir: str | Path) -> Path:
    root = Path(output_dir).expanduser().resolve() / "strategy_imports" / analysis.import_id
    root.mkdir(parents=True, exist_ok=True)
    path = root / "analysis.json"
    path.write_text(json.dumps(analysis.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def pd_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def run_generated_strategy_backtest(
    manifest_path: str | Path,
    *,
    asset_pool: list[str],
    cash: float = 100_000.0,
    start: str = "20190801",
    end: str | None = None,
    benchmark: str = "000300.SH",
    run_freq: str = "ME",
    output_dir: str | Path = "",
) -> Any:
    """只运行本模块生成且未被修改的候选代码。"""
    manifest_file = Path(manifest_path).expanduser().resolve()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if not manifest.get("runnable"):
        raise ValueError("该策略没有生成可运行模板，请先人工补全策略逻辑")
    code_path = Path(manifest["generated_code_path"]).resolve()
    if hashlib.sha256(code_path.read_bytes()).hexdigest() != manifest.get("generated_code_hash"):
        raise ValueError("候选代码已被修改，请重新生成或人工确认后再运行")
    spec = importlib.util.spec_from_file_location("qteasy_imported_candidate", code_path)
    if spec is None or spec.loader is None:
        raise ImportError("无法加载候选策略模块")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    strategy_class = getattr(module, manifest["class_name"])
    from qteasy_research.backtesting.engine import BacktestConfig, BacktestEngine

    strategy = strategy_class()
    config = BacktestConfig(
        strategy=strategy,
        asset_pool=asset_pool,
        cash=cash,
        start=start,
        end=end,
        benchmark=benchmark,
        run_freq=run_freq,
        output_dir=output_dir,
    )
    return BacktestEngine(config).run()
