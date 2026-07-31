#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
宏观因子驱动资产配置系统 — 入口脚本
====================================

功能：
  --init      首次初始化：下载全部因子历史数据到本地
  --update    联网更新最新数据
  --dashboard 输出详细宏观仪表盘
  --backtest  运行宏观信号回测验证
  --optimize  网格搜索最优化参数
  无参数      输出当前宏观评分报告

使用示例：
  python run_macro.py --init          # 首次初始化
  python run_macro.py                 # 输出评分
  python run_macro.py --dashboard     # 详细仪表盘
  python run_macro.py --backtest      # 回测验证
  python run_macro.py --optimize      # 参数优化

Windows 中文编码：
  python -X utf8 run_macro.py [参数]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """主入口。"""
    parser = argparse.ArgumentParser(
        description="宏观因子驱动资产配置系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python run_macro.py --init          首次初始化（联网下载）
  python run_macro.py                 输出当前评分
  python run_macro.py --dashboard     详细仪表盘
  python run_macro.py --backtest      回测验证
  python run_macro.py --optimize      参数优化
        """,
    )

    parser.add_argument("--init", action="store_true", help="首次初始化：下载全部因子历史数据")
    parser.add_argument("--update", action="store_true", help="联网更新最新数据")
    parser.add_argument("--dashboard", action="store_true", help="输出宏观仪表盘")
    parser.add_argument("--backtest", action="store_true", help="运行回测验证")
    parser.add_argument("--optimize", action="store_true", help="参数网格搜索优化")
    parser.add_argument("--chart-dir", default="", help="图表保存目录（默认 data/../charts）")

    args = parser.parse_args(argv)

    # --init 下载全部历史数据
    if args.init:
        return _cmd_init()

    # --update 更新最新数据
    if args.update:
        return _cmd_update()

    # --backtest 回测
    if args.backtest:
        return _cmd_backtest()

    # --optimize 参数优化
    if args.optimize:
        return _cmd_optimize()

    # --dashboard 仪表盘
    if args.dashboard:
        return _cmd_dashboard(args)

    # 无参数：输出评分
    return _cmd_signal()


def _cmd_init() -> int:
    """--init：首次初始化，下载全部因子历史数据。"""
    print("=" * 70)
    print("  首次初始化：下载宏观因子历史数据")
    print("=" * 70)
    print("  数据将保存至：data/macro/")
    print()

    from qteasy_research.macro.factors import MacroDataFetcher
    fetcher = MacroDataFetcher()
    result = fetcher.fetch_all()

    success = sum(1 for v in result.values() if not v.empty)
    total = len(result)
    print(f"\n下载完成：{success}/{total} 个因子成功")

    # 保存到本地
    print("\n保存到本地...")
    for name, series in result.items():
        if not series.empty:
            from qteasy_research.macro.config import MACRO_DATA_DIR
            filepath = MACRO_DATA_DIR / f"{name}.csv"
            series.to_frame(name=name).to_csv(filepath, encoding="utf-8")
            print(f"  ✅ {name}: {len(series)} 行 -> {filepath.name}")

    print("\n初始化完成。以后只需运行：")
    print("  python run_macro.py --update   # 更新最新数据")
    print("  python run_macro.py            # 输出评分")
    return 0


def _cmd_update() -> int:
    """--update：联网更新最新数据。"""
    print("=" * 70)
    print("  更新宏观因子数据")
    print("=" * 70)

    from qteasy_research.macro.factors import MacroDataFetcher
    fetcher = MacroDataFetcher()
    fetcher.update_all()
    return 0


def _cmd_signal() -> int:
    """无参数：输出当前宏观评分。"""
    try:
        from qteasy_research.macro.signal import generate_signal, print_report
        report = generate_signal()
        print_report(report)
        return 0
    except RuntimeError as e:
        print(e)
        return 1


def _cmd_dashboard(args) -> int:
    """--dashboard：输出宏观仪表盘。"""
    from qteasy_research.macro.dashboard import MacroDashboard
    dash = MacroDashboard()

    # 因子状态表格
    dash.print_factor_table()

    # 评分
    try:
        from qteasy_research.macro.signal import generate_signal, print_report
        report = generate_signal(verbose=False)
        print_report(report)
    except RuntimeError as e:
        print(e)

    # 图表
    chart_dir = args.chart_dir or ""
    if chart_dir:
        print("生成因子趋势图...")
        dash.plot_all_factors(output_dir=chart_dir)
    else:
        print("（要保存图表，请指定 --chart-dir 目录）")

    return 0


def _cmd_backtest() -> int:
    """--backtest：运行宏观信号回测。"""
    print("=" * 70)
    print("  宏观信号回测验证")
    print("=" * 70)

    from qteasy_research.macro.backtest import build_signal_history

    print("构建历史评分信号...")
    signals = build_signal_history()
    print(f"信号覆盖：{signals.index.min().date()} ~ {signals.index.max().date()}")
    print(f"信号数量：{len(signals)}")

    # 统计信号分布
    action_counts = signals["action"].value_counts()
    print(f"\n信号分布：")
    for action, count in action_counts.items():
        print(f"  {action}: {count} 次 ({count / len(signals):.1%})")
    print(f"\n平均评分：{signals['score'].mean():.3f}")
    print(f"评分标准差：{signals['score'].std():.3f}")

    return 0


def _cmd_optimize() -> int:
    """--optimize：参数网格搜索。"""
    from qteasy_research.macro.backtest import grid_search
    grid_search()
    return 0


if __name__ == "__main__":
    sys.exit(main())
