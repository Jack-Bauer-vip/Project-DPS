"""Standalone PySide6 page for factor definitions, activation and score preview."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from qteasy_research.pretrade.factor_scoring import calculate_factor_scores
from qteasy_research.pretrade.factor_ui import (
    get_factor_data_status,
    initialize_default_factor_definitions,
)
from qteasy_research.pretrade.storage import ResearchStore


SCORE_COLUMN_LABELS = {
    "asset_code": "资产代码",
    "composite_score": "综合评分",
    "factor_score": "因子评分",
    "as_of_date": "数据截至日期",
    "availability_status": "数据状态",
    "availability_reason": "不可用原因",
}


class FactorScoreWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        *,
        target_date: str,
        asset_type: str,
        horizon: str,
        universe: list[str],
        store_root: str | Path,
        macro_regime: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.target_date = target_date
        self.asset_type = asset_type
        self.horizon = horizon
        self.universe = universe
        self.store_root = store_root
        self.macro_regime = macro_regime

    def run(self) -> None:
        try:
            result = calculate_factor_scores(
                self.target_date,
                self.asset_type,
                self.horizon,
                universe=self.universe,
                store_root=self.store_root,
                macro_regime=self.macro_regime,
            )
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class FactorResearchPage(QWidget):
    status_message = Signal(str)
    diagnostic_action = Signal(str, object)

    def __init__(self, store_root: str | Path, parent=None) -> None:
        super().__init__(parent)
        self.store_root = Path(store_root)
        self.factor_definitions: list[dict[str, Any]] = []
        self.current_factor_id: str | None = None
        self.score_worker: FactorScoreWorker | None = None
        self._build_ui()
        self.refresh_factors()

    def set_store_root(self, store_root: str | Path) -> None:
        self.store_root = Path(store_root)
        self.refresh_factors()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 18, 18, 18)
        title_row = QHBoxLayout()
        title = QLabel("因子研究")
        title.setObjectName("factorTitle")
        title_row.addWidget(title)
        title_row.addWidget(QLabel("独立研究因子、管理启用范围，并预览当前资产评分"))
        title_row.addStretch(1)
        self.initialize_button = QPushButton("初始化默认因子库")
        self.initialize_button.clicked.connect(self.initialize_defaults)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh_factors)
        title_row.addWidget(self.initialize_button)
        title_row.addWidget(self.refresh_button)
        root_layout.addLayout(title_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_factor_list())
        splitter.addWidget(self._build_factor_details())
        splitter.addWidget(self._build_factor_controls())
        splitter.setSizes([270, 500, 360])
        root_layout.addWidget(splitter, 1)
        root_layout.addWidget(self._build_score_preview())

    def _build_factor_list(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        filters = QFormLayout()
        self.category_filter = QComboBox()
        self.category_filter.addItem("全部类别", "")
        for text, value in (
            ("动量", "momentum"),
            ("价值", "value"),
            ("质量", "quality"),
            ("低波动", "low_volatility"),
            ("流动性", "liquidity"),
            ("宏观", "macro"),
            ("技术辅助", "technical"),
        ):
            self.category_filter.addItem(text, value)
        self.category_filter.currentIndexChanged.connect(self.refresh_factors)
        self.asset_filter = QComboBox()
        self.asset_filter.addItem("全部资产", "")
        for text, value in (("股票", "STOCK"), ("ETF", "ETF"), ("LOF", "LOF"), ("QDII", "QDII")):
            self.asset_filter.addItem(text, value)
        self.asset_filter.currentIndexChanged.connect(self.refresh_factors)
        self.horizon_filter = QComboBox()
        self.horizon_filter.addItem("全部期限", "")
        for text, value in (("短线", "short"), ("中线", "medium"), ("长线", "long")):
            self.horizon_filter.addItem(text, value)
        self.horizon_filter.currentIndexChanged.connect(self.refresh_factors)
        self.status_filter = QComboBox()
        self.status_filter.addItem("全部状态", "")
        for text, value in (("已启用", "enabled"), ("未启用", "disabled"), ("已暂停", "suspended"), ("数据缺失", "missing")):
            self.status_filter.addItem(text, value)
        self.status_filter.currentIndexChanged.connect(self.refresh_factors)
        filters.addRow("类别", self.category_filter)
        filters.addRow("资产", self.asset_filter)
        filters.addRow("期限", self.horizon_filter)
        filters.addRow("状态", self.status_filter)
        layout.addLayout(filters)
        self.factor_list = QListWidget()
        self.factor_list.currentRowChanged.connect(self.select_factor)
        layout.addWidget(self.factor_list, 1)
        self.factor_empty_label = QLabel("暂无因子定义，请初始化默认因子库。")
        self.factor_empty_label.setWordWrap(True)
        layout.addWidget(self.factor_empty_label)
        return panel

    def _build_factor_details(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.factor_detail_title = QLabel("请选择一个因子")
        self.factor_detail_title.setObjectName("factorDetailTitle")
        layout.addWidget(self.factor_detail_title)
        self.detail_tabs = QTabWidget()
        self.definition_view = QTextBrowser()
        self.research_view = QTextBrowser()
        self.detail_tabs.addTab(self.definition_view, "因子定义")
        self.detail_tabs.addTab(self.research_view, "离线研究结论")
        layout.addWidget(self.detail_tabs, 1)
        return panel

    def _build_factor_controls(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        box = QGroupBox("启用配置")
        form = QFormLayout(box)
        self.profile_asset_type = QComboBox()
        for text, value in (("股票", "STOCK"), ("ETF", "ETF"), ("LOF", "LOF"), ("QDII", "QDII")):
            self.profile_asset_type.addItem(text, value)
        self.profile_horizon = QComboBox()
        for text, value in (("短线", "short"), ("中线", "medium"), ("长线", "long")):
            self.profile_horizon.addItem(text, value)
        self.profile_asset_type.currentIndexChanged.connect(self.load_profile)
        self.profile_horizon.currentIndexChanged.connect(self.load_profile)
        self.profile_enabled = QCheckBox("启用这个因子配置")
        self.profile_weight = QDoubleSpinBox()
        self.profile_weight.setRange(-10.0, 10.0)
        self.profile_weight.setDecimals(3)
        self.profile_weight.setSingleStep(0.1)
        self.profile_weight.setValue(1.0)
        self.profile_drawdown = QDoubleSpinBox()
        self.profile_drawdown.setRange(-1.0, 0.0)
        self.profile_drawdown.setDecimals(3)
        self.profile_drawdown.setSingleStep(0.01)
        self.profile_drawdown.setValue(-0.15)
        self.profile_suspend = QCheckBox("回撤触发自动暂停")
        self.profile_suspend.setChecked(True)
        self.profile_status = QLabel("未选择配置")
        self.profile_status.setWordWrap(True)
        form.addRow("资产类型", self.profile_asset_type)
        form.addRow("投资期限", self.profile_horizon)
        form.addRow("启用", self.profile_enabled)
        form.addRow("因子权重", self.profile_weight)
        form.addRow("最大回撤", self.profile_drawdown)
        form.addRow("风控", self.profile_suspend)
        form.addRow("当前状态", self.profile_status)
        layout.addWidget(box)
        self.save_profile_button = QPushButton("保存应用配置")
        self.save_profile_button.clicked.connect(self.save_profile)
        self.restore_profile_button = QPushButton("人工恢复暂停配置")
        self.restore_profile_button.clicked.connect(self.restore_profile)
        self.restore_profile_button.setEnabled(False)
        layout.addWidget(self.save_profile_button)
        layout.addWidget(self.restore_profile_button)
        layout.addStretch(1)
        return panel

    def _build_score_preview(self) -> QWidget:
        box = QGroupBox("当前评分预览")
        layout = QVBoxLayout(box)
        form = QFormLayout()
        self.preview_codes = QPlainTextEdit()
        self.preview_codes.setFixedHeight(55)
        self.preview_codes.setPlaceholderText("每行或用逗号输入代码，例如：000001.SZ\n000002.SZ")
        self.preview_asset_type = QComboBox()
        for text, value in (("股票", "STOCK"), ("ETF", "ETF"), ("LOF", "LOF"), ("QDII", "QDII")):
            self.preview_asset_type.addItem(text, value)
        self.preview_horizon = QComboBox()
        for text, value in (("短线", "short"), ("中线", "medium"), ("长线", "long")):
            self.preview_horizon.addItem(text, value)
        self.preview_date = QLineEdit(date.today().isoformat())
        self.preview_regime = QLineEdit()
        self.preview_regime.setPlaceholderText("可选，例如 default")
        form.addRow("资产代码", self.preview_codes)
        form.addRow("资产类型", self.preview_asset_type)
        form.addRow("投资期限", self.preview_horizon)
        form.addRow("目标日期", self.preview_date)
        form.addRow("宏观状态", self.preview_regime)
        layout.addLayout(form)
        action_row = QHBoxLayout()
        self.score_button = QPushButton("计算当前因子评分")
        self.score_button.clicked.connect(self.run_score_preview)
        action_row.addWidget(self.score_button)
        self.score_status = QLabel("输入资产代码后计算。")
        action_row.addWidget(self.score_status, 1)
        layout.addLayout(action_row)

        self.diagnostic_box = QGroupBox("评分诊断")
        diagnostic_layout = QVBoxLayout(self.diagnostic_box)
        self.diagnostic_summary = QLabel("评分完成后，这里会显示不可用原因和解决建议。")
        self.diagnostic_summary.setWordWrap(True)
        diagnostic_layout.addWidget(self.diagnostic_summary)
        self.diagnostic_view = QTextBrowser()
        self.diagnostic_view.setMinimumHeight(105)
        self.diagnostic_view.setMaximumHeight(180)
        diagnostic_layout.addWidget(self.diagnostic_view)
        diagnostic_actions = QHBoxLayout()
        self.open_data_button = QPushButton("去数据管理")
        self.prepare_factor_button = QPushButton("重新生成因子")
        self.switch_asset_button = QPushButton("切换资产类型")
        self.recalculate_button = QPushButton("重新计算")
        self.open_factor_config_button = QPushButton("查看因子配置")
        self.open_data_button.clicked.connect(lambda: self._emit_diagnostic_action("open_data"))
        self.prepare_factor_button.clicked.connect(lambda: self._emit_diagnostic_action("prepare_factor"))
        self.switch_asset_button.clicked.connect(self._switch_asset_type_from_diagnostic)
        self.recalculate_button.clicked.connect(self.run_score_preview)
        self.open_factor_config_button.clicked.connect(self._focus_factor_from_diagnostic)
        for button in (
            self.open_data_button,
            self.prepare_factor_button,
            self.switch_asset_button,
            self.recalculate_button,
            self.open_factor_config_button,
        ):
            diagnostic_actions.addWidget(button)
        diagnostic_layout.addLayout(diagnostic_actions)
        layout.addWidget(self.diagnostic_box)
        self.score_table = QTableWidget()
        self.score_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.score_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.score_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.score_table.setMinimumHeight(150)
        layout.addWidget(self.score_table)
        return box

    def _store(self) -> ResearchStore:
        return ResearchStore(self.store_root)

    @staticmethod
    def _category_matches(category: str, selected: str) -> bool:
        if not selected:
            return True
        aliases = {
            "动量": {"momentum", "trend"},
            "价值": {"value"},
            "质量": {"quality", "growth"},
            "低波动": {"low_volatility", "risk"},
            "宏观": {"macro"},
            "技术辅助": {"technical"},
        }
        return category in aliases.get(selected, {selected})

    @staticmethod
    def _profile_status(factor_id: str, profiles: list[dict[str, Any]], data_status: dict[str, Any]) -> str:
        factor_profiles = [item for item in profiles if item["factor_id"] == factor_id]
        if any(item["status"] == "SUSPENDED" for item in factor_profiles):
            return "suspended"
        data_missing = data_status["parquet_status"] != "available" or data_status["manifest_status"] != "valid"
        if any(int(item["enabled"]) and item["status"] == "ENABLED" for item in factor_profiles):
            return "missing" if data_missing else "enabled"
        if data_missing:
            return "missing"
        return "disabled"

    @staticmethod
    def _status_label(status: str) -> str:
        return {"enabled": "已启用", "disabled": "未启用", "suspended": "已暂停", "missing": "数据缺失（可能已启用）"}.get(status, status)

    def refresh_factors(self) -> None:
        try:
            store = self._store()
            self.factor_definitions = store.list_factor_definitions()
            profiles = store.list_factor_activations()
        except Exception as exc:
            self.factor_definitions = []
            self.status_message.emit(f"因子库加载失败：{type(exc).__name__}: {exc}")
            return
        previous = self.current_factor_id
        self.factor_list.blockSignals(True)
        self.factor_list.clear()
        selected_row = -1
        category = self.category_filter.currentData()
        asset_type = self.asset_filter.currentData()
        horizon = self.horizon_filter.currentData()
        status_filter = self.status_filter.currentData()
        for definition in self.factor_definitions:
            if not self._category_matches(str(definition.get("category", "")), category):
                continue
            supported = {str(item).upper() for item in definition.get("supported_asset_types", [])}
            if asset_type and asset_type not in supported:
                continue
            allowed_horizons = definition.get("horizons") or ["short", "medium", "long"]
            if horizon and horizon not in allowed_horizons:
                continue
            data_status = get_factor_data_status(self.store_root, definition["factor_id"])
            status = self._profile_status(definition["factor_id"], profiles, data_status)
            if status_filter and status_filter != status:
                continue
            text = f"{definition.get('name', definition['factor_id'])}\n{definition['factor_id']}  ·  {self._status_label(status)}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, definition["factor_id"])
            self.factor_list.addItem(item)
            if definition["factor_id"] == previous:
                selected_row = self.factor_list.count() - 1
        self.factor_list.blockSignals(False)
        self.factor_empty_label.setVisible(self.factor_list.count() == 0)
        if selected_row >= 0:
            self.factor_list.setCurrentRow(selected_row)
        elif self.factor_list.count():
            self.factor_list.setCurrentRow(0)
        else:
            self.current_factor_id = None
            self.factor_detail_title.setText("请选择一个因子")
            self.definition_view.setMarkdown("暂无符合筛选条件的因子。")
            self.research_view.setMarkdown("暂无研究结果。")

    def initialize_defaults(self) -> None:
        created = initialize_default_factor_definitions(self.store_root)
        self.refresh_factors()
        self.status_message.emit(f"默认因子库已登记 {len(created)} 个新因子；默认未启用。")

    def _current_definition(self) -> dict[str, Any] | None:
        return next((item for item in self.factor_definitions if item["factor_id"] == self.current_factor_id), None)

    def select_factor(self, row: int) -> None:
        item = self.factor_list.item(row)
        if item is None:
            return
        self.current_factor_id = str(item.data(Qt.ItemDataRole.UserRole))
        definition = self._current_definition()
        if definition is None:
            return
        self.factor_detail_title.setText(f"{definition.get('name', self.current_factor_id)}  ·  {self.current_factor_id}")
        data_status = get_factor_data_status(self.store_root, self.current_factor_id)
        supported = ", ".join(definition.get("supported_asset_types", [])) or "未设置"
        research_summary = definition.get("research_summary") or {}
        self.definition_view.setMarkdown(
            "\n".join([
                "## 因子定义",
                "",
                f"| 项目 | 内容 |",
                "|---|---|",
                f"| 经济假设 | {definition.get('hypothesis') or '未填写'} |",
                f"| 类别 | {definition.get('category') or '未设置'} |",
                f"| 公式 | `{definition.get('formula') or '未设置'}` |",
                f"| 公式 MD5 | `{definition.get('formula_hash') or '未设置'}` |",
                 f"| 值语义 | `{data_status.get('value_semantics') or '未读取'}` |",
                 f"| 暴露范围 | `{definition.get('value_scope') or 'asset'}` |",
                 f"| 支持资产 | {supported} |",
                f"| 默认期限 | {definition.get('default_horizon') or '未设置'} |",
                f"| 缺失策略 | {definition.get('missing_policy') or '未设置'} |",
                f"| Parquet | {data_status.get('parquet_status')}，{data_status.get('rows', 0)} 行 |",
                f"| 数据截至 | {data_status.get('as_of') or '不可用'} |",
                f"| 研究摘要 | {research_summary or '暂无'} |",
            ])
        )
        rows = self._store().list_factor_research(self.current_factor_id, limit=5)
        if not rows:
            self.research_view.setMarkdown("尚未导入离线研究结论。\n\n请先通过 Notebook 或 `research_factor()` 生成研究结果。")
        else:
            lines = ["## 最近研究结果", "", "| 截至日期 | 期限 | IC | Rank IC | ICIR | 分组收益差 | 成本后收益 | 最大回撤 | 质量 | 状态 |", "|---|---|---:|---:|---:|---:|---:|---:|---|---|"]
            for row_data in rows:
                payload = row_data.get("payload", {})
                lines.append(
                    f"| {row_data.get('as_of') or '—'} | {row_data.get('horizon') or '—'} | "
                    f"{payload.get('ic', '—')} | {payload.get('rank_ic', '—')} | {payload.get('icir', '—')} | "
                    f"{payload.get('group_spread', '—')} | {payload.get('net_return', '—')} | "
                    f"{payload.get('max_drawdown', '—')} | {payload.get('quality_level', '—')} | {payload.get('status', '—')} |"
                )
            self.research_view.setMarkdown("\n".join(lines))
        self.load_profile()

    def load_profile(self) -> None:
        if not self.current_factor_id:
            return
        asset_type = self.profile_asset_type.currentData()
        horizon = self.profile_horizon.currentData()
        profiles = self._store().list_factor_activations()
        profile = next((item for item in profiles if item["factor_id"] == self.current_factor_id and item["asset_type"] == asset_type and item["horizon"] == horizon), None)
        self.profile_enabled.setChecked(bool(profile and int(profile.get("enabled", 0))))
        self.profile_weight.setValue(float(profile.get("weight", 1.0) if profile else 1.0))
        self.profile_drawdown.setValue(float(profile.get("max_drawdown_override", -0.15) if profile else -0.15))
        self.profile_suspend.setChecked(bool(profile is None or int(profile.get("suspend_on_breach", 1))))
        suspended = bool(profile and profile.get("status") == "SUSPENDED")
        self.profile_status.setText(
            "已暂停：" + str(profile.get("suspend_reason") or "等待人工复核") if suspended else ("已启用" if profile and int(profile.get("enabled", 0)) else "未启用")
        )
        self.profile_enabled.setEnabled(not suspended)
        self.save_profile_button.setEnabled(not suspended)
        self.restore_profile_button.setEnabled(suspended)

    def save_profile(self) -> None:
        if not self.current_factor_id:
            self.status_message.emit("请先选择一个因子。")
            return
        self._store().upsert_factor_activation({
            "factor_id": self.current_factor_id,
            "asset_type": self.profile_asset_type.currentData(),
            "horizon": self.profile_horizon.currentData(),
            "enabled": int(self.profile_enabled.isChecked()),
            "weight": self.profile_weight.value(),
            "max_drawdown_override": self.profile_drawdown.value(),
            "suspend_on_breach": int(self.profile_suspend.isChecked()),
            "status": "ENABLED",
        })
        self.refresh_factors()
        self.status_message.emit(f"已保存 {self.current_factor_id} 的应用配置。")

    def restore_profile(self) -> None:
        if not self.current_factor_id:
            return
        profiles = self._store().list_factor_activations()
        profile = next((item for item in profiles if item["factor_id"] == self.current_factor_id and item["asset_type"] == self.profile_asset_type.currentData() and item["horizon"] == self.profile_horizon.currentData()), None)
        if profile:
            self._store().restore_factor(profile["profile_id"], reason="桌面端人工恢复")
            self.refresh_factors()
            self.status_message.emit("因子配置已人工恢复。")

    def run_score_preview(self) -> None:
        codes = [item for item in re.split(r"[\s,，;；]+", self.preview_codes.toPlainText().strip()) if item]
        if not codes:
            self.score_status.setText("请先输入至少一个资产代码。")
            return
        target_date = self.preview_date.text().strip() or date.today().isoformat()
        self.score_button.setEnabled(False)
        self.score_status.setText("正在计算评分…")
        self.score_worker = FactorScoreWorker(
            target_date=target_date,
            asset_type=self.preview_asset_type.currentData(),
            horizon=self.preview_horizon.currentData(),
            universe=codes,
            store_root=self.store_root,
            macro_regime=self.preview_regime.text().strip() or None,
            parent=self,
        )
        self.score_worker.completed.connect(self.show_score_result)
        self.score_worker.failed.connect(self.show_score_error)
        self.score_worker.finished.connect(lambda: self.score_button.setEnabled(True))
        self.score_worker.start()

    def _emit_diagnostic_action(self, action: str) -> None:
        payload = {
            "codes": [
                item
                for item in re.split(r"[\s,，；;]+", self.preview_codes.toPlainText().strip())
                if item
            ],
            "asset_type": self.preview_asset_type.currentData(),
            "horizon": self.preview_horizon.currentData(),
            "factor_id": self.current_factor_id,
        }
        self.diagnostic_action.emit(action, payload)

    def _switch_asset_type_from_diagnostic(self) -> None:
        codes = [
            item
            for item in re.split(r"[\s,，；;]+", self.preview_codes.toPlainText().strip())
            if item
        ]
        looks_like_etf = any(
            str(code).upper().replace(".SH", "").replace(".SZ", "").isdigit()
            and str(code).upper().replace(".SH", "").replace(".SZ", "")[:2] in {"15", "51", "56", "58"}
            for code in codes
        )
        target = "ETF" if looks_like_etf else "STOCK"
        index = self.preview_asset_type.findData(target)
        if index >= 0:
            self.preview_asset_type.setCurrentIndex(index)
        self.score_status.setText(f"已将当前评分资产类型切换为 {target}；请确认并保存对应因子配置。")

    def _focus_factor_from_diagnostic(self) -> None:
        factor_id = self.current_factor_id
        for item in getattr(self, "_last_diagnostics", []):
            if item.get("factor_id"):
                factor_id = str(item["factor_id"])
                break
        if factor_id:
            for row in range(self.factor_list.count()):
                item = self.factor_list.item(row)
                if item and str(item.data(Qt.ItemDataRole.UserRole)) == factor_id:
                    self.factor_list.setCurrentRow(row)
                    break
            self.detail_tabs.setCurrentIndex(0)
        self.status_message.emit("已定位到相关因子配置，请检查资产类型、投资期限和启用状态。")

    def _render_diagnostics(self, result: Any) -> None:
        diagnostics = list(getattr(result, "diagnostics", []) or [])
        self._last_diagnostics = diagnostics
        coverage = getattr(result, "coverage", {}) or {}
        asset_count = int(coverage.get("asset_count", len(result.scores or [])))
        available_count = int(coverage.get("available_asset_count", 0))
        unavailable_count = int(coverage.get("unavailable_asset_count", max(asset_count - available_count, 0)))
        if not diagnostics:
            self.diagnostic_summary.setText(
                f"当前没有诊断问题。资产 {asset_count} 个，可用 {available_count} 个，不可用 {unavailable_count} 个。"
            )
            self.diagnostic_view.setMarkdown("暂无需要处理的问题。")
        else:
            self.diagnostic_summary.setText(
                f"发现 {len(diagnostics)} 项诊断：资产 {asset_count} 个，可用 {available_count} 个，不可用 {unavailable_count} 个。"
            )
            lines = ["## 需要处理的问题", ""]
            seen: set[tuple[str, str | None, str | None]] = set()
            for item in diagnostics[:12]:
                key = (str(item.get("code")), item.get("factor_id"), item.get("asset_code"))
                if key in seen:
                    continue
                seen.add(key)
                scope = ""
                if item.get("factor_id"):
                    scope += f"因子：{item['factor_id']}；"
                if item.get("asset_code"):
                    scope += f"资产：{item['asset_code']}；"
                lines.extend([
                    f"### {item.get('title', item.get('code', '未知问题'))}",
                    f"- {scope}{item.get('detail', '')}",
                    f"- 解决建议：{item.get('solution', '请检查数据和配置。')}",
                    "",
                ])
            self.diagnostic_view.setMarkdown("\n".join(lines))
        action_names = {
            str(action)
            for item in diagnostics
            for action in (item.get("actions") or [])
        }
        self.open_data_button.setEnabled(bool(action_names & {"open_data", "prepare_factor"}))
        self.prepare_factor_button.setEnabled("prepare_factor" in action_names)
        self.switch_asset_button.setEnabled("switch_asset_type" in action_names)
        self.recalculate_button.setEnabled(bool(diagnostics))
        self.open_factor_config_button.setEnabled("open_factor_config" in action_names)

    def _score_column_label(self, column: str) -> str:
        if column.startswith("score_"):
            factor_id = column[len("score_"):]
            definition = next(
                (item for item in self.factor_definitions if item.get("factor_id") == factor_id),
                None,
            )
            name = str(definition.get("name") if definition else factor_id)
            return f"{name}得分"
        return SCORE_COLUMN_LABELS.get(column, column)

    @staticmethod
    def _score_column_order(columns: list[str]) -> list[str]:
        preferred = [
            "asset_code",
            "as_of_date",
            "availability_status",
            "availability_reason",
            "composite_score",
        ]
        factor_columns = [column for column in columns if column.startswith("score_")]
        ordered = [column for column in preferred if column in columns and column != "composite_score"]
        ordered.extend(factor_columns)
        if "composite_score" in columns:
            ordered.append("composite_score")
        ordered.extend(column for column in columns if column not in ordered and column != "factor_score")
        return ordered

    @staticmethod
    def _format_score_value(column: str, value: Any) -> str:
        if value is None or (isinstance(value, float) and value != value):
            return "不可用"
        if column in {"availability_status", "availability_reason", "asset_code", "as_of_date"}:
            return str(value)
        if isinstance(value, (int, float)):
            return f"{value:.2f}"
        return str(value)

    def show_score_result(self, result: Any) -> None:
        self._render_diagnostics(result)
        rows = result.scores or []
        columns = ["asset_code"]
        for row in rows:
            for key in row:
                if key not in columns and key not in {"factor_score"}:
                    columns.append(key)
        columns = self._score_column_order(columns)
        self.score_table.setColumnCount(len(columns))
        self.score_table.setRowCount(len(rows))
        self.score_table.setHorizontalHeaderLabels([self._score_column_label(column) for column in columns])
        for row_index, row in enumerate(rows):
            for column_index, column in enumerate(columns):
                value = self._format_score_value(column, row.get(column, ""))
                self.score_table.setItem(row_index, column_index, QTableWidgetItem(value))
        header = self.score_table.horizontalHeader()
        header.setToolTip("评分字段已翻译为中文；后台 CSV/JSON 仍保留原始字段名。")
        for column, width in {
            "asset_code": 110,
            "as_of_date": 120,
            "availability_status": 90,
            "availability_reason": 230,
            "composite_score": 100,
        }.items():
            if column in columns:
                self.score_table.setColumnWidth(columns.index(column), width)
        warnings = "；".join(result.warnings) if result.warnings else "无警告"
        self.score_status.setText(f"完成：{len(rows)} 个资产；{warnings}")

    def show_score_error(self, message: str) -> None:
        self.score_status.setText(f"评分失败：{message}")


__all__ = ["FactorResearchPage", "FactorScoreWorker"]
