"""Desktop data management page."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qteasy_research.pretrade.data_manager import DataManager, DataSyncRequest


class DataWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: str, manager: DataManager, payload: Any = None, parent=None) -> None:
        super().__init__(parent)
        self.operation = operation
        self.manager = manager
        self.payload = payload

    def run(self) -> None:
        try:
            if self.operation == "sync":
                result = self.manager.sync_data(self.payload)
            elif self.operation == "import":
                result = self.manager.import_existing_local_data()
            elif self.operation == "build_factor":
                result = self.manager.build_factor_values(**self.payload)
            else:
                raise ValueError(f"不支持的数据任务：{self.operation}")
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class DataManagementPage(QWidget):
    status_message = Signal(str)

    def __init__(self, store_root: str | Path, *, initial_mode: str = "direct", parent=None) -> None:
        super().__init__(parent)
        self.store_root = Path(store_root)
        self.mode = initial_mode if initial_mode in {"direct", "middleware"} else "direct"
        self.worker: DataWorker | None = None
        self._build_ui()
        self.refresh_catalog()

    def set_store_root(self, store_root: str | Path) -> None:
        self.store_root = Path(store_root)
        self.refresh_catalog()

    def prepare_from_factor_diagnostic(
        self,
        codes: list[str],
        asset_type: str,
        factor_id: str | None = None,
    ) -> None:
        """Prefill data-management inputs without starting a network task."""

        self.codes_input.setPlainText("\n".join(str(code) for code in codes if str(code).strip()))
        asset_index = self.asset_type_input.findData(str(asset_type).upper())
        if asset_index >= 0:
            self.asset_type_input.setCurrentIndex(asset_index)
        dataset_index = self.dataset_input.findData("market")
        if dataset_index >= 0:
            self.dataset_input.setCurrentIndex(dataset_index)
        if factor_id:
            self.status_label.setText(
                f"已带入 {len(codes)} 个资产；下一步请更新行情，并生成 {factor_id} 因子数据。"
            )
        else:
            self.status_label.setText("已带入评分资产；下一步请更新行情并生成因子数据。")

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        title_row = QHBoxLayout()
        title = QLabel("数据管理")
        title.setObjectName("dataTitle")
        title_row.addWidget(title)
        title_row.addWidget(QLabel("先更新本地数据，再生成因子和评分；研究页面只读取本地结果"))
        title_row.addStretch(1)
        layout.addLayout(title_row)

        connection = QGroupBox("数据连接与更新")
        form = QFormLayout(connection)
        self.mode_input = QComboBox()
        self.mode_input.addItem("API 直连（默认：AKShare → Tushare）", "direct")
        self.mode_input.addItem("数据中台", "middleware")
        self.mode_input.setCurrentIndex(self.mode_input.findData(self.mode))
        self.mode_input.currentIndexChanged.connect(self._mode_changed)
        self.dataset_input = QComboBox()
        self.dataset_input.addItem("日线行情", "market")
        self.dataset_input.addItem("基础资料", "metadata")
        self.asset_type_input = QComboBox()
        for label, value in (("ETF", "ETF"), ("股票", "STOCK"), ("指数", "INDEX")):
            self.asset_type_input.addItem(label, value)
        self.horizon_input = QComboBox()
        for label, value in (("短线", "short"), ("中线", "medium"), ("长线", "long")):
            self.horizon_input.addItem(label, value)
        self.codes_input = QPlainTextEdit()
        self.codes_input.setFixedHeight(58)
        self.codes_input.setPlaceholderText("输入代码，每行或逗号分隔；例如：518880.SH\n510300.SH")
        self.start_input = QLineEdit()
        self.start_input.setPlaceholderText("可选，例如 2018-01-01")
        self.end_input = QLineEdit(date.today().isoformat())
        self.policy_input = QComboBox()
        self.policy_input.addItem("增量更新", "incremental")
        self.policy_input.addItem("完整刷新", "refresh")
        form.addRow("连接方式", self.mode_input)
        form.addRow("数据集", self.dataset_input)
        form.addRow("资产类型", self.asset_type_input)
        form.addRow("投资期限", self.horizon_input)
        form.addRow("代码范围", self.codes_input)
        form.addRow("开始日期", self.start_input)
        form.addRow("结束日期", self.end_input)
        form.addRow("更新策略", self.policy_input)
        actions = QHBoxLayout()
        self.check_button = QPushButton("检查连接")
        self.check_button.clicked.connect(self.check_connection)
        self.import_button = QPushButton("导入现有 CSV")
        self.import_button.clicked.connect(self.import_csv)
        self.update_button = QPushButton("更新数据到 SQLite")
        self.update_button.clicked.connect(self.update_data)
        self.build_button = QPushButton("生成行情因子")
        self.build_button.clicked.connect(self.build_factors)
        for button in (self.check_button, self.import_button, self.update_button, self.build_button):
            actions.addWidget(button)
        form.addRow("操作", actions)
        layout.addWidget(connection)

        status_row = QHBoxLayout()
        self.status_label = QLabel("等待操作")
        self.status_label.setWordWrap(True)
        self.catalog_label = QLabel("暂无数据目录")
        status_row.addWidget(self.status_label, 2)
        status_row.addWidget(self.catalog_label, 1)
        layout.addLayout(status_row)

        query_box = QGroupBox("本地数据查询")
        query_layout = QVBoxLayout(query_box)
        query_actions = QHBoxLayout()
        self.query_code_input = QLineEdit()
        self.query_code_input.setPlaceholderText("可选，留空查看全部")
        self.query_button = QPushButton("查询行情")
        self.query_button.clicked.connect(self.query_market)
        query_actions.addWidget(QLabel("代码"))
        query_actions.addWidget(self.query_code_input, 1)
        query_actions.addWidget(self.query_button)
        query_layout.addLayout(query_actions)
        self.data_table = QTableWidget()
        self.data_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.data_table.setAlternatingRowColors(True)
        query_layout.addWidget(self.data_table, 1)
        layout.addWidget(query_box, 1)

    def _manager(self) -> DataManager:
        return DataManager(self.store_root)

    def _mode_changed(self) -> None:
        self.mode = str(self.mode_input.currentData())
        label = "API 直连" if self.mode == "direct" else "数据中台"
        self.status_label.setText(f"当前连接：{label}")

    def _codes(self) -> list[str]:
        return [item for item in re.split(r"[\s,，;；]+", self.codes_input.toPlainText().strip()) if item]

    def _run_worker(self, operation: str, payload: Any = None) -> None:
        if self.worker and self.worker.isRunning():
            self.status_label.setText("已有数据任务正在运行，请稍候。")
            return
        self._set_busy(True)
        self.worker = DataWorker(operation, self._manager(), payload, self)
        self.worker.completed.connect(self._task_completed)
        self.worker.failed.connect(self._task_failed)
        self.worker.finished.connect(lambda: self._set_busy(False))
        self.worker.start()

    def _set_busy(self, busy: bool) -> None:
        for button in (self.check_button, self.import_button, self.update_button, self.build_button, self.query_button):
            button.setEnabled(not busy)

    def check_connection(self) -> None:
        statuses = self._manager().check_data_source(self.mode)
        self.status_label.setText("；".join(f"{item.source}: {item.message}" for item in statuses))

    def import_csv(self) -> None:
        self.status_label.setText("正在导入本地 CSV 到 SQLite…")
        self._run_worker("import")

    def update_data(self) -> None:
        codes = self._codes()
        if not codes:
            self.status_label.setText("请先输入至少一个代码。系统不会默认下载全部 ETF。")
            return
        request = DataSyncRequest(
            datasets=[str(self.dataset_input.currentData())],
            codes=codes,
            start=self.start_input.text().strip() or None,
            end=self.end_input.text().strip() or None,
            mode=self.mode,
            update_policy=str(self.policy_input.currentData()),
        )
        self.status_label.setText("正在连接数据源并写入 SQLite…")
        self._run_worker("sync", request)

    def build_factors(self) -> None:
        codes = self._codes() or None
        payload = {
            "factor_ids": [
                "momentum_60d",
                "momentum_120d",
                "low_volatility_20d",
                "liquidity_turnover",
            ],
            "asset_type": str(self.asset_type_input.currentData()),
            "horizon": str(self.horizon_input.currentData()),
            "codes": codes,
            "as_of_date": self.end_input.text().strip() or None,
        }
        self.status_label.setText("正在从 SQLite 行情生成因子 Parquet…")
        self._run_worker("build_factor", payload)

    def _task_completed(self, result: Any) -> None:
        warnings = getattr(result, "warnings", []) or []
        if hasattr(result, "factors_built"):
            message = f"数据任务完成：新增 {result.rows_added} 行，更新 {result.rows_updated} 行"
        elif hasattr(result, "row_count"):
            message = f"{result.factor_id} 因子生成：{result.row_count} 行"
        else:
            message = f"数据任务完成：{getattr(result, 'status', 'COMPLETED')}"
        if warnings:
            message += "；" + "；".join(warnings[:3])
        self.status_label.setText(message)
        self.status_message.emit(message)
        self.refresh_catalog()

    def _task_failed(self, message: str) -> None:
        self.status_label.setText(f"数据任务失败：{message}")
        self.status_message.emit(f"数据任务失败：{message}")

    def refresh_catalog(self) -> None:
        try:
            catalog = self._manager().store.list_data_catalog()
            self.catalog_label.setText(f"本地目录：{len(catalog)} 项数据记录")
        except Exception as exc:
            self.catalog_label.setText(f"目录读取失败：{type(exc).__name__}")

    def query_market(self) -> None:
        code = self.query_code_input.text().strip() or None
        result = self._manager().query_data("market", code=code, limit=500)
        frame = result.rows
        self.data_table.setRowCount(len(frame))
        self.data_table.setColumnCount(len(frame.columns))
        self.data_table.setHorizontalHeaderLabels([str(column) for column in frame.columns])
        for row_index, row in frame.iterrows():
            for column_index, column in enumerate(frame.columns):
                value = row[column]
                self.data_table.setItem(row_index, column_index, QTableWidgetItem("" if pd_is_na(value) else str(value)))
        self.status_label.setText(f"查询完成：{len(frame)} 行")


def pd_is_na(value: Any) -> bool:
    try:
        import pandas as pd

        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


__all__ = ["DataManagementPage", "DataWorker"]
