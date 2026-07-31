"""投前研究 Markdown 报告和必要图表。

报告层只负责展示和解释，不改变定量分析结果。原始精度仍保存在
``result.json`` 和 SQLite 快照中，Markdown 仅进行面向阅读的格式化。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from qteasy_research.pretrade.display import metadata_display_rows


_STATE_LABELS = {
    "bullish": "偏强",
    "bearish": "偏弱",
    "neutral": "中性",
    "weak": "偏弱",
    "medium": "中等",
    "strong": "偏强",
    "high": "高",
    "low": "低",
    "up": "向上",
    "down": "向下",
    "sideways": "横盘",
    "positive": "正向",
    "negative": "负向",
    "inside_band": "布林带内部",
    "above_upper": "突破上轨",
    "below_lower": "跌破下轨",
    "unavailable": "不可用",
    "insufficient_data": "数据不足",
}

_CONFIDENCE_LABELS = {
    "normal": "正常",
    "medium": "中等",
    "low": "较低",
    "unavailable": "不可用",
}

_WINDOW_LABELS = {
    "1m": "近1个月",
    "3m": "近3个月",
    "6m": "近6个月",
    "1y": "近1年",
    "3y": "近3年",
}

_RUN_STATUS_LABELS = {
    "COMPLETED": "已完成",
    "PARTIAL": "部分完成",
    "FAILED": "失败",
    "REVIEW": "待人工复核",
}

_ASSET_TYPE_LABELS = {
    "ETF": "ETF（交易型开放式指数基金）",
    "LOF": "LOF（上市型开放式基金）",
    "QDII": "QDII（境外投资基金）",
    "STOCK": "股票",
    "INDEX": "指数",
}

_EXCHANGE_LABELS = {
    "SH": "上海证券交易所（SH）",
    "SZ": "深圳证券交易所（SZ）",
    "BJ": "北京证券交易所（BJ）",
}


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in {"", "none", "nan", "nat"}:
        return True
    return False


def _as_float(value: Any) -> float | None:
    if _missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def _number(value: Any, digits: int = 2, unavailable: str = "不可用") -> str:
    number = _as_float(value)
    return unavailable if number is None else f"{number:,.{digits}f}"


def _pct(value: Any, digits: int = 2) -> str:
    number = _as_float(value)
    return "不可用" if number is None else f"{number:.{digits}%}"


def _ratio(value: Any, digits: int = 2) -> str:
    return _number(value, digits)


def _date(value: Any) -> str:
    if _missing(value):
        return "未知"
    parsed = pd.to_datetime(value, errors="coerce")
    return "未知" if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")


def _label(value: Any) -> str:
    if _missing(value):
        return "不可用"
    text = str(value)
    return _STATE_LABELS.get(text.lower(), text)


def _confidence(value: Any) -> str:
    if _missing(value):
        return "不可用"
    return _CONFIDENCE_LABELS.get(str(value).lower(), str(value))


def _run_status(value: Any) -> str:
    if _missing(value):
        return "未知"
    text = str(value).upper()
    return f"{_RUN_STATUS_LABELS.get(text, text)}（{text}）"


def _cell(value: Any) -> str:
    """让来源消息、证据文本安全地进入 Markdown 表格。"""
    if _missing(value):
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _value(node: Any, digits: int = 2) -> str:
    """读取技术指标常见的 {value: ..., reason: ...} 结构。"""
    if isinstance(node, dict):
        if "value" in node:
            return _number(node.get("value"), digits)
        return "不可用"
    return _number(node, digits)


def _reason(node: Any) -> str:
    if isinstance(node, dict) and node.get("reason"):
        return str(node["reason"])
    return "—"


def _factor_value(item: dict[str, Any]) -> str:
    name = str(item.get("name", ""))
    if any(token in name for token in ("动量", "波动", "回撤", "ATR", "Bollinger")):
        return _pct(item.get("value"))
    return _number(item.get("value"), 4)


def _risk_list(value: Any) -> list[str]:
    if _missing(value):
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if not _missing(item)]
    return [str(value)]


def _source_result(source: dict[str, Any]) -> str:
    if source.get("ok") is True or source.get("success") is True:
        return "成功"
    message = str(source.get("message", "")).lower()
    if "缺" in message or "missing" in message:
        return "字段不足"
    return "失败/降级"


def render_charts(analysis: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    series = analysis.get("series", {})
    dates = pd.to_datetime(series.get("dates", []))
    close = series.get("close", [])
    drawdown = series.get("drawdown", [])
    artifacts: dict[str, str] = {}
    if len(dates) > 0 and close:
        fig, ax = plt.subplots(figsize=(10, 4.5))
        base = close[0]
        ax.plot(dates, [value / base for value in close], color="#1769aa", linewidth=1.4)
        ax.set_title("归一化价格走势")
        ax.set_ylabel("起点 = 1")
        ax.grid(alpha=0.25)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=8))
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
        path = output_dir / "normalized_price.png"
        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)
        artifacts["normalized_price"] = str(path)
    if len(dates) > 0 and drawdown:
        fig, ax = plt.subplots(figsize=(10, 4.5))
        ax.fill_between(dates, drawdown, 0, color="#c0392b", alpha=0.35)
        ax.set_title("回撤曲线")
        ax.set_ylabel("回撤")
        ax.grid(alpha=0.25)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=8))
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
        path = output_dir / "drawdown.png"
        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)
        artifacts["drawdown"] = str(path)
    return artifacts


def render_markdown(result: dict[str, Any]) -> str:
    """渲染面向人工复核的报告。

    重要原则：百分比统一以百分号展示，价格/评分/比率分别使用不同精度；
    不能计算的指标显示“不可用”，不把缺失值伪装成 0。
    """
    identity = result.get("asset_identity", {})
    profile = result.get("instrument_analysis", {}).get("asset_profile") or {}
    metrics = result.get("quantitative_metrics", {})
    windows = metrics.get("windows", {})
    benchmark = result.get("benchmark_analysis", {})
    quality = metrics.get("quality", {})
    one_year = windows.get("1y", {})
    total = one_year.get("total_return")
    mdd = one_year.get("max_drawdown")
    code = identity.get("code") or "未知标的"
    name = identity.get("name") or code
    sample_days = quality.get("sample_days", 0)
    conclusion = f"{code} 已有 {_number(sample_days, 0)} 个交易日样本；近一年收益 {_pct(total)}，最大回撤 {_pct(mdd)}。"

    lines = [
        f"# {name} 投前研究报告",
        "",
        f"> 研究运行：`{_cell(result.get('run_id'))}`  |  状态：`{_run_status(result.get('run_status'))}`  |  数据截至：`{_date(result.get('data_as_of'))}`",
        "",
        "## 执行摘要（Executive Summary）",
        "",
        f"**核心结论：** {conclusion}",
        "",
    ]

    if benchmark.get("available"):
        lines.append(
            f"**组合角色提示：** 与基准的相关性为 {_ratio(benchmark.get('correlation'))}，说明该标的与当前基准的同涨同跌程度较低；但这不等同于低风险，仍需结合自身回撤和波动判断。"
        )
    else:
        lines.append("**比较限制：** 当前没有足够的基准数据，组合分散化判断需要后续补充基准后再确认。")
    lines.extend([
        "",
        f"**数据完整度：** {sample_days} 个有效收益样本，缺失项：{_cell('、'.join(map(str, result.get('missing_items', []))) if result.get('missing_items') else '无')}。",
        "",
        "## 1. 标的身份与研究范围",
        "",
        "| 项目 | 内容 |",
        "|---|---|",
        f"| 名称 | {_cell(name)} |",
        f"| 代码 | `{_cell(code)}` |",
        f"| 资产类型 | {_ASSET_TYPE_LABELS.get(str(identity.get('asset_type', '')).upper(), _label(identity.get('asset_type')))} |",
        f"| 市场 | {_EXCHANGE_LABELS.get(str(identity.get('exchange', '')).upper(), _cell(identity.get('exchange') or '未知'))} |",
        f"| 基础档案版本 | {_cell(profile.get('profile_version') or '未建立')} |",
        f"| 基础资料来源 | {_cell(profile.get('source') or '未知')} |",
        f"| 基础资料截至 | {_date(profile.get('as_of'))} |",
        f"| 跟踪基准 | {_cell(profile.get('benchmark') or identity.get('benchmark') or '未知')} |",
        f"| 数据截至 | {_date(result.get('data_as_of'))} |",
        f"| 样本区间 | {_date(quality.get('start_date'))} 至 {_date(quality.get('end_date'))} |",
        "",
    ])
    metadata_rows = metadata_display_rows(identity.get("metadata") or profile.get("fixed_metadata") or {})
    if metadata_rows:
        lines.extend([
            "### 基金基础资料",
            "",
            "| 字段 | 内容 |",
            "|---|---|",
        ])
        lines.extend(f"| {_cell(key)} | {_cell(value)} |" for key, value in metadata_rows)
        lines.append("")
    lines.extend([
        "本报告用于投前筛选和人工复核。收益、风险和技术指标由确定性程序计算；来源事实、联网补研和 AI 文字综合单独标识，不自动加入资产池或生成交易指令。",
        "",
        "## 2. 收益表现：中期收益为正，但近期波动和回撤需要重视",
        "",
        "下表中的收益率均以该窗口起点为基准；年化指标需要至少 60 个交易日，样本不足时显示为“不可用”。",
        "",
        "| 观察窗口 | 样本 | 区间收益 | 年化收益 | 年化波动 | 最大回撤 | 修复天数 | 夏普 | 置信度 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ])
    for window in ("1m", "3m", "6m", "1y", "3y"):
        item = windows.get(window, {})
        recovery = item.get("drawdown_recovery_days")
        lines.append(
            f"| {_WINDOW_LABELS[window]} | {_number(item.get('sample_days'), 0)} | {_pct(item.get('total_return'))} | "
            f"{_pct(item.get('annual_return'))} | {_pct(item.get('annual_volatility'))} | {_pct(item.get('max_drawdown'))} | "
            f"{_number(recovery, 0) if recovery is not None else '未修复/不可用'} | {_ratio(item.get('sharpe'))} | {_confidence(item.get('confidence'))} |"
        )

    lines.extend([
        "",
        "### 风险调整后收益",
        "",
        "夏普、索提诺和卡玛都是辅助比较指标，数值越高通常越好，但必须结合样本长度、市场环境和最大回撤一起看。",
        "",
        "| 观察窗口 | 夏普 | 索提诺 | 卡玛 |",
        "|---|---:|---:|---:|",
    ])
    for window in ("1m", "3m", "6m", "1y", "3y"):
        item = windows.get(window, {})
        lines.append(
            f"| {_WINDOW_LABELS[window]} | {_ratio(item.get('sharpe'))} | {_ratio(item.get('sortino'))} | {_ratio(item.get('calmar'))} |"
        )

    liquidity = metrics.get("liquidity", {})
    lines.extend([
        "",
        "### 流动性参考",
        "",
        "成交额越高通常越容易完成交易，但历史平均值不能保证当前时点的买卖价差和冲击成本。金额单位沿用数据源单位。",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 平均成交额 | {_number(liquidity.get('average_amount'), 2)} |",
        f"| 成交额中位数 | {_number(liquidity.get('median_amount'), 2)} |",
        f"| 最新成交额 | {_number(liquidity.get('latest_amount'), 2)} |",
        f"| 平均成交量 | {_number(liquidity.get('average_volume'), 2)} |",
        "",
        "### 极端波动与当前趋势参考",
        "",
        "| 指标 | 数值 | 解释 |",
        "|---|---:|---|",
        f"| VaR 95% | {_pct(metrics.get('var95'))} | 在历史样本中，约 5% 的单日收益低于该水平；不是保证的最大亏损。 |",
        f"| ES 95% | {_pct(metrics.get('es95'))} | 进入最差 5% 情景后的平均单日收益，比 VaR 更关注尾部风险。 |",
        f"| 最佳 / 最差单日 | {_pct(metrics.get('best_day'))} / {_pct(metrics.get('worst_day'))} | 反映历史单日波动边界，不代表未来必然重现。 |",
        f"| 最佳 / 最差单月 | {_pct(metrics.get('best_month'))} / {_pct(metrics.get('worst_month'))} | 用于观察月度持有体验。 |",
        f"| 日收益胜率 / 上涨月份比例 | {_pct(metrics.get('win_rate_daily'))} / {_pct(metrics.get('win_rate_monthly'))} | 胜率不等于收益质量，需和盈亏幅度一起看。 |",
        f"| 最新收盘价 | {_number(metrics.get('latest_close'), 4)} | 数据截至日的收盘价。 |",
        "",
        "| 均线 | 数值 | 当前价相对均线 |",
        "|---|---:|---:|",
    ])
    latest_close = _as_float(metrics.get("latest_close"))
    for key, label in (("ma20", "MA20"), ("ma60", "MA60"), ("ma120", "MA120"), ("ma250", "MA250")):
        average = _as_float(metrics.get("moving_averages", {}).get(key))
        relative = (latest_close / average - 1.0) if latest_close is not None and average else None
        lines.append(f"| {label} | {_number(average, 4)} | {_pct(relative)} |")

    lines.extend([
        "",
        "**如何理解：** 最大回撤表示从历史高点跌到随后低点的最大幅度；年化波动越高，持有过程中的净值波动通常越大。均线只用于描述趋势位置，不单独构成买卖信号。",
        "",
        "## 3. 与基准比较：相关性较低，超额表现需要确认基准适配性",
        "",
    ])
    if benchmark.get("available"):
        lines.extend([
            "| 指标 | 标的 | 基准/比较值 | 解释 |",
            "|---|---:|---:|---|",
            f"| 重叠交易日 | {_number(benchmark.get('overlap_days'), 0)} | — | 只有重叠交易日用于比较。 |",
            f"| 累计收益 | {_pct(benchmark.get('total_return'))} | {_pct(benchmark.get('benchmark_total_return'))} | 同一重叠区间的累计收益。 |",
            f"| 超额收益 | {_pct(benchmark.get('excess_total_return'))} | — | 标的累计收益减基准累计收益。 |",
            f"| Beta | {_ratio(benchmark.get('beta'))} | 1.00 | 衡量对基准日收益变化的敏感度。 |",
            f"| Alpha（年化近似） | {_pct(benchmark.get('alpha'))} | — | 基于当前样本和模型的年化相对表现估计。 |",
            f"| 相关性 | {_ratio(benchmark.get('correlation'))} | 1.00 | 越接近 1，同涨同跌越明显；接近 0 表示线性相关较低。 |",
            f"| 压力期相关性 | {_ratio(benchmark.get('stress_correlation'))} | — | 基准处于较差 5% 日收益时的相关性。 |",
            f"| 信息比率 | {_ratio(benchmark.get('information_ratio'))} | — | 超额收益相对其波动的稳定性参考。 |",
        ])
    else:
        lines.append(f"基准比较不可用：{_cell(benchmark.get('reason', '未取得基准数据'))}。")
    lines.extend([
        "",
        "**注意：** 黄金 ETF 与沪深 300 不是同类风险暴露。低相关性可以支持分散化判断，但不能替代对黄金价格、汇率、海外市场时差和产品跟踪质量的专项研究。",
        "",
        "## 4. 技术状态：短期动量偏弱，趋势与风险信号并不一致",
        "",
    ])
    technical = metrics.get("technical_analysis", {})
    indicators = technical.get("indicators", {})
    scores = technical.get("composite_scores", {})
    if technical:
        trend = scores.get("trend", {})
        momentum = scores.get("momentum", {})
        risk = scores.get("risk", {})
        agreement = scores.get("agreement", {})
        lines.extend([
            f"技术指标计算截至 {_date(technical.get('as_of'))}，公式版本为 `{_cell(technical.get('formula_version', 'unknown'))}`。评分范围为 0—100，风险评分越高表示风险压力越大，不是收益预测。",
            "",
            "| 合成维度 | 评分 | 状态 | 中文解释 |",
            "|---|---:|---|---|",
            f"| 趋势 | {_number(trend.get('value'), 1)} / 100 | {_label(trend.get('state'))} | 综合均线和 ADX 判断趋势方向及强度。 |",
            f"| 动量 | {_number(momentum.get('value'), 1)} / 100 | {_label(momentum.get('state'))} | 综合 MACD、RSI 和相对强弱判断近期动能。 |",
            f"| 风险压力 | {_number(risk.get('value'), 1)} / 100 | {_label(risk.get('state'))} | 综合 ATR、布林带和波动状态判断风险压力。 |",
            f"| 指标一致性 | — | {_label(agreement.get('state'))} | 不同指标方向一致时，结论可信度相对更高；相反则需要观望或人工复核。 |",
            "",
            "| 指标 | 当前值 | 状态 | 解释 |",
            "|---|---:|---|---|",
        ])
        macd = indicators.get("macd", {})
        rsi = indicators.get("rsi", {})
        atr = indicators.get("atr", {})
        adx = indicators.get("adx", {})
        bollinger = indicators.get("bollinger", {})
        lines.extend([
            f"| MACD 柱值 | {_number(macd.get('histogram', {}).get('value'), 4)} | {_label(macd.get('state'))} | 柱值反映 DIF 与 DEA 的差距及动能变化；本报告不将其直接作为交易指令。 |",
            f"| RSI14 | {_number(rsi.get('value', {}).get('value'), 2)} | {_label(rsi.get('state'))} | 0—100 的相对强弱指标，通常用于观察动能强弱，不单独判断买卖。 |",
            f"| ATR14 / 价格 | {_pct(atr.get('relative_to_price', {}).get('value'))} | {_label(atr.get('state'))} | ATR 衡量波动幅度，除以价格后便于不同资产比较。 |",
            f"| ADX14 | {_number(adx.get('value', {}).get('value'), 2)} | 方向：{_label(adx.get('direction'))} | ADX 主要反映趋势强度，方向需要结合 +DI/-DI 判断。 |",
            f"| 布林带位置 | {_pct(bollinger.get('position', {}).get('value'))} | {_label(bollinger.get('state'))} | 位置接近 0 表示下轨，接近 100% 表示上轨；超轨不等于必然反转。 |",
        ])
    else:
        lines.append("技术指标尚未生成。")

    factor_analysis = metrics.get("factor_analysis", {})
    lines.extend(["", "## 5. 分期限因子分析", ""])
    if factor_analysis:
        lines.append("因子模块只用于判断不同期限下的影响因素，不直接生成交易指令；当前版本不包含 KDJ。")
        for horizon, title in (("short", "短线因子"), ("medium", "中线因子"), ("long", "长线参考因子")):
            item = factor_analysis.get(horizon, {})
            if not item:
                continue
            lines.extend([
                "",
                f"### {title}",
                "",
                f"综合评分：**{_number(item.get('composite_score'), 1)} / 100**；状态：**{_label(item.get('state'))}**；一致性：**{_label(item.get('consistency'))}**；截至：{_date(item.get('as_of'))}。",
                "",
                "| 因子 | 当前值 | 方向 | 评分 | 置信度 | 公式/说明 |",
                "|---|---:|---|---:|---|---|",
            ])
            for factor in item.get("factors", []):
                lines.append(
                    f"| {_cell(factor.get('name'))} | {_factor_value(factor)} | {_label(factor.get('direction'))} | "
                    f"{_number(factor.get('score'), 1)} | {_confidence(factor.get('confidence'))} | {_cell(factor.get('formula'))} |"
                )
            if item.get("summary"):
                lines.append("")
                lines.append(f"**因子结论：** {_cell(item.get('summary'))}")
    else:
        lines.append("尚未生成分期限因子结果；请先生成一次新的研究版本。")

    lines.extend([
        "",
        "## 6. 数据质量与限制",
        "",
        "| 检查项 | 结果 | 判断 |",
        "|---|---:|---|",
        f"| 有效行情行数 | {_number(quality.get('rows'), 0)} | 用于计算的原始行情记录。 |",
        f"| 有效收益样本 | {_number(quality.get('sample_days'), 0)} | 60 日以上才允许年化指标；252 日以上才适合稳定年度判断。 |",
        f"| 缺失收盘价 | {_number(quality.get('missing_close'), 0)} | 0 为通过。 |",
        f"| 重复日期 | {_number(quality.get('duplicate_dates'), 0)} | 0 为通过。 |",
        f"| 非正收盘价 | {_number(quality.get('non_positive_close'), 0)} | 0 为通过。 |",
        f"| 异常单日波动（绝对值 > 20%） | {_number(quality.get('large_daily_moves'), 0)} | 数量较多时应核验复权、拆分或数据源。 |",
        f"| 年化指标条件 | {'满足' if quality.get('annual_metrics_allowed') else '不满足'} | 只代表样本条件满足，不代表结论没有风险。 |",
        f"| 年度判断条件 | {'满足' if quality.get('stable_annual_judgment') else '不满足'} | 252 个有效收益样本以上。 |",
        "",
    ])
    missing_items = result.get("missing_items") or []
    risks = _risk_list(result.get("risks"))
    lines.append(f"**缺失项：** {_cell('、'.join(map(str, missing_items)) if missing_items else '无')}。")
    lines.append("")
    if risks:
        lines.append("**规则化风险提示：**")
        lines.extend(f"- {risk_item}" for risk_item in risks)
    else:
        lines.append("**规则化风险提示：** 当前没有触发规则化风险提示；这不等于不存在投资风险，尤其要注意报告中的高波动、回撤和数据源降级信息。")

    lines.extend(["", "## 7. 结构化研究证据", ""])
    evidence = result.get("evidence") or []
    if evidence:
        lines.extend([
            "下表只展示已保存的来源事实，不把 AI 推断当作结构化数据事实。",
            "",
            "| 研究事实 | 值 | 来源 | 截至日期 | 官方/置信度 |",
            "|---|---|---|---|---:|",
        ])
        for item in evidence:
            official = "官方" if item.get("official") is True else "非官方/未知"
            confidence = _pct(item.get("confidence"), 0) if item.get("confidence") is not None else "不可用"
            lines.append(
                f"| {_cell(item.get('claim'))} | {_cell(item.get('value'))} | {_cell(item.get('source_url'))} | {_date(item.get('as_of'))} | {official} / {confidence} |"
            )
    else:
        lines.append("暂无经过来源校验的联网证据。当前结论主要基于本地行情和结构化指标，定性资料不完整。")

    lines.extend(["", "## 8. 数据来源与降级过程", ""])
    sources = result.get("source_status", [])
    if sources:
        lines.extend([
            "数据源按配置顺序尝试；失败后使用备用源或已有本地快照。报告中的旧版本不会因后续刷新而被覆盖。",
            "",
            "| 数据源 | 操作 | 结果 | 返回截至 | 说明 |",
            "|---|---|---|---|---|",
        ])
        for source in sources:
            lines.append(
                f"| `{_cell(source.get('source'))}` | {_cell(source.get('operation'))} | {_source_result(source)} | "
                f"{_date(source.get('as_of'))} | {_cell(source.get('message', ''))} |"
            )
    else:
        lines.append("未记录数据源状态。")

    lines.extend([
        "",
        "## 9. 研究边界与下一步",
        "",
        "本报告的定量指标由本地确定性程序计算；联网事实、AI 综合推断和人工确认结论需要分别核验。报告不会自动加入资产池、修改策略或生成交易指令。",
        "",
        "建议下一步：",
        "",
        "1. 补充基金规模、费率、跟踪指数、跟踪误差、折溢价和流动性等 ETF 产品资料。",
        "2. 对黄金现货/期货价格、美元和人民币汇率、海外休市及时差风险进行联网核验。",
        "3. 通过新研究版本更新数据，不覆盖当前报告；重点观察回撤修复、趋势评分和数据源是否恢复。",
        "",
        "---",
        "",
        "*展示说明：百分比保留 2 位小数，价格保留 4 位小数，评分保留 1 位小数；完整原始数值请查看 `result.json`。*",
    ])
    return "\n".join(lines)
