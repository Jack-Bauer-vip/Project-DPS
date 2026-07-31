"""配置 qteasy 的本地数据源、日志目录和 Tushare Token。"""

from __future__ import annotations

import os
from pathlib import Path

import qteasy as qt


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]

    data_dir = project_root / "data"
    system_log_dir = project_root / "logs" / "system"
    trade_log_dir = project_root / "logs" / "trades"

    for directory in (data_dir, system_log_dir, trade_log_dir):
        directory.mkdir(parents=True, exist_ok=True)

    tushare_token = os.environ.get("TUSHARE_TOKEN", "").strip()

    if not tushare_token:
        raise SystemExit(
            "没有读取到 TUSHARE_TOKEN。\n"
            "请先在当前 PowerShell 中设置环境变量。"
        )

    qt.update_start_up_setting(
        tushare_token=tushare_token,
        local_data_source="file",
        local_data_file_type="csv",
        local_data_file_path=str(data_dir),
        sys_log_file_path=str(system_log_dir),
        trade_log_file_path=str(trade_log_dir),
    )

    print("qteasy 启动配置已保存。")
    print(f"数据目录：{data_dir}")
    print(f"系统日志：{system_log_dir}")
    print(f"交易日志：{trade_log_dir}")
    print("Tushare Token：已配置，未显示具体内容。")


if __name__ == "__main__":
    main()