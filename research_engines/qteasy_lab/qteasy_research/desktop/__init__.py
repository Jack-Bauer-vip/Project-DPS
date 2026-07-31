"""ETF/股票投前研究桌面工作台。"""

from __future__ import annotations


def launch_desktop() -> int:
    from qteasy_research.desktop.app import launch

    return launch()


__all__ = ["launch_desktop"]
