"""PySide6 桌面端全球 ETF 宏观研究页面。

展示数据状态、当前宏观状态、DGS30/DGS10 利率代理、SPY/TLT/GLD 基础评分与宏观修正、
支持/冲突因子、条件收益表和研究规则状态；支持重新计算与查看 Notebook 产出。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from qteasy_research.core.global_etf_engine import (
    GlobalEtfEngine,
    initialize_default_global_etf_profiles,
)
from qteasy_research.pretrade.storage import ResearchStore

# 评分表格列中文翻译
SCORE_COLUMN_LABELS = {
    "asset": "资产",
    "research_asset": "研究资产",
    "data_as_of": "数据截至",
    "data_quality": "数据质量",
    "macro_state": "宏观状态",
    "rate_proxy": "利率代理",
    "frequency_used": "频率",
    "base_score": "基础评分",
    "macro_modifier": "宏观修正",
    "final_score": "最终评分",
    "macro_support_factors": "支持因子",
    "macro_conflict_factors": "冲突因子",
    "sample_count": "样本数",
    "status": "状态",
    "warnings": "警告",
}

# 条件收益表列中文翻译
CONDITION_COLUMN_LABELS = {
    "asset": "资产",
    "macro_state": "宏观状态",
    "sample_count": "样本数",
    "monthly_return": "月均收益",
    "monthly_volatility": "月均波动",
    "max_drawdown": "最大回撤",
    "win_rate": "胜率",
}

# 规则状态表列中文翻译
RULE_COLUMN_LABELS = {
    "asset_code": "资产",
    "macro_state": "状态",
    "modifier": "修正",
    "sample_count": "样本",
    "confidence": "置信度",
    "status": "规则状态",
    "sample_start": "样本起",
    "sample_end": "样本止",
    "effective_date": "生效日",
    "approved_at": "批准时间",
}

GLOBAL_ETF_ASSETS = ("SPY", "TLT", "GLD")


class GlobalEtfScoreWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        *,
        target_date: str,
        assets: list[str],
        horizon: str,
        data_root: str | Path,
        store_root: str | Path,
        persist: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.target_date = target_date
        self.assets = assets
        self.horizon = horizon
        self.data_root = data_root
        self.store_root = store_root
        self.persist = persist

    def run(self) -> None:
        try:
            engine = GlobalEtfEngine(self.data_root, self.store_root)
            result = engine.calculate_scores(
                self.target_date,
                assets=self.assets,
                horizon=self.horizon,
                persist=self.persist,
            )
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class GlobalEtfPage(QWidget):
    status_message = Signal(str)

    def __init__(self, store_root: str | Path, data_root: str | Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.store_root = Path(store_root)
        self.data_root = Path(data_root) if data_root else self.store_root.parent / "data"
        self.score_worker: GlobalEtfScoreWorker | None = None
        self._build_ui()
        self.refresh()

    def set_store_root(self, store_root: str | Path) -> None:
        self.store_root = Path(store_root)
        if self.store_root.parent / "data" != self.data_root:
            self.data_root = self.store_root.parent / "data"
        self.refresh()

    def _store(self) -> ResearchStore:
        return ResearchStore(self.store_root)

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 18, 18, 18)

        # 标题行
        title_row = QHBoxLayout()
        title = QLabel("全球 ETF 宏观")
        title.setObjectName("factorTitle")
        title_row.addWidget(title)
        title_row.addWidget(QLabel("SPY/TLT/GLD 宏观匹配研究、规则状态与条件收益"))
        title_row.addStretch(1)
        self.initialize_button = QPushButton("初始化默认配置")
        self.initialize_button.clicked.connect(self.initialize_defaults)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh)
        self.recalculate_button = QPushButton("重新计算")
        self.recalculate_button.clicked.connect(self.run_recalculate)
        title_row.addWidget(self.initialize_button)
        title_row.addWidget(self.refresh_button)
        title_row.addWidget(self.recalculate_button)
        root_layout.addLayout(title_row)

        # 参数行
        param_row = QHBoxLayout()
        param_row.addWidget(QLabel("目标日期"))
        self.target_date_edit = QLineEdit("2026-08-03")
        self.target_date_edit.setFixedWidth(110)
        param_row.addWidget(self.target_date_edit)
        param_row.addWidget(QLabel("周期"))
        self.horizon_combo = QComboBox()
        self.horizon_combo.addItems(["medium", "short", "long"])
        param_row.addWidget(self.horizon_combo)
        self.persist_check = QCheckBox("写入评分快照")
        param_row.addWidget(self.persist_check)
        param_row.addStretch(1)
        self.page_status = QLabel("")
        self.page_status.setWordWrap(True)
        param_row.addWidget(self.page_status, 1)
        root_layout.addLayout(param_row)

        # 状态与宏观区 + 规则区
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_status_macro_section())
        splitter.addWidget(self._build_rules_section())
        splitter.setSizes([560, 620])
        root_layout.addWidget(splitter, 1)

        # 评分表格
        root_layout.addWidget(self._build_score_section(), 3)

        # 条件收益表
        root_layout.addWidget(self._build_condition_section(), 3)

    # ---- 分区构建 ----

    def _build_status_macro_section(self) -> QWidget:
        box = QGroupBox("数据状态与当前宏观状态")
        layout = QVBoxLayout(box)
        self.macro_browser = QTextBrowser()
        layout.addWidget(self.macro_browser)
        return box

    def _build_rules_section(self) -> QWidget:
        box = QGroupBox("宏观规则状态")
        layout = QVBoxLayout(box)
        self.rules_table = QTableWidget()
        self._configure_table(self.rules_table)
        layout.addWidget(self.rules_table)
        return box

    def _build_score_section(self) -> QWidget:
        box = QGroupBox("SPY/TLT/GLD 宏观匹配评分")
        layout = QVBoxLayout(box)
        self.score_table = QTableWidget()
        self._configure_table(self.score_table)
        layout.addWidget(self.score_table)
        return box

    def _build_condition_section(self) -> QWidget:
        box = QGroupBox("条件收益表（来自 Notebook 产出）")
        layout = QVBoxLayout(box)
        self.condition_table = QTableWidget()
        self._configure_table(self.condition_table)
        layout.addWidget(self.condition_table)
        return box

    @staticmethod
    def _configure_table(table: QTableWidget) -> None:
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

    @staticmethod
    def _fill_table(table: QTableWidget, rows: list[dict[str, Any]], labels: dict[str, str], widths: dict[str, int] | None = None) -> None:
        if not rows:
            table.clear()
            table.setRowCount(0)
            table.setColumnCount(0)
            return
        columns = list(labels.keys())
        table.setColumnCount(len(columns))
        table.setRowCount(len(rows))
        table.setHorizontalHeaderLabels([labels[column] for column in columns])
        for row_index, row in enumerate(rows):
            for column_index, column in enumerate(columns):
                value = row.get(column, "")
                if isinstance(value, list):
                    value = "、".join(str(v) for v in value)
                if isinstance(value, float):
                    value = f"{value:.4f}" if value == value else "不可用"
                if value is None:
                    value = ""
                table.setItem(row_index, column_index, QTableWidgetItem(str(value)))
        if widths:
            for column, width in widths.items():
                if column in columns:
                    table.setColumnWidth(columns.index(column), width)

    # ---- 数据加载与展示 ----

    def refresh(self) -> None:
        """刷新数据状态、宏观状态、条件收益与规则状态（不触发引擎重算）。"""
        self._refresh_status_macro()
        self._refresh_condition_table()
        self._refresh_rules_table()
        self.status_message.emit("全球 ETF 页面已刷新")

    def _refresh_status_macro(self) -> None:
        lines: list[str] = []
        data_dir = self.data_root / "processed" / "global_macro"
        lines.append(f"数据目录：{data_dir.resolve()}")
        lines.append(f"研究库：{self.store_root.resolve()}")
        lines.append("")
        # 数据序列截至日期与质量
        for series in ("DGS10", "DGS2", "DGS30", "DFII10", "SPY", "TLT", "GLD"):
            path = data_dir / f"{series}.csv"
            if path.exists():
                try:
                    frame = pd.read_csv(path)
                    as_of = str(frame["observation_date"].max())
                    quality = str(frame["quality_level"].iloc[-1]) if "quality_level" in frame.columns else "?"
                    lines.append(f"{series}：截至 {as_of}，质量 {quality}")
                except Exception as exc:
                    lines.append(f"{series}：读取失败 {exc}")
            else:
                lines.append(f"{series}：缺失")
        lines.append("")
        # 当前宏观状态（只读引擎，不重算）
        try:
            engine = GlobalEtfEngine(self.data_root, self.store_root)
            result = engine.calculate_scores(
                self.target_date_edit.text(), assets=list(GLOBAL_ETF_ASSETS), persist=False
            )
            lines.append(f"利率代理：{result.rate_proxy or '无'}")
            lines.append(f"引擎状态：{result.status}")
            if result.warnings:
                lines.append("引擎警告：")
                lines.extend(f"  · {w}" for w in result.warnings)
            lines.append("")
            lines.append("各资产宏观状态：")
            for row in result.scores:
                states = "、".join(row.get("macro_state") or [])
                lines.append(
                    f"  · {row['asset']}：宏观状态 [{states}] ｜ 评分状态 {row.get('status')} "
                    f"｜ base={self._fmt(row.get('base_score'))} 修正={self._fmt(row.get('macro_modifier'))}"
                )
        except Exception as exc:
            lines.append(f"引擎读取失败：{exc}")
        self.macro_browser.setPlainText("\n".join(lines))

    @staticmethod
    def _fmt(value: Any) -> str:
        if value is None or (isinstance(value, float) and value != value):
            return "—"
        if isinstance(value, float):
            return f"{value:.4f}"
        return str(value)

    def _refresh_condition_table(self) -> None:
        path = self.data_root / "processed" / "global_macro" / "condition_returns.csv"
        if not path.exists():
            self._fill_table(self.condition_table, [], CONDITION_COLUMN_LABELS)
            return
        try:
            frame = pd.read_csv(path)
            rows = frame.to_dict(orient="records")
        except Exception:
            rows = []
        self._fill_table(self.condition_table, rows, CONDITION_COLUMN_LABELS)

    def _refresh_rules_table(self) -> None:
        try:
            rules = self._store().list_global_etf_macro_rules()
        except Exception:
            rules = []
        self._fill_table(self.rules_table, rules, RULE_COLUMN_LABELS)

    # ---- 重算与动作 ----

    def run_recalculate(self) -> None:
        target_date = self.target_date_edit.text().strip()
        if not target_date:
            self.page_status.setText("请填写目标日期")
            return
        if self.score_worker is not None and self.score_worker.isRunning():
            return
        self.recalculate_button.setEnabled(False)
        self.page_status.setText("正在计算…")
        self.score_worker = GlobalEtfScoreWorker(
            target_date=target_date,
            assets=list(GLOBAL_ETF_ASSETS),
            horizon=self.horizon_combo.currentText(),
            data_root=self.data_root,
            store_root=self.store_root,
            persist=self.persist_check.isChecked(),
            parent=self,
        )
        self.score_worker.completed.connect(self._on_score_completed)
        self.score_worker.failed.connect(self._on_score_failed)
        self.score_worker.finished.connect(self._on_score_finished)
        self.score_worker.start()

    def _on_score_completed(self, result: Any) -> None:
        rows = list(result.scores or [])
        self._fill_table(self.score_table, rows, SCORE_COLUMN_LABELS, widths={
            "asset": 60, "data_as_of": 110, "base_score": 100, "macro_modifier": 100, "final_score": 100, "warnings": 320,
        })
        warnings = "；".join(result.warnings) if result.warnings else "无警告"
        self.page_status.setText(f"完成：{len(rows)} 个资产；利率代理 {result.rate_proxy}；{warnings}")
        if result.output_csv:
            self.status_message.emit(f"评分已输出：{result.output_csv}")

    def _on_score_failed(self, message: str) -> None:
        self.page_status.setText(f"计算失败：{message}")

    def _on_score_finished(self) -> None:
        self.recalculate_button.setEnabled(True)

    def initialize_defaults(self) -> None:
        """注册 SPY/TLT/GLD 默认研究配置，保留既有 enabled 状态，不覆盖。"""
        try:
            store = self._store()
            existing = {item["asset_code"]: item for item in store.list_global_etf_activations()}
            initialize_default_global_etf_profiles(self.store_root, enabled=False)
            # 恢复既有 enabled 状态，避免初始化覆盖已启用配置
            for asset in GLOBAL_ETF_ASSETS:
                previous = existing.get(asset)
                if previous and int(previous.get("enabled", 0)):
                    store.upsert_global_etf_activation({
                        "asset_code": asset,
                        "horizon": previous.get("horizon", "medium"),
                        "enabled": 1,
                        "status": "ENABLED",
                        "frequency": previous.get("frequency", "daily" if asset == "TLT" else "monthly"),
                    })
            self.refresh()
            self.page_status.setText("默认研究配置已注册（enabled 状态已保留）")
        except Exception as exc:
            self.page_status.setText(f"初始化失败：{exc}")
