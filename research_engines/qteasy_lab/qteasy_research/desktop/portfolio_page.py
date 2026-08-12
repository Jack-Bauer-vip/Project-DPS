"""PySide6 桌面端组合分析（L3/L4）页面。

四步引导：①选择组合（契约策略或自定义权重）→ ②组合风险收益 →
③因子暴露与风险贡献（L4 g=w'X）→ ④比例建议（风险平价/逆波动/等权/有效前沿）。

- 后台 QThread 计算，避免阻塞 UI；错误友好提示。
- 一切展示为 ``REFERENCE_ONLY`` 参考，不生成交易指令。
- 只读 B 本地数据 + A 侧只读契约，只写 B 本地 reports/portfolio_analysis/。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if value != value:
            return "—"
        return f"{value:.{digits}f}"
    return str(value)


def _weights_text(weights: dict[str, float]) -> str:
    return "，".join(f"{asset}={w:.4f}" for asset, w in weights.items())


class PortfolioAnalysisWorker(QThread):
    """后台执行组合分析（数据加载 + analyze_portfolio）。"""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        *,
        weights: dict[str, float],
        bounds: dict[str, tuple[float, float]] | None,
        data_root: str | Path,
        factor_dir: str | Path,
        include_macro: bool,
        window: int,
        run_id: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.weights = weights
        self.bounds = bounds
        self.data_root = Path(data_root)
        self.factor_dir = Path(factor_dir)
        self.include_macro = include_macro
        self.window = window
        self.run_id = run_id

    def run(self) -> None:
        try:
            from qteasy_research.reference.backtest_engine import load_price_frames
            from qteasy_research.reference.factor_tear import (
                FACTOR_IDS,
                load_factor_panel_from_parquet,
            )
            from qteasy_research.reference.portfolio_analysis import (
                align_close_panel,
                analyze_portfolio,
            )

            factor_panels = load_factor_panel_from_parquet(self.factor_dir, FACTOR_IDS)
            universe = set(self.weights.keys())
            for panel in factor_panels.values():
                universe.update(panel.columns)
            frames = load_price_frames(
                sorted(universe), data_dir=self.data_root, online_ok=False
            )
            close_panel = align_close_panel(frames)

            macro_frames: dict[str, Any] = {}
            if self.include_macro:
                macro_dir = self.data_root / "processed" / "global_macro"
                for series_id in ("DGS30", "DFII10"):
                    path = macro_dir / f"{series_id}.csv"
                    if path.exists():
                        try:
                            import pandas as pd

                            macro_frames[series_id] = pd.read_csv(path)
                        except Exception:
                            continue

            result = analyze_portfolio(
                self.weights,
                close_panel,
                factor_panels,
                macro_frames=macro_frames,
                include_macro=self.include_macro,
                window=self.window,
                bounds=self.bounds,
                run_id=self.run_id,
            )
            self.completed.emit(result)
        except Exception as exc:  # noqa: BLE001 - 展示给用户
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class PortfolioPage(QWidget):
    status_message = Signal(str)

    def __init__(self, store_root: str | Path, parent=None) -> None:
        super().__init__(parent)
        self.store_root = Path(store_root)
        self.data_root = self.store_root.parent / "data"
        self.factor_dir = self.store_root / "factor_values"
        self.worker: PortfolioAnalysisWorker | None = None
        self._last_result: dict[str, Any] | None = None
        self._build_ui()
        self.refresh_strategies()

    def set_store_root(self, store_root: str | Path) -> None:
        self.store_root = Path(store_root)
        self.data_root = self.store_root.parent / "data"
        self.factor_dir = self.store_root / "factor_values"
        self.refresh_strategies()

    # ---- UI ----

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)

        # 标题行
        title_row = QHBoxLayout()
        title = QLabel("组合分析（L3/L4）")
        title.setObjectName("factorTitle")
        title_row.addWidget(title)
        title_row.addWidget(QLabel("组合风险收益 + 因子暴露 + 比例建议（REFERENCE_ONLY）"))
        title_row.addStretch(1)
        self.example_button = QPushButton("示例：三剑客")
        self.example_button.clicked.connect(self.fill_example)
        self.refresh_button = QPushButton("刷新策略")
        self.refresh_button.clicked.connect(self.refresh_strategies)
        self.run_button = QPushButton("运行分析")
        self.run_button.clicked.connect(self.run_analysis)
        title_row.addWidget(self.example_button)
        title_row.addWidget(self.refresh_button)
        title_row.addWidget(self.run_button)
        root.addLayout(title_row)

        # Step ①：选择组合
        step1 = QGroupBox("① 选择组合")
        s1 = QVBoxLayout(step1)
        mode_row = QHBoxLayout()
        self.contract_mode = QRadioButton("契约策略")
        self.contract_mode.setChecked(True)
        self.weights_mode = QRadioButton("自定义权重")
        self.contract_mode.toggled.connect(lambda _: self._sync_mode())
        mode_row.addWidget(self.contract_mode)
        self.strategy_combo = QComboBox()
        mode_row.addWidget(self.strategy_combo, 1)
        mode_row.addWidget(QLabel("权重（asset:w,asset:w）"))
        self.weights_edit = QLineEdit()
        self.weights_edit.setPlaceholderText("512890.SH:0.43,513650.SH:0.19,518880.SH:0.38")
        self.weights_edit.setEnabled(False)
        mode_row.addWidget(self.weights_edit, 1)
        s1.addLayout(mode_row)
        opt_row = QHBoxLayout()
        self.macro_check = QPushButton()
        self.macro_check.setText("含宏观因子（ΔDGS30/ΔDFII10）")
        self.macro_check.setCheckable(True)
        opt_row.addWidget(self.macro_check)
        opt_row.addWidget(QLabel("暴露窗口"))
        self.window_edit = QLineEdit("252")
        self.window_edit.setFixedWidth(60)
        opt_row.addWidget(self.window_edit)
        opt_row.addStretch(1)
        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)
        opt_row.addWidget(self.preview_label, 1)
        s1.addLayout(opt_row)
        self.strategy_hint = QLabel("")
        self.strategy_hint.setWordWrap(True)
        self.strategy_hint.setStyleSheet("color: #c62828;")
        s1.addWidget(self.strategy_hint)
        root.addWidget(step1)

        # Step ②：组合风险收益
        step2 = QGroupBox("② 组合风险收益")
        s2 = QVBoxLayout(step2)
        self.risk_table = QTableWidget()
        self._configure_table(self.risk_table)
        s2.addWidget(self.risk_table)
        self.risk_note = QTextBrowser()
        self.risk_note.setFixedHeight(70)
        s2.addWidget(self.risk_note)
        root.addWidget(step2)

        # Step ③：因子暴露与风险贡献
        step3 = QGroupBox("③ 因子暴露（L4 g=w'X）与风险贡献")
        s3 = QHBoxLayout(step3)
        self.exposure_table = QTableWidget()
        self._configure_table(self.exposure_table)
        s3.addWidget(self.exposure_table)
        self.rc_table = QTableWidget()
        self._configure_table(self.rc_table)
        s3.addWidget(self.rc_table)
        root.addWidget(step3)

        # Step ④：比例建议
        step4 = QGroupBox("④ 比例建议（参考）")
        s4 = QVBoxLayout(step4)
        self.prop_table = QTableWidget()
        self._configure_table(self.prop_table)
        s4.addWidget(self.prop_table)
        self.frontier_note = QLabel("")
        self.frontier_note.setWordWrap(True)
        s4.addWidget(self.frontier_note)
        root.addWidget(step4)

        self.page_status = QLabel("")
        self.page_status.setWordWrap(True)
        root.addWidget(self.page_status)

    @staticmethod
    def _configure_table(table: QTableWidget) -> None:
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

    @staticmethod
    def _fill_table(
        table: QTableWidget,
        headers: list[str],
        rows: list[list[Any]],
    ) -> None:
        table.clear()
        table.setColumnCount(len(headers))
        table.setRowCount(len(rows))
        table.setHorizontalHeaderLabels(headers)
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(_fmt(value)))

    # ---- 策略加载 / 模式 ----

    def refresh_strategies(self) -> None:
        self.strategy_combo.clear()
        self.strategy_hint.setText("")
        try:
            from qteasy_research.reference.backtest_engine import parse_contract
            from qteasy_research.reference.config import STRATEGY_CONTRACT_PATH

            contract = parse_contract(STRATEGY_CONTRACT_PATH)
            enabled = [s for s in contract.strategies if s.enabled]
            self.strategy_combo.addItem("请选择策略", None)
            for strategy in enabled:
                self.strategy_combo.addItem(strategy.strategy_id, strategy)
            if self.strategy_combo.count() <= 1:
                self.strategy_hint.setText("契约中无启用策略")
        except Exception as exc:  # noqa: BLE001
            self.strategy_hint.setText(f"契约不可用：{exc}")

    def _sync_mode(self) -> None:
        contract_on = self.contract_mode.isChecked()
        self.strategy_combo.setEnabled(contract_on)
        self.weights_edit.setEnabled(not contract_on)
        self._update_preview()

    def _current_weights(self) -> tuple[dict[str, float], dict[str, tuple[float, float]] | None, str]:
        """返回 (weights, bounds, source)；解析失败抛 ValueError。"""
        if self.contract_mode.isChecked():
            from qteasy_research.reference.portfolio_analysis import strategy_analysis_inputs

            strategy = self.strategy_combo.currentData()
            if strategy is None:
                raise ValueError("请选择一个契约策略")
            inputs = strategy_analysis_inputs(strategy)
            if inputs["error"]:
                raise ValueError(inputs["error"])
            return inputs["weights"], inputs["bounds"], inputs["source"]
        # 自定义权重
        text = self.weights_edit.text().strip()
        if not text:
            raise ValueError("请输入自定义权重（asset:w,asset:w）")
        weights: dict[str, float] = {}
        for part in text.split(","):
            part = part.strip()
            if not part or ":" not in part:
                raise ValueError(f"权重格式错误：{part!r}（应为 asset:w）")
            asset, value = part.rsplit(":", 1)
            try:
                number = float(value)
            except ValueError:
                raise ValueError(f"权重值错误：{value!r}") from None
            if number < 0:
                raise ValueError(f"权重不可为负：{asset}={number}")
            weights[asset.strip()] = number
        if not weights:
            raise ValueError("未解析出任何权重")
        return weights, None, "direct"

    def _update_preview(self) -> None:
        try:
            weights, bounds, source = self._current_weights()
            total = sum(weights.values()) or 1.0
            text = f"{source}｜{'，'.join(f'{a}={w/total:.3f}' for a, w in weights.items())}"
            if bounds:
                text += "｜契约 bounds 已启用"
            self.preview_label.setText(text)
            self.preview_label.setStyleSheet("")
        except ValueError as exc:
            self.preview_label.setText(str(exc))
            self.preview_label.setStyleSheet("color: #c62828;")

    # ---- 动作 ----

    def fill_example(self) -> None:
        """示例：三剑客（契约存在时）或直接填权重。"""
        try:
            from qteasy_research.reference.backtest_engine import parse_contract
            from qteasy_research.reference.config import STRATEGY_CONTRACT_PATH

            contract = parse_contract(STRATEGY_CONTRACT_PATH)
            idx = self.strategy_combo.findText("three_musketeers")
            if idx >= 0:
                self.contract_mode.setChecked(True)
                self.strategy_combo.setCurrentIndex(idx)
                self.page_status.setText("示例已填入：three_musketeers（点击“运行分析”）")
                self._update_preview()
                return
        except Exception:
            pass
        # 契约不可用 → 直接填权重示例
        self.weights_mode.setChecked(True)
        self.weights_edit.setText("512890.SH:0.43,513650.SH:0.19,518880.SH:0.38")
        self.page_status.setText("示例已填入：自定义权重三剑客（点击“运行分析”）")
        self._update_preview()

    def run_analysis(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.page_status.setText("已有分析在运行，请稍候…")
            return
        try:
            weights, bounds, _ = self._current_weights()
            window_text = self.window_edit.text().strip()
            try:
                window = int(window_text) if window_text else 252
            except ValueError:
                raise ValueError(f"窗口值错误：{window_text!r}") from None
        except ValueError as exc:
            self.page_status.setText(f"输入有误：{exc}")
            return

        self.run_button.setEnabled(False)
        self.page_status.setText("正在分析…（后台计算，请稍候）")
        self.worker = PortfolioAnalysisWorker(
            weights=weights,
            bounds=bounds,
            data_root=self.data_root,
            factor_dir=self.factor_dir,
            include_macro=self.macro_check.isChecked(),
            window=window,
            run_id="desktop",
            parent=self,
        )
        self.worker.completed.connect(self._on_completed)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_completed(self, result: dict[str, Any]) -> None:
        self._last_result = result
        self._render_risk(result)
        self._render_exposure(result)
        self._render_proportions(result)
        warnings = "；".join(result["warnings"]) if result["warnings"] else "无警告"
        self.page_status.setText(f"完成：{len(result['assets'])} 个资产；{warnings}")
        self.status_message.emit(
            f"组合分析完成：{','.join(result['assets'])} vol="
            f"{result['portfolio']['annual_vol_factor_model']}"
        )

    def _on_failed(self, message: str) -> None:
        self.page_status.setText(f"分析失败：{message}")

    def _on_finished(self) -> None:
        self.run_button.setEnabled(True)

    # ---- 渲染 ----

    def _render_risk(self, result: dict[str, Any]) -> None:
        portfolio = result["portfolio"]
        rows = [
            ["年化收益", portfolio["annual_return"]],
            ["年化波动（因子模型）", portfolio["annual_vol_factor_model"]],
            ["年化波动（样本）", portfolio["annual_vol_sample"]],
            ["年化波动（EWMA）", portfolio["annual_vol_ewma"]],
            ["最大回撤", portfolio["max_drawdown"]],
            ["Sharpe", portfolio["sharpe"]],
        ]
        self._fill_table(self.risk_table, ["指标", "数值"], rows)
        lines: list[str] = []
        if portfolio["vol_consistency_warnings"]:
            lines.append("波动一致性警告：")
            lines.extend(f"· {w}" for w in portfolio["vol_consistency_warnings"])
        if result["dropped_assets"]:
            lines.append(f"已剔除（暴露不足，不虚构）：{', '.join(result['dropped_assets'])}")
        self.risk_note.setPlainText("\n".join(lines) if lines else "三口径波动一致性良好")

    def _render_exposure(self, result: dict[str, Any]) -> None:
        exposure = result["factor_exposure"]
        exp_rows = [[factor, row["exposure"]] for factor, row in exposure.iterrows()]
        self._fill_table(self.exposure_table, ["因子", "组合暴露 g"], exp_rows)
        rc = result["asset_risk_contributions"]
        rc_rows = [
            [row["asset"], row["weight"], row["rc"]] for _, row in rc.iterrows()
        ]
        self._fill_table(self.rc_table, ["资产", "权重", "风险贡献 RC"], rc_rows)

    def _render_proportions(self, result: dict[str, Any]) -> None:
        props = result["proportions"]
        rows: list[list[Any]] = []
        for key, label in (
            ("risk_parity", "风险平价"),
            ("inverse_vol", "逆波动"),
            ("equal_weight", "等权"),
        ):
            item = props.get(key, {})
            status = item.get("status")
            method = item.get("method")
            weights = item.get("weights", {})
            note = ""
            if item.get("warning"):
                note = str(item["warning"])
            rows.append([label, status, method, _weights_text(weights), note])
        frontier = props.get("efficient_frontier", {})
        rows.append([
            "有效前沿",
            frontier.get("status"),
            f"{len(frontier.get('points', []))} 点",
            "",
            frontier.get("warning") or "",
        ])
        self._fill_table(
            self.prop_table, ["方法", "状态", "算法", "权重", "说明"], rows
        )
        self.frontier_note.setText(
            "提示：无 scipy 时有效前沿诚实省略（status=degraded），"
            "比例建议仅给风险平价/逆波动/等权三锚点；全部为参考，不自动批准。"
        )
