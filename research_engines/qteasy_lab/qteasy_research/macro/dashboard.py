"""
宏观仪表盘 — 可视化因子趋势、状态和评分。

提供控制台表格和 matplotlib 图表输出。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# 非交互式后端，兼容无GUI环境
matplotlib.use("Agg")

# Windows 中文显示配置
_CN_FONTS = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
for _f in _CN_FONTS:
    try:
        matplotlib.font_manager.findfont(_f, fallback_to_default=False)
        plt.rcParams["font.sans-serif"] = [_f] + plt.rcParams["font.sans-serif"]
        break
    except Exception:
        continue
plt.rcParams["axes.unicode_minus"] = False  # 正常显示负号

from qteasy_research.macro import config
from qteasy_research.macro.factors import MacroDataFetcher
from qteasy_research.macro.states import FactorStateClassifier


class MacroDashboard:
    """
    宏观仪表盘 — 因子走势图表和状态可视化。

    Examples
    --------
    >>> dash = MacroDashboard()
    >>> dash.plot_factor_trend("PMI")
    >>> dash.plot_all_factors()
    """

    def __init__(self) -> None:
        self.fetcher = MacroDataFetcher()
        self.clf = FactorStateClassifier()

    def plot_factor_trend(
        self,
        name: str,
        save_path: str | Path | None = None,
    ) -> None:
        """
        绘制单因子历史走势 + Z-score + 状态标记。

        Parameters
        ----------
        name : str
            因子名。
        save_path : str or Path, optional
            保存路径。不指定则显示。
        """
        series = self.fetcher.load_local(name)
        if series.empty:
            print(f"因子 {name} 无数据")
            return

        factor_cfg = config.get_factor_config(name)
        label = factor_cfg["label"] if factor_cfg else name

        zscore = self.clf.rolling_zscore(series)
        states = self.clf.classify(series)

        fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)

        # 上：因子值
        axes[0].plot(series.index, series.values, color="steelblue", linewidth=1.5)
        axes[0].set_title(f"{label}（{name}）历史走势", fontsize=13)
        axes[0].set_ylabel(factor_cfg.get("unit", "") if factor_cfg else "")
        axes[0].grid(True, alpha=0.3)

        # 中：Z-score
        colors_z = np.where(
            zscore.values > self.clf.z_threshold, "red",
            np.where(zscore.values < -self.clf.z_threshold, "green", "gray")
        )
        axes[1].bar(zscore.index, zscore.values, color=colors_z, width=20, alpha=0.7)
        axes[1].axhline(self.clf.z_threshold, color="red", linestyle="--", alpha=0.5,
                        label=f"+{self.clf.z_threshold}")
        axes[1].axhline(-self.clf.z_threshold, color="green", linestyle="--", alpha=0.5,
                        label=f"-{self.clf.z_threshold}")
        axes[1].axhline(0, color="black", linewidth=0.5)
        axes[1].set_title("Z-score", fontsize=11)
        axes[1].legend(fontsize=9)
        axes[1].grid(True, alpha=0.3)

        # 下：状态
        colors_s = {"up": "red", "neutral": "gray", "down": "green"}
        bar_colors = [colors_s.get(s, "gray") for s in states.values]
        axes[2].bar(states.index, np.ones(len(states)), color=bar_colors, width=20, alpha=0.6)
        axes[2].set_title("因子状态（↑ 上行 / → 中性 / ↓ 下行）", fontsize=11)
        axes[2].set_ylim(0, 1.5)
        axes[2].set_yticks([])

        plt.tight_layout()

        if save_path:
            save_path = Path(save_path)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            plt.savefig(save_path, dpi=150, bbox_inches="tight")
            print(f"图表已保存：{save_path}")
        else:
            plt.show()

        plt.close(fig)

    def plot_all_factors(
        self,
        output_dir: str | Path | None = None,
    ) -> None:
        """绘制所有因子的趋势图。"""
        output_dir = Path(output_dir) if output_dir else config.MACRO_DATA_DIR.parent / "charts"
        output_dir.mkdir(parents=True, exist_ok=True)

        for factor in config.FACTOR_DEFINITIONS:
            name = factor["name"]
            save_path = output_dir / f"{name}_trend.png"
            try:
                self.plot_factor_trend(name, save_path=save_path)
            except Exception as e:
                print(f"  {name} 图表生成失败：{e}")

    def print_factor_table(self) -> None:
        """打印当前所有因子的状态表格。"""
        factor_data = self.fetcher.load_all()
        if not factor_data:
            print("无数据，请先运行 --init")
            return

        states = self.clf.classify_all(factor_data)

        print()
        print("=" * 70)
        print("  宏观因子状态一览")
        print("=" * 70)
        print(f"  {'因子':<16} {'最新值':<12} {'Z-score':<10} {'状态':<8}")
        print(f"  {'-'*46}")
        for name, info in states.items():
            s = info["state"]
            symbol = {"up": "↑ ", "down": "↓ ", "neutral": "→ "}.get(s, "  ")
            print(
                f"  {info['label']:<16} {info['latest_value']:<12} "
                f"{info['score']:<10} {symbol}{s}"
            )
        print("=" * 70)
        print(f"  数据截至：最近可用月份")
        print()
