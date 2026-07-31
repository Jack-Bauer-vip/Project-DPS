"""
回测报告生成器 — 将回测结果转换为可读的报告。

支持控制台输出和 CSV 文件导出。
"""

from __future__ import annotations

import csv
from pathlib import Path

from qteasy_research.backtesting.engine import BacktestResult
from qteasy_research.backtesting.metrics import PerformanceMetrics


class BacktestReporter:
    """
    回测报告生成器。

    Examples
    --------
    >>> reporter = BacktestReporter(result)
    >>> reporter.print_summary()
    >>> reporter.save_csv("results/summary.csv")
    """

    def __init__(self, result: BacktestResult) -> None:
        self.result = result

    def print_summary(self) -> None:
        """在控制台输出回测摘要。"""
        if not self.result.success:
            print(f"回测失败：{self.result.error}")
            return

        raw = self.result.raw_result
        if raw is None:
            print("回测结果为空。")
            return

        print("\n" + "=" * 60)
        print(f"策略：{self.result.strategy_name}")
        print("=" * 60)

        # qteasy 回测结果字典中的关键字段
        key_map = {
            "rtn": "总收益率",
            "annual_rtn": "年化收益率",
            "benchmark_return": "基准收益率",
            "benchmark_yearly_return": "基准年化收益率",
            "sharp": "夏普比率",
            "mdd": "最大回撤",
            "total_fee": "总手续费",
            "final_value": "最终价值",
        }

        for key, label in key_map.items():
            if key in raw:
                value = raw[key]
                if isinstance(value, float):
                    if key in ("sharp",):
                        print(f"  {label}：{value:.4f}")
                    elif key in ("total_fee", "final_value"):
                        print(f"  {label}：¥{value:,.2f}")
                    else:
                        print(f"  {label}：{value:.2%}")
                else:
                    print(f"  {label}：{value}")

        print("=" * 60)

    def print_detailed_metrics(self) -> None:
        """
        在控制台输出详细的绩效指标。

        需要回测结果中包含日收益率序列。
        """
        if not self.result.success:
            print(f"回测失败：{self.result.error}")
            return

        raw = self.result.raw_result
        if raw is None:
            print("无回测结果。")
            return

        # 从回测结果获取净值曲线和基准数据
        try:
            value_curve = raw.get("value_curve", None)
            benchmark_data = raw.get("benchmark_data", None)

            if value_curve is not None:
                daily_returns = value_curve.pct_change().dropna().values
                bench_returns = None
                if benchmark_data is not None:
                    bench_returns = benchmark_data.pct_change().dropna().values

                metrics = PerformanceMetrics.calculate(
                    daily_returns=daily_returns,
                    benchmark_returns=bench_returns,
                )

                print("\n" + "=" * 60)
                print(f"详细绩效指标：{self.result.strategy_name}")
                print("=" * 60)

                fields = [
                    ("total_return", "总收益率"),
                    ("annual_return", "年化收益率"),
                    ("annual_volatility", "年化波动率"),
                    ("sharpe_ratio", "夏普比率"),
                    ("sortino_ratio", "索提诺比率"),
                    ("calmar_ratio", "卡尔玛比率"),
                    ("max_drawdown", "最大回撤"),
                    ("max_drawdown_duration", "最大回撤持续期"),
                    ("win_rate", "胜率（正收益日占比）"),
                    ("benchmark_return", "基准总收益率"),
                    ("benchmark_annual_return", "基准年化收益率"),
                    ("alpha", "Alpha"),
                    ("beta", "Beta"),
                    ("information_ratio", "信息比率"),
                ]

                for key, label in fields:
                    value = getattr(metrics, key, None)
                    if value is None:
                        continue
                    if isinstance(value, float):
                        if any(k in key for k in ("ratio", "alpha", "beta", "rate")):
                            print(f"  {label}：{value:.4f}")
                        else:
                            print(f"  {label}：{value:.2%}")
                    else:
                        print(f"  {label}：{value}")

                print("=" * 60)
        except Exception as e:
            print(f"无法计算详细指标：{e}")

    def save_csv(self, filepath: str | Path) -> None:
        """
        将回测摘要保存为 CSV 文件。

        Parameters
        ----------
        filepath : str or Path
            输出文件路径。
        """
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        summary = {
            "strategy": self.result.strategy_name,
            "success": str(self.result.success),
        }

        if self.result.success and self.result.raw_result:
            raw = self.result.raw_result
            qteasy_keys = ["rtn", "annual_rtn", "sharp", "mdd",
                           "benchmark_return", "final_value", "total_fee"]
            for key in qteasy_keys:
                if key in raw:
                    summary[key] = raw[key]

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["指标", "数值"])
            for key, value in summary.items():
                if isinstance(value, float):
                    writer.writerow([key, f"{value:.6f}"])
                else:
                    writer.writerow([key, value])

        print(f"报告已保存：{filepath}")
