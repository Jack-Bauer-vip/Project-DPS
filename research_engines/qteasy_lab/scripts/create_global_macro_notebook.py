"""Create the reproducible global macro condition-return notebook."""

from __future__ import annotations

from pathlib import Path

import nbformat as nbf


def build_notebook() -> nbf.NotebookNode:
    notebook = nbf.v4.new_notebook()
    notebook.metadata.update({
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.11+"},
    })
    notebook.cells = [
        nbf.v4.new_markdown_cell(
            "# 全球 ETF 宏观条件收益研究\n\n"
            "本 Notebook 使用本地快照研究 SPY、TLT、GLD 在利率和实际利率状态下的条件收益。"
        ),
        nbf.v4.new_markdown_cell(
            "## 使用说明\n\n"
            "执行后请根据最后的条件收益表填写结论。本 Notebook 不生成交易指令，也不会自动修改 A 股评分器。"
        ),
        nbf.v4.new_markdown_cell(
            "## 方法与边界\n\n"
            "- 少量 ETF 不进行 5 组或 10 组横截面分组，改用宏观状态条件收益。\n"
            "- 日频利率先聚合到月末；资产收益使用月度调整收盘价变化。\n"
            "- 所有数据同时满足 observation_date <= target_date 和 available_at <= target_date，避免未来函数。\n"
            "- TLT 的利率代理优先使用 DGS30；缺少时回退 DGS10，并标记精度降级。\n\n"
            "### 默认阈值\n\n"
            "利率月度变化 >= +0.20 个百分点定义为加息/利率上行，<= -0.20 个百分点定义为降息/利率下行；实际利率变化阈值为 ±0.10 个百分点。"
        ),
        nbf.v4.new_markdown_cell(
            "## 数据\n\n读取 `data/processed/global_macro/*.csv`。请先运行 `fetch_global_macro_data.py`。"
        ),
        nbf.v4.new_code_cell(
            "from pathlib import Path\n"
            "import numpy as np\n"
            "import pandas as pd\n\n"
            "# 兼容从 qteasy_lab 根目录或 notebooks/ 目录运行：定位 data/processed/global_macro\n"
            "CANDIDATES = [\n"
            "    Path.cwd() / 'data' / 'processed' / 'global_macro',\n"
            "    Path.cwd().parent / 'data' / 'processed' / 'global_macro',\n"
            "]\n"
            "DATA_DIR = next((p for p in CANDIDATES if p.exists()), None)\n"
            "if DATA_DIR is None:\n"
            "    raise FileNotFoundError(f'未找到本地宏观数据目录：{CANDIDATES}')\n"
            "TARGET_DATE = pd.Timestamp.today().normalize()\n"
            "ASSETS = ['SPY', 'TLT', 'GLD']\n"
            "print('数据目录：', DATA_DIR.resolve())\n"
            "print('目标日期：', TARGET_DATE.date())"
        ),
        nbf.v4.new_code_cell(
            "def load_series(series_id: str) -> pd.DataFrame:\n"
            "    path = DATA_DIR / f'{series_id}.csv'\n"
            "    if not path.exists():\n"
            "        return pd.DataFrame(columns=['series_id', 'observation_date', 'available_at', 'value', 'quality_level'])\n"
            "    frame = pd.read_csv(path)\n"
            "    frame['observation_date'] = pd.to_datetime(frame['observation_date'], errors='coerce')\n"
            "    frame['available_at'] = pd.to_datetime(frame['available_at'], errors='coerce')\n"
            "    frame['value'] = pd.to_numeric(frame['value'], errors='coerce')\n"
            "    frame = frame.dropna(subset=['observation_date', 'available_at', 'value'])\n"
            "    frame = frame.loc[(frame['observation_date'] <= TARGET_DATE) & (frame['available_at'] <= TARGET_DATE)]\n"
            "    return frame.sort_values('observation_date')\n\n"
            "macro = {series_id: load_series(series_id) for series_id in ['DGS10', 'DGS2', 'DGS30', 'DFII10']}\n"
            "asset_prices = {series_id: load_series(series_id) for series_id in ASSETS}\n"
            "print({key: len(value) for key, value in {**macro, **asset_prices}.items()})"
        ),
        nbf.v4.new_markdown_cell("## 月度宏观状态\n\n按月末聚合利率与 ETF 价格，并建立利率状态。"),
        nbf.v4.new_code_cell(
            "def month_end(frame: pd.DataFrame) -> pd.Series:\n"
            "    if frame.empty:\n"
            "        return pd.Series(dtype='float64')\n"
            "    return frame.set_index('observation_date')['value'].resample('ME').last()\n\n"
            "rate10 = month_end(macro['DGS10'])\n"
            "rate2 = month_end(macro['DGS2'])\n"
            "rate30 = month_end(macro['DGS30'])\n"
            "real10 = month_end(macro['DFII10'])\n"
            "rate_level = rate30 if not rate30.empty else rate10\n"
            "rate_proxy = 'DGS30' if not rate30.empty else 'DGS10'\n"
            "state_frame = pd.DataFrame({'rate': rate_level, 'real_rate': real10})\n"
            "state_frame['spread_10y2y'] = rate10 - rate2\n"
            "state_frame['rate_change'] = state_frame['rate'].diff()\n"
            "state_frame['real_rate_change'] = state_frame['real_rate'].diff()\n"
            "state_frame['rate_state'] = np.select(\n"
            "    [state_frame['rate_change'] >= 0.20, state_frame['rate_change'] <= -0.20],\n"
            "    ['加息/利率上行', '降息/利率下行'], default='利率平稳'\n"
            ")\n"
            "state_frame['term_state'] = np.where(state_frame['spread_10y2y'] < 0, '期限结构倒挂', '期限结构正常')\n"
            "state_frame['real_rate_state'] = np.select(\n"
            "    [state_frame['real_rate_change'] >= 0.10, state_frame['real_rate_change'] <= -0.10],\n"
            "    ['实际利率上行', '实际利率下行'], default='实际利率平稳'\n"
            ")\n"
            "state_frame.tail()"
        ),
        nbf.v4.new_markdown_cell("## 条件收益表\n\n输出各资产在不同宏观状态下的月均收益、波动、最大回撤和胜率。"),
        nbf.v4.new_code_cell(
            "def asset_monthly_returns(series_id: str) -> pd.Series:\n"
            "    return month_end(asset_prices[series_id]).pct_change().rename(series_id)\n\n"
            "def conditional_stats(asset: str, state_name: str, state_series: pd.Series, returns: pd.Series) -> dict:\n"
            "    frame = pd.concat([state_series.rename('state'), returns.rename('return')], axis=1).dropna()\n"
            "    frame = frame.loc[frame['state'] == state_name]\n"
            "    if frame.empty:\n"
            "        return {'asset': asset, 'macro_state': state_name, 'sample_count': 0, 'monthly_return': None, 'monthly_volatility': None, 'max_drawdown': None, 'win_rate': None, 'sample_start': None, 'sample_end': None}\n"
            "    nav = (1.0 + frame['return']).cumprod()\n"
            "    drawdown = nav / nav.cummax() - 1.0\n"
            "    return {'asset': asset, 'macro_state': state_name, 'sample_count': int(len(frame)),\n"
            "            'monthly_return': float(frame['return'].mean()),\n"
            "            'monthly_volatility': float(frame['return'].std(ddof=0)),\n"
            "            'max_drawdown': float(drawdown.min()),\n"
            "            'win_rate': float((frame['return'] > 0).mean()),\n"
            "            'sample_start': frame.index.min().date().isoformat(),\n"
            "            'sample_end': frame.index.max().date().isoformat()}\n\n"
            "rows = []\n"
            "for asset in ASSETS:\n"
            "    returns = asset_monthly_returns(asset)\n"
            "    rows.extend(conditional_stats(asset, state, state_frame['rate_state'], returns) for state in ['加息/利率上行', '降息/利率下行'])\n"
            "    rows.extend(conditional_stats(asset, state, state_frame['real_rate_state'], returns) for state in ['实际利率上行', '实际利率下行'])\n"
            "condition_returns = pd.DataFrame(rows)\n"
            "condition_returns.to_csv(DATA_DIR / 'condition_returns.csv', index=False, encoding='utf-8-sig')\n"
            "baseline_rows = [\n"
            "    {'asset': asset, 'baseline_monthly_return': float(asset_monthly_returns(asset).mean()), 'baseline_sample_count': int(len(asset_monthly_returns(asset)))}\n"
            "    for asset in ASSETS\n"
            "]\n"
            "pd.DataFrame(baseline_rows).to_csv(DATA_DIR / 'baseline_returns.csv', index=False, encoding='utf-8-sig')\n"
            "display_columns = {'asset': '资产', 'macro_state': '宏观状态', 'sample_count': '样本数', 'monthly_return': '月均收益', 'monthly_volatility': '月均波动', 'max_drawdown': '最大回撤', 'win_rate': '胜率'}\n"
            "condition_returns.rename(columns=display_columns)"
        ),
        nbf.v4.new_markdown_cell(
            "## 五档离散 modifier 建议\n\n"
            "按「条件月均收益 − 全期基准月均收益」的偏差（百分点）离散分档，**仅供人工参考，不自动生效**：\n\n"
            "| 偏差(pp) | modifier | 含义 |\n"
            "|---|---|---|\n"
            "| ≥ +1.5 | 1.15 | 强烈利好 |\n"
            "| ≥ +0.5 且 < +1.5 | 1.08 | 温和利好 |\n"
            "| > −0.5 且 < +0.5 | 1.00 | 中性 |\n"
            "| ≤ −0.5 且 > −1.5 | 0.92 | 温和利空 |\n"
            "| ≤ −1.5 | 0.85 | 强烈利空 |\n\n"
            "样本联动：<24 个月仅作参考（REFERENCE_ONLY）；24–59 为候选（CANDIDATE）；≥60 才允许 APPROVED。\n"
            "生效的 modifier 只能来自数据库中人工确认的 APPROVED 规则；无数据状态（期限结构/利率平稳等）为 1.00 保守中性，不落库。"
        ),
        nbf.v4.new_code_cell(
            "import sys\n"
            "from pathlib import Path\n"
            "PROJECT_ROOT = Path.cwd() if (Path.cwd() / 'qteasy_research').exists() else Path.cwd().parent\n"
            "if str(PROJECT_ROOT) not in sys.path:\n"
            "    sys.path.insert(0, str(PROJECT_ROOT))\n"
            "from qteasy_research.core.global_etf_engine import suggest_modifier_from_condition_returns\n\n"
            "STATE_MAP = {\n"
            "    '加息/利率上行': 'rate_up', '降息/利率下行': 'rate_down', '利率平稳': 'rate_stable',\n"
            "    '期限结构倒挂': 'curve_inverted', '期限结构正常': 'curve_normal',\n"
            "    '实际利率上行': 'real_yield_up', '实际利率下行': 'real_yield_down', '实际利率平稳': 'real_yield_stable',\n"
            "}\n"
            "baseline_returns = pd.read_csv(DATA_DIR / 'baseline_returns.csv')\n"
            "suggest_rows = []\n"
            "for _, row in condition_returns.iterrows():\n"
            "    if row['sample_count'] <= 0:\n"
            "        continue\n"
            "    base = baseline_returns.loc[baseline_returns['asset'] == row['asset'], 'baseline_monthly_return'].iloc[0]\n"
            "    s = suggest_modifier_from_condition_returns(\n"
            "        condition_return=float(row['monthly_return']) * 100,\n"
            "        baseline_return=float(base) * 100,\n"
            "        sample_count=int(row['sample_count']),\n"
            "    )\n"
            "    suggest_rows.append({\n"
            "        '资产': row['asset'], '宏观状态': row['macro_state'], '引擎状态': STATE_MAP[row['macro_state']],\n"
            "        '样本数': int(row['sample_count']),\n"
            "        '月均收益%': round(float(row['monthly_return']) * 100, 2),\n"
            "        '全期基准%': round(float(base) * 100, 2),\n"
            "        '偏差pp': round((float(row['monthly_return']) - float(base)) * 100, 2),\n"
            "        '建议modifier': s['modifier'], '置信度': s['confidence'], '候选等级': s['status'],\n"
            "    })\n"
            "suggest_table = pd.DataFrame(suggest_rows)\n"
            "suggest_table"
        ),
        nbf.v4.new_code_cell(
            "if state_frame.empty:\n"
            "    print('无宏观数据，无法识别当前状态。')\n"
            "else:\n"
            "    current_states = [\n"
            "        STATE_MAP[state_frame['rate_state'].iloc[-1]],\n"
            "        STATE_MAP[state_frame['term_state'].iloc[-1]],\n"
            "        STATE_MAP[state_frame['real_rate_state'].iloc[-1]],\n"
            "    ]\n"
            "    print('当前宏观状态三元组：', ' + '.join(current_states))\n"
            "    print()\n"
            "    print('候选规则后果：')\n"
            "    print('- 利率状态样本 <60 个月 → 仅 CANDIDATE，不能 APPROVED；')\n"
            "    print('- 期限结构状态无条件收益数据 → 保守中性，不落库；')\n"
            "    print('- 实际利率状态样本 ≥60 个月 → 可 APPROVED。')\n"
            "    print()\n"
            "    print('三元组中任一分量缺少 APPROVED 规则时，引擎返回 PARTIAL（base_score 可算，宏观修正为空），属预期安全行为。')"
        ),
        nbf.v4.new_markdown_cell("## 研究结论边界\n\n结论只能基于条件收益表；样本不足 24 个月的状态仅作参考，不进入正式宏观修正。"),
        nbf.v4.new_code_cell(
            "available = condition_returns.loc[condition_returns['sample_count'] > 0].copy()\n"
            "print('利率回归代理：', rate_proxy)\n"
            "print('有效条件结果行数：', len(available))\n"
            "if available.empty:\n"
            "    print('当前没有足够的本地数据，请先运行数据下载脚本。')\n"
            "else:\n"
            "    print('请人工比较各资产在不同宏观状态下的月均收益、最大回撤和胜率。')\n"
            "    print('样本数少于 24 个月的状态仅作参考，不进入正式宏观修正。')"
        ),
    ]
    return notebook


if __name__ == "__main__":
    path = Path("notebooks/global_macro_lab.ipynb")
    path.parent.mkdir(parents=True, exist_ok=True)
    nbf.write(build_notebook(), path)
    print(path)
