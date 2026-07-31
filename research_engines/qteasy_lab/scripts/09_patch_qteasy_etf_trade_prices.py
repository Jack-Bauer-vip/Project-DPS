# -*- coding: utf-8 -*-

"""
修复qteasy场内ETF回测价格读取问题。

问题原因：
qteasy的check_and_prepare_trade_prices()函数默认只从：
1. E   股票行情；
2. IDX 指数行情；

读取回测交易价格。

只有代码以OF结尾的场外基金才会加入FD基金类型。
但是场内ETF代码以SH或SZ结尾，因此不会读取fund_daily。

本补丁把交易价格候选资产类型从：
E, IDX

修改为：
E, IDX, FD

这样qteasy在回测ETF时可以从fund_daily读取收盘价。
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path


def locate_qteasy_history_file() -> Path:
    """
    查找当前虚拟环境中的qteasy/history.py文件。

    Returns
    -------
    Path
        qteasy的history.py完整路径。
    """

    # 查找当前Python实际加载的qteasy模块位置
    module_spec = importlib.util.find_spec("qteasy")

    if module_spec is None:
        raise RuntimeError(
            "当前Python环境中没有找到qteasy。"
        )

    if module_spec.origin is None:
        raise RuntimeError(
            "无法确定qteasy模块的安装路径。"
        )

    # qteasy/__init__.py所在目录就是qteasy包目录
    package_directory = (
        Path(module_spec.origin)
        .resolve()
        .parent
    )

    history_file = (
        package_directory
        / "history.py"
    )

    if not history_file.exists():
        raise RuntimeError(
            f"没有找到history.py：{history_file}"
        )

    return history_file


def apply_patch(history_file: Path) -> None:
    """
    修改qteasy的交易价格资产类型配置。

    Parameters
    ----------
    history_file:
        qteasy/history.py文件路径。
    """

    # qteasy原始代码
    original_text = (
        "        asset_types = 'E, IDX'"
    )

    # 修复后的代码：
    # 同时允许从fund_daily读取场内ETF价格
    patched_text = (
        "        asset_types = 'E, IDX, FD'"
        "  # 修复：场内ETF也需要读取fund_daily"
    )

    # 按UTF-8读取qteasy源码
    source_code = history_file.read_text(
        encoding="utf-8"
    )

    # 已打补丁时不重复修改
    if patched_text in source_code:
        print("当前qteasy已经应用ETF回测补丁。")
        return

    # 找不到原始代码时停止，避免错误替换其他内容
    if original_text not in source_code:
        raise RuntimeError(
            "没有找到预期的qteasy原始代码：\n"
            f"{original_text}\n"
            "可能是qteasy版本发生了变化，"
            "请不要自动修改。"
        )

    # 修改前创建备份文件
    backup_file = history_file.with_name(
        "history.py.before_etf_patch.bak"
    )

    if not backup_file.exists():
        shutil.copy2(
            history_file,
            backup_file,
        )

        print(
            f"已备份原文件：{backup_file}"
        )
    else:
        print(
            f"备份文件已经存在：{backup_file}"
        )

    # 只替换目标代码一次
    patched_source = source_code.replace(
        original_text,
        patched_text,
        1,
    )

    # 保存修复后的源码
    history_file.write_text(
        patched_source,
        encoding="utf-8",
    )

    print("qteasy ETF回测价格补丁安装成功。")
    print(f"已修改文件：{history_file}")
    print("修改内容：")
    print(f"- 原代码：{original_text.strip()}")
    print(f"- 新代码：{patched_text.strip()}")


def main() -> None:
    """定位qteasy源码并安装ETF价格读取补丁。"""

    print("=" * 70)
    print("qteasy场内ETF回测价格补丁")
    print("=" * 70)

    history_file = locate_qteasy_history_file()

    print(f"qteasy源码位置：{history_file}")

    apply_patch(history_file)


if __name__ == "__main__":
    try:
        main()

    except Exception as exc:
        print(
            "\n补丁安装失败："
            f"{type(exc).__name__}: {exc}"
        )
        raise