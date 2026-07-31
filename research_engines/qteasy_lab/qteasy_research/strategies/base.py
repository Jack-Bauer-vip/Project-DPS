"""
策略基类 — 封装 qteasy GeneralStg 的通用逻辑。

所有自定义策略继承 BaseStrategy，实现 build() 方法返回 qt.GeneralStg 实例。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import qteasy as qt


class BaseStrategy(ABC):
    """
    策略基类。

    子类需要设置类属性并实现 build() 方法。

    Attributes
    ----------
    name : str
        策略名称。
    description : str
        策略说明。
    params : dict
        策略参数（子类可自定义）。
    """

    name: str = "BASE_STRATEGY"
    description: str = "策略基类"
    params: dict = {}

    def __init__(self, **kwargs) -> None:
        """初始化策略，允许通过 kwargs 覆盖参数。"""
        self.params.update(kwargs)

    @abstractmethod
    def build(self) -> qt.GeneralStg:
        """
        创建并返回 qteasy GeneralStg 实例。

        Returns
        -------
        qt.GeneralStg
            可在 Operator 中使用的策略实例。
        """

    def get_config(self) -> dict:
        """返回策略配置字典，用于日志和报告。"""
        return {
            "name": self.name,
            "description": self.description,
            "params": self.params,
        }
