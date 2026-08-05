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
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QSpinBox,
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
from qteasy_research.core.global_etf_trade_conversion import convert_research_score_to_trade
from qteasy_research.core.global_etf_trade_mapping import effective_trade_mapping
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
    "rule_id": "ID",
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
    "rejected_at": "驳回/撤销时间",
    "reason": "审核意见",
}

# 规则历史版本表列中文翻译
RULE_HISTORY_COLUMN_LABELS = {
    "history_id": "版本",
    "action": "动作",
    "status": "规则状态",
    "modifier": "修正",
    "sample_count": "样本",
    "confidence": "置信度",
    "approved_by": "批准人",
    "approved_at": "批准时间",
    "rejected_by": "驳回人",
    "rejected_at": "驳回时间",
    "reason": "意见/原因",
    "changed_at": "变更时间",
}

# 审核动作 → 中文动作名
RULE_ACTION_LABELS = {
    "create": "创建",
    "update": "编辑",
    "approve": "确认",
    "reject": "驳回",
    "revoke": "撤销",
    "reset": "重新提交",
}

# 交易资产映射表列中文翻译
MAPPING_COLUMN_LABELS = {
    "research_asset_code": "研究资产",
    "trade_asset_code": "交易资产",
    "trade_asset_name": "名称",
    "trade_market": "市场",
    "currency": "币种",
    "fx_pair": "汇率对",
    "fx_rule": "汇率方式",
    "exchange_rate": "汇率",
    "management_fee": "管理费%",
    "trading_cost_bps": "成本bps",
    "tracking_error": "跟踪误差%",
    "premium_discount": "折溢价%",
    "market_timezone": "时区",
    "trading_hours": "交易时段",
    "holiday_risk": "休市风险",
    "priority": "优先级",
    "status": "状态",
}

# 交易口径换算表列中文翻译（独立展示，不改动研究评分）
TRADE_CONVERSION_COLUMN_LABELS = {
    "asset": "研究资产",
    "trade_asset_code": "交易资产",
    "trade_asset_name": "名称",
    "currency": "币种",
    "exchange_rate": "汇率",
    "management_fee": "管理费%",
    "trading_cost_bps": "成本bps",
    "tracking_error": "跟踪误差%",
    "premium_discount": "折溢价%",
    "research_final_score": "研究评分",
    "fee_adjustment": "费用调整",
    "cost_adjustment": "成本调整",
    "trade_final_score": "交易评分",
    "status": "状态",
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
        self._last_scores: list[dict[str, Any]] | None = None
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

        # 交易口径换算区（独立展示，不改动研究评分）
        root_layout.addWidget(self._build_trade_conversion_section(), 3)

        # 交易资产映射配置区
        root_layout.addWidget(self._build_mapping_section(), 3)

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

        # 顶部：状态筛选 + 审核动作按钮
        top = QHBoxLayout()
        top.addWidget(QLabel("状态筛选"))
        self.rule_status_filter = QComboBox()
        self.rule_status_filter.addItems(["全部", "DRAFT", "APPROVED", "REJECTED"])
        self.rule_status_filter.currentTextChanged.connect(lambda _: self._refresh_rules_table())
        top.addWidget(self.rule_status_filter)
        top.addStretch(1)
        self.rule_approve_button = QPushButton("确认")
        self.rule_approve_button.setToolTip("DRAFT → APPROVED，批准生效")
        self.rule_approve_button.clicked.connect(lambda: self._rule_action("approve"))
        top.addWidget(self.rule_approve_button)
        self.rule_reject_button = QPushButton("驳回")
        self.rule_reject_button.setToolTip("DRAFT → REJECTED，否定该规则")
        self.rule_reject_button.clicked.connect(lambda: self._rule_action("reject"))
        top.addWidget(self.rule_reject_button)
        self.rule_revoke_button = QPushButton("撤销")
        self.rule_revoke_button.setToolTip("APPROVED → REJECTED，废止已批准规则")
        self.rule_revoke_button.clicked.connect(lambda: self._rule_action("revoke"))
        top.addWidget(self.rule_revoke_button)
        self.rule_reset_button = QPushButton("重新提交")
        self.rule_reset_button.setToolTip("REJECTED → DRAFT，重新走审核")
        self.rule_reset_button.clicked.connect(lambda: self._rule_action("reset"))
        top.addWidget(self.rule_reset_button)
        self.rule_history_button = QPushButton("历史")
        self.rule_history_button.setToolTip("查看该规则的版本变更链")
        self.rule_history_button.clicked.connect(self._show_rule_history)
        top.addWidget(self.rule_history_button)
        layout.addLayout(top)

        self.rules_table = QTableWidget()
        self._configure_table(self.rules_table)
        self.rules_table.itemSelectionChanged.connect(self._on_rule_selection_changed)
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

    def _build_trade_conversion_section(self) -> QWidget:
        box = QGroupBox("交易口径换算（研究评分 → 可交易标的，独立参考）")
        layout = QVBoxLayout(box)
        self.trade_conversion_table = QTableWidget()
        self._configure_table(self.trade_conversion_table)
        layout.addWidget(self.trade_conversion_table)
        return box

    def _build_mapping_section(self) -> QWidget:
        box = QGroupBox("交易资产映射（研究资产 → 可交易标的）")
        layout = QVBoxLayout(box)

        # 顶部：研究资产选择 + 操作按钮
        top = QHBoxLayout()
        top.addWidget(QLabel("研究资产"))
        self.mapping_asset_combo = QComboBox()
        self.mapping_asset_combo.addItems(list(GLOBAL_ETF_ASSETS))
        self.mapping_asset_combo.currentTextChanged.connect(lambda _: self._refresh_mapping_table())
        top.addWidget(self.mapping_asset_combo)
        top.addStretch(1)
        self.mapping_refresh_button = QPushButton("刷新")
        self.mapping_refresh_button.clicked.connect(self._refresh_mapping_table)
        top.addWidget(self.mapping_refresh_button)
        layout.addLayout(top)

        # 映射表格
        self.mapping_table = QTableWidget()
        self._configure_table(self.mapping_table)
        self.mapping_table.itemSelectionChanged.connect(self._on_mapping_selection_changed)
        layout.addWidget(self.mapping_table)

        # 底部：新增/编辑表单
        form = QFormLayout()
        self.mapping_trade_code = QLineEdit()
        self.mapping_trade_code.setPlaceholderText("如 513500.SH / 007300.OF / 518880.SH")
        form.addRow("交易资产代码", self.mapping_trade_code)
        self.mapping_trade_name = QLineEdit()
        form.addRow("交易资产名称", self.mapping_trade_name)
        self.mapping_currency = QComboBox()
        self.mapping_currency.addItems(["CNY", "USD"])
        form.addRow("交易币种", self.mapping_currency)
        self.mapping_fx_rule = QComboBox()
        self.mapping_fx_rule.addItems(["static", "manual", "realtime", "estimate"])
        form.addRow("汇率方式", self.mapping_fx_rule)
        self.mapping_exchange_rate = QDoubleSpinBox()
        self.mapping_exchange_rate.setRange(0.0, 100.0)
        self.mapping_exchange_rate.setDecimals(4)
        self.mapping_exchange_rate.setValue(1.0)
        form.addRow("汇率", self.mapping_exchange_rate)
        self.mapping_management_fee = QDoubleSpinBox()
        self.mapping_management_fee.setRange(0.0, 10.0)
        self.mapping_management_fee.setDecimals(2)
        form.addRow("管理费%", self.mapping_management_fee)
        self.mapping_trading_cost = QDoubleSpinBox()
        self.mapping_trading_cost.setRange(0.0, 1000.0)
        self.mapping_trading_cost.setDecimals(1)
        form.addRow("交易成本(bps)", self.mapping_trading_cost)
        self.mapping_tracking_error = QDoubleSpinBox()
        self.mapping_tracking_error.setRange(0.0, 50.0)
        self.mapping_tracking_error.setDecimals(2)
        form.addRow("跟踪误差%", self.mapping_tracking_error)
        self.mapping_premium_discount = QDoubleSpinBox()
        self.mapping_premium_discount.setRange(-50.0, 50.0)
        self.mapping_premium_discount.setDecimals(2)
        form.addRow("折溢价%", self.mapping_premium_discount)
        self.mapping_priority = QSpinBox()
        self.mapping_priority.setRange(1, 99)
        self.mapping_priority.setValue(1)
        form.addRow("优先级(小优先)", self.mapping_priority)
        form_row = QHBoxLayout()
        self.mapping_save_button = QPushButton("保存映射")
        self.mapping_save_button.clicked.connect(self.save_mapping)
        self.mapping_deactivate_button = QPushButton("停用选中")
        self.mapping_deactivate_button.clicked.connect(self.deactivate_mapping)
        self.mapping_delete_button = QPushButton("删除选中")
        self.mapping_delete_button.clicked.connect(self.delete_mapping)
        form_row.addWidget(self.mapping_save_button)
        form_row.addWidget(self.mapping_deactivate_button)
        form_row.addWidget(self.mapping_delete_button)
        form_row.addStretch(1)
        form.addRow(form_row)
        layout.addLayout(form)
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
        """刷新数据状态、宏观状态、条件收益、规则状态、换算与映射（不触发引擎重算）。"""
        self._refresh_status_macro()
        self._refresh_condition_table()
        self._refresh_rules_table()
        self._refresh_trade_conversion_table()
        self._refresh_mapping_table()
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
        status = self.rule_status_filter.currentText()
        try:
            rules = self._store().list_global_etf_macro_rules(
                status=None if status == "全部" else status
            )
        except Exception:
            rules = []
        self._fill_table(self.rules_table, rules, RULE_COLUMN_LABELS)
        self._on_rule_selection_changed()

    def _selected_rule(self) -> dict[str, Any] | None:
        """返回当前选中行的规则（按第一列 rule_id 反查）。"""
        row = self.rules_table.currentRow()
        if row < 0:
            return None
        item = self.rules_table.item(row, 0)
        if item is None or not item.text().isdigit():
            return None
        rule_id = int(item.text())
        for rule in self._store().list_global_etf_macro_rules():
            if int(rule["rule_id"]) == rule_id:
                return rule
        return None

    def _on_rule_selection_changed(self) -> None:
        """按选中规则的当前状态启用/禁用审核按钮。"""
        rule = self._selected_rule()
        status = rule.get("status") if rule else None
        self.rule_approve_button.setEnabled(status == "DRAFT")
        self.rule_reject_button.setEnabled(status == "DRAFT")
        self.rule_revoke_button.setEnabled(status == "APPROVED")
        self.rule_reset_button.setEnabled(status == "REJECTED")
        self.rule_history_button.setEnabled(rule is not None)

    def _rule_action(self, action: str) -> None:
        """执行确认/驳回/撤销/重新提交，收集可选审核意见。"""
        rule = self._selected_rule()
        if rule is None:
            self.page_status.setText("请先选中要操作的规则行")
            return
        labels = {
            "approve": ("确认规则", f"确认 {rule['asset_code']}/{rule['macro_state']} 为 APPROVED？"),
            "reject": ("驳回规则", f"驳回 {rule['asset_code']}/{rule['macro_state']}？"),
            "revoke": ("撤销规则", f"撤销已批准的 {rule['asset_code']}/{rule['macro_state']}？"),
            "reset": ("重新提交", f"将 {rule['asset_code']}/{rule['macro_state']} 退回 DRAFT 重新审核？"),
        }
        title, prompt = labels[action]
        note, ok = QInputDialog.getText(self, title, prompt + "\n审核意见（可空）：")
        if not ok:
            return
        try:
            store = self._store()
            rule_id = int(rule["rule_id"])
            if action == "approve":
                store.approve_global_etf_macro_rule(rule_id, approved_by="manual", note=note.strip())
            elif action == "reject":
                store.reject_global_etf_macro_rule(rule_id, rejected_by="manual", reason=note.strip())
            elif action == "revoke":
                store.revoke_global_etf_macro_rule(rule_id, rejected_by="manual", reason=note.strip())
            elif action == "reset":
                store.reset_global_etf_macro_rule_to_draft(rule_id, note=note.strip())
            self._refresh_rules_table()
            self.page_status.setText(f"已{labels[action][0]}：{rule['asset_code']}/{rule['macro_state']}")
        except ValueError as exc:
            self.page_status.setText(f"操作失败：{exc}")

    def _show_rule_history(self) -> None:
        """弹出对话框展示选中规则的版本变更链。"""
        rule = self._selected_rule()
        if rule is None:
            self.page_status.setText("请先选中要查看历史的规则行")
            return
        rule_id = int(rule["rule_id"])
        try:
            history = self._store().list_global_etf_macro_rule_history(rule_id)
        except Exception as exc:
            self.page_status.setText(f"读取历史失败：{exc}")
            return
        # 动作名翻译为中文
        history = [
            dict(row, action=RULE_ACTION_LABELS.get(row.get("action"), row.get("action")))
            for row in history
        ]

        dialog = QDialog(self)
        dialog.setWindowTitle(
            f"规则历史：{rule['asset_code']}/{rule['macro_state']}（{len(history)} 个版本）"
        )
        dialog.resize(760, 420)
        layout = QVBoxLayout(dialog)
        table = QTableWidget()
        self._configure_table(table)
        layout.addWidget(table)
        self._fill_table(table, history, RULE_HISTORY_COLUMN_LABELS)
        if not history:
            layout.addWidget(QLabel("暂无历史版本记录。"))
        dialog.exec()

    def _refresh_trade_conversion_table(self) -> None:
        """按最近一次评分的每行评分做交易口径换算并展示。

        未配置生效映射 → NO_MAPPING；研究评分不可用 → NO_RESEARCH_SCORE；
        仅独立展示，不改动研究口径 final_score。
        """
        scores = {row.get("asset"): row for row in (self._last_scores or [])}
        rows: list[dict[str, Any]] = []
        for asset in GLOBAL_ETF_ASSETS:
            score_row = scores.get(asset)
            if score_row is None:
                continue
            try:
                mapping = effective_trade_mapping(self._store(), asset)
                converted = convert_research_score_to_trade(score_row, mapping)
            except Exception as exc:
                converted = {
                    "asset": asset,
                    "status": "ERROR",
                    "research_final_score": score_row.get("final_score"),
                    "trade_final_score": None,
                    "warnings": [f"换算失败：{exc}"],
                }
            rows.append(converted)
        self._fill_table(
            self.trade_conversion_table,
            rows,
            TRADE_CONVERSION_COLUMN_LABELS,
            widths={
                "trade_asset_code": 130,
                "research_final_score": 90,
                "trade_final_score": 90,
            },
        )
        # 汇率说明与警告放进行工具提示，避免撑宽表格
        for row_index, converted in enumerate(rows):
            tips: list[str] = []
            fx_note = converted.get("fx_note")
            if fx_note:
                tips.append(str(fx_note))
            tips.extend(str(w) for w in (converted.get("warnings") or []))
            if tips:
                for column in range(self.trade_conversion_table.columnCount()):
                    item = self.trade_conversion_table.item(row_index, column)
                    if item is not None:
                        item.setToolTip("\n".join(tips))

    # ---- 交易资产映射 ----

    def _refresh_mapping_table(self) -> None:
        research_asset = self.mapping_asset_combo.currentText()
        try:
            mappings = self._store().get_global_etf_trade_mappings(research_asset, status=None)
        except Exception:
            mappings = []
        self._fill_table(self.mapping_table, mappings, MAPPING_COLUMN_LABELS)
        # 更新选中映射的有效性提示
        effective = effective_trade_mapping(self._store(), research_asset)
        tip = f"当前生效：{effective['trade_asset_code']}" if effective else "未配置生效映射"
        self.mapping_table.setToolTip(tip)

    def _on_mapping_selection_changed(self) -> None:
        selected = self.mapping_table.currentRow()
        if selected < 0:
            return
        item = self.mapping_table.item(selected, 1)  # trade_asset_code 列
        if item is None:
            return
        mappings = self.mapping_table
        # 从表格回填表单：通过当前选中的行数据（这里简单回填交易资产代码）
        self.mapping_trade_code.setText(item.text())

    def _mapping_payload_from_form(self, research_asset: str) -> dict[str, Any]:
        """从表单收集映射字段。exchange_rate 在 fx_rule=static 时必填。"""
        payload: dict[str, Any] = {
            "research_asset_code": research_asset,
            "trade_asset_code": self.mapping_trade_code.text().strip(),
            "trade_asset_name": self.mapping_trade_name.text().strip(),
            "currency": self.mapping_currency.currentText(),
            "fx_rule": self.mapping_fx_rule.currentText(),
            "exchange_rate": self.mapping_exchange_rate.value(),
            "management_fee": self.mapping_management_fee.value(),
            "trading_cost_bps": self.mapping_trading_cost.value(),
            "tracking_error": self.mapping_tracking_error.value(),
            "premium_discount": self.mapping_premium_discount.value(),
            "priority": self.mapping_priority.value(),
            "status": "ACTIVE",
        }
        return payload

    def save_mapping(self) -> None:
        research_asset = self.mapping_asset_combo.currentText()
        payload = self._mapping_payload_from_form(research_asset)
        if not payload["trade_asset_code"]:
            self.page_status.setText("请填写交易资产代码")
            return
        try:
            saved = self._store().upsert_global_etf_trade_mapping(payload)
            self._refresh_mapping_table()
            self.page_status.setText(f"已保存映射：{saved['trade_asset_code']}")
            self.status_message.emit(f"已保存交易资产映射 {research_asset} → {saved['trade_asset_code']}")
        except ValueError as exc:
            self.page_status.setText(f"保存失败：{exc}")

    def deactivate_mapping(self) -> None:
        self._set_mapping_status("INACTIVE")

    def delete_mapping(self) -> None:
        research_asset = self.mapping_asset_combo.currentText()
        row = self.mapping_table.currentRow()
        if row < 0:
            self.page_status.setText("请先选中要删除的映射行")
            return
        trade_code = self.mapping_trade_code.text().strip() or self._mapping_trade_at(row)
        if not trade_code:
            self.page_status.setText("无法识别选中的映射")
            return
        try:
            store = self._store()
            for mapping in store.list_global_etf_trade_mappings(research_asset_code=research_asset, trade_asset_code=trade_code):
                # 无软删，直接删行（mapping 是当前配置，INACTIVE 已表达停用）
                store.delete_global_etf_trade_mapping(mapping["mapping_id"])
            self._refresh_mapping_table()
            self.page_status.setText(f"已删除映射 {research_asset} → {trade_code}")
        except Exception as exc:
            self.page_status.setText(f"删除失败：{exc}")

    def _set_mapping_status(self, status: str) -> None:
        research_asset = self.mapping_asset_combo.currentText()
        row = self.mapping_table.currentRow()
        if row < 0:
            self.page_status.setText("请先选中要操作的映射行")
            return
        trade_code = self.mapping_trade_code.text().strip() or self._mapping_trade_at(row)
        if not trade_code:
            self.page_status.setText("无法识别选中的映射")
            return
        try:
            store = self._store()
            mapping = None
            for m in store.list_global_etf_trade_mappings(research_asset_code=research_asset, trade_asset_code=trade_code):
                mapping = m
                break
            if mapping is None:
                self.page_status.setText("未找到该映射")
                return
            mapping["status"] = status
            store.upsert_global_etf_trade_mapping(mapping)
            self._refresh_mapping_table()
            self.page_status.setText(f"已{('停用' if status == 'INACTIVE' else '启用')}映射 {trade_code}")
        except Exception as exc:
            self.page_status.setText(f"操作失败：{exc}")

    def _mapping_trade_at(self, row: int) -> str:
        item = self.mapping_table.item(row, 1)
        return item.text() if item else ""

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
        self._last_scores = rows
        self._fill_table(self.score_table, rows, SCORE_COLUMN_LABELS, widths={
            "asset": 60, "data_as_of": 110, "base_score": 100, "macro_modifier": 100, "final_score": 100, "warnings": 320,
        })
        self._refresh_trade_conversion_table()
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
