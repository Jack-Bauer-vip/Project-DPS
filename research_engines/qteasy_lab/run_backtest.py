#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
qteasy_research — 通用策略回测入口
====================================

一键运行 ETF 组合策略历史回测，支持多种策略类型。

Windows 编码注意：
    本脚本已在文件头设置 UTF-8 编码。
    如遇中文输出乱码，请使用以下命令运行：
        python -X utf8 run_backtest.py

使用方法
--------
    # 使用默认配置运行等权策略
    python run_backtest.py

    # 指定策略类型和参数
    python run_backtest.py --strategy momentum --lookback 126 --top-n 3

    # 指定资产池和资金
    python run_backtest.py --strategy risk_parity --cash 500000

    # 对比多个策略
    python run_backtest.py --compare

策略类型
--------
    equal_weight     : 等权配置（默认）
    risk_parity      : 风险平价（等风险贡献）
    inverse_vol      : 波动率倒数加权
    momentum         : 动量策略
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 确保可以导入 qteasy_research 包
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ============================================================
# 默认配置
# ============================================================

# 核心 ETF 资产池（全球资产配置）
DEFAULT_POOL = [
    "518880.SH",  # 黄金ETF华安
    "159941.SZ",  # 纳指ETF广发
    "513050.SH",  # 中概互联网ETF
    "513520.SH",  # 日经ETF华夏
    "512890.SH",  # 红利低波ETF华泰柏瑞
]

# 回测默认参数
DEFAULT_CASH = 100_000
DEFAULT_START = "20190801"
DEFAULT_FEE_RATE = 0.00016
DEFAULT_BENCHMARK = "000300.SH"
DEFAULT_STRATEGY = "equal_weight"

# 更多 ETF（用于扩展资产池）
EXTENDED_POOL = [
    "562800.SH",  # 稀有金属ETF
    "588230.SH",  # 科创200ETF
    "513650.SH",  # 标普500ETF
    "159985.SZ",  # 豆粕ETF
    "515180.SH",  # 红利ETF
    "515450.SH",  # 红利低波50ETF南方
    "159131.SZ",  # 港股通信息技术ETF华宝
    "159516.SZ",  # 半导体设备ETF国泰
]


# ============================================================
# 策略工厂
# ============================================================

def create_strategy(name: str, **kwargs):
    """
    根据名称创建策略实例。

    Parameters
    ----------
    name : str
        策略名称：equal_weight / risk_parity / inverse_vol / momentum。
    **kwargs:
        传递给策略构造函数的额外参数。

    Returns
    -------
    BaseStrategy
        策略实例。
    """
    from qteasy_research.strategies.equal_weight import EqualWeightStrategy
    from qteasy_research.strategies.risk_parity import RiskParityStrategy
    from qteasy_research.strategies.inverse_vol import InverseVolatilityStrategy
    from qteasy_research.strategies.momentum import MomentumStrategy

    strategy_map = {
        "equal_weight": EqualWeightStrategy,
        "risk_parity": RiskParityStrategy,
        "inverse_vol": InverseVolatilityStrategy,
        "momentum": MomentumStrategy,
    }

    cls = strategy_map.get(name)
    if cls is None:
        available = ", ".join(strategy_map.keys())
        raise ValueError(f"未知策略 '{name}'。可用策略：{available}")

    return cls(**kwargs)


# ============================================================
# 回测运行函数
# ============================================================

def run_single_backtest(
    strategy_name: str = DEFAULT_STRATEGY,
    asset_pool: list[str] | None = None,
    cash: float = DEFAULT_CASH,
    start: str = DEFAULT_START,
    end: str | None = None,
    fee_rate: float = DEFAULT_FEE_RATE,
    benchmark: str = DEFAULT_BENCHMARK,
    run_freq: str = "ME",
    visual: bool = False,
    strategy_params: dict | None = None,
) -> dict:
    """
    运行单个策略回测。

    Parameters
    ----------
    strategy_name : str
        策略名称。
    asset_pool : list[str], optional
        ETF 代码列表。默认使用 DEFAULT_POOL。
    cash : float
        初始资金。
    start : str
        回测开始日期，YYYYMMDD。
    end : str, optional
        回测结束日期。None=自动。
    fee_rate : float
        买卖费率。
    benchmark : str
        基准指数代码。
    run_freq : str
        调仓频率。
    visual : bool
        是否显示图表。
    strategy_params : dict, optional
        策略特定参数。

    Returns
    -------
    dict
        包含 BacktestResult 和策略名称的结果字典。
    """
    from qteasy_research.backtesting.engine import BacktestEngine, BacktestConfig

    if asset_pool is None:
        asset_pool = DEFAULT_POOL

    # 创建策略
    params = strategy_params or {}
    strategy = create_strategy(strategy_name, **params)

    # 创建配置
    config = BacktestConfig(
        strategy=strategy,
        asset_pool=asset_pool,
        cash=cash,
        start=start,
        end=end,
        buy_fee_rate=fee_rate,
        sell_fee_rate=fee_rate,
        benchmark=benchmark,
        run_freq=run_freq,
        visual=visual,
    )

    # 运行回测
    engine = BacktestEngine(config)
    result = engine.run()

    return {
        "strategy_name": strategy_name,
        "result": result,
    }


def run_comparison(
    strategies: list[str],
    asset_pool: list[str] | None = None,
    cash: float = DEFAULT_CASH,
    start: str = DEFAULT_START,
    fee_rate: float = DEFAULT_FEE_RATE,
    benchmark: str = DEFAULT_BENCHMARK,
) -> list[dict]:
    """
    运行多个策略并对比结果。

    Parameters
    ----------
    strategies : list[str]
        要运行的策略名称列表。
    asset_pool : list[str], optional
        ETF 代码列表。
    cash : float
        初始资金。
    start : str
        回测开始日期。
    fee_rate : float
        买卖费率。
    benchmark : str
        基准指数代码。

    Returns
    -------
    list[dict]
        每个策略的回测结果。
    """
    results = []
    for name in strategies:
        print(f"\n{'#'*70}")
        print(f"# 策略：{name}")
        print(f"{'#'*70}")

        out = run_single_backtest(
            strategy_name=name,
            asset_pool=asset_pool,
            cash=cash,
            start=start,
            fee_rate=fee_rate,
            benchmark=benchmark,
        )
        results.append(out)

    # 打印对比摘要（从 raw_result 字典提取数据）
    print("\n" + "=" * 70)
    print("策略对比摘要")
    print("=" * 70)

    for out in results:
        result = out["result"]
        name = out["strategy_name"]

        if not result.success:
            print(f"\n  {name}: ❌ 失败 - {result.error}")
            continue

        raw = result.raw_result or {}
        total_ret = raw.get("rtn", 0)
        annual_ret = raw.get("annual_rtn", 0)
        sharp = raw.get("sharp", 0)
        mdd = raw.get("mdd", 0)
        benchmark_ret = raw.get("benchmark_return", None)

        print(f"\n  {name}:")
        print(f"    总收益率：{total_ret:.2%}")
        print(f"    年化收益：{annual_ret:.2%}")
        print(f"    夏普比率：{sharp:.4f}")
        print(f"    最大回撤：{mdd:.2%}")
        if benchmark_ret is not None:
            print(f"    基准收益：{benchmark_ret:.2%}")

    return results


# ============================================================
# 命令行入口
# ============================================================

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="qteasy_research — 通用策略回测入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python run_backtest.py
  python run_backtest.py --strategy momentum --lookback 126 --top-n 3
  python run_backtest.py --strategy risk_parity --cash 500000
  python run_backtest.py --compare --strategies equal_weight,momentum,risk_parity
        """,
    )

    parser.add_argument(
        "--strategy", "-s",
        default=DEFAULT_STRATEGY,
        choices=["equal_weight", "risk_parity", "inverse_vol", "momentum"],
        help=f"策略类型（默认：{DEFAULT_STRATEGY}）",
    )

    parser.add_argument(
        "--compare", "-c",
        action="store_true",
        help="对比多个策略",
    )

    parser.add_argument(
        "--strategies",
        default="equal_weight,momentum,risk_parity,inverse_vol",
        help="对比模式下的策略列表，逗号分隔",
    )

    parser.add_argument(
        "--cash",
        type=float,
        default=DEFAULT_CASH,
        help=f"初始资金（默认：{DEFAULT_CASH:,}）",
    )

    parser.add_argument(
        "--start",
        default=DEFAULT_START,
        help=f"回测开始日期（默认：{DEFAULT_START}）",
    )

    parser.add_argument(
        "--fee",
        type=float,
        default=DEFAULT_FEE_RATE,
        help=f"买卖费率（默认：{DEFAULT_FEE_RATE}）",
    )

    parser.add_argument(
        "--benchmark",
        default=DEFAULT_BENCHMARK,
        help=f"基准指数（默认：{DEFAULT_BENCHMARK}）",
    )

    parser.add_argument(
        "--freq",
        default="ME",
        choices=["ME", "W", "QE", "YE"],
        help="调仓频率：ME=月末, W=周末, QE=季末, YE=年末（默认：ME）",
    )

    parser.add_argument(
        "--extended",
        action="store_true",
        help="使用扩展资产池（包含更多 ETF）",
    )

    parser.add_argument(
        "--visual",
        action="store_true",
        help="显示回测图表",
    )

    # 策略特定参数
    parser.add_argument("--lookback", type=int, default=126,
                        help="动量策略：回溯期（交易日数，默认 126）")
    parser.add_argument("--top-n", type=int, default=3,
                        help="动量策略：选择前 N 只 ETF（默认 3）")
    parser.add_argument("--vol-window", type=int, default=60,
                        help="波动率策略：计算窗口（交易日数，默认 60）")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """主入口。"""
    args = parse_args(argv)

    # 确定资产池
    pool = EXTENDED_POOL if args.extended else DEFAULT_POOL

    # 策略参数
    strategy_params = {}
    if args.strategy == "momentum":
        strategy_params["lookback"] = args.lookback
        strategy_params["top_n"] = args.top_n
    elif args.strategy in ("risk_parity", "inverse_vol"):
        strategy_params["window_length"] = args.vol_window

    if args.compare:
        strategy_names = [
            s.strip() for s in args.strategies.split(",")
        ]
        run_comparison(
            strategies=strategy_names,
            asset_pool=pool,
            cash=args.cash,
            start=args.start,
            fee_rate=args.fee,
            benchmark=args.benchmark,
        )
    else:
        run_single_backtest(
            strategy_name=args.strategy,
            asset_pool=pool,
            cash=args.cash,
            start=args.start,
            fee_rate=args.fee,
            benchmark=args.benchmark,
            run_freq=args.freq,
            visual=args.visual,
            strategy_params=strategy_params,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
