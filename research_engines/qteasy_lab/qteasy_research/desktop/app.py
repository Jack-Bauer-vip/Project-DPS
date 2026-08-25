"""PySide6 原生桌面工作台 MVP。"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from qteasy_research.desktop.settings import load_settings, save_settings


def launch() -> int:
    try:
        from PySide6.QtCore import QProcess, QThread, Qt, Signal
        from PySide6.QtGui import QAction, QFont
        from PySide6.QtWidgets import (
            QApplication,
            QCheckBox,
            QComboBox,
            QDialog,
            QDoubleSpinBox,
            QFileDialog,
            QFormLayout,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QListWidget,
            QMainWindow,
            QMessageBox,
            QPlainTextEdit,
            QProgressBar,
            QPushButton,
            QSpinBox,
            QSplitter,
            QStackedWidget,
            QTabWidget,
            QTextBrowser,
            QTextEdit,
            QToolBar,
            QVBoxLayout,
            QWidget,
        )
    except ImportError as exc:
        raise RuntimeError("桌面端需要 PySide6，请执行：pip install -e \".[desktop]\"") from exc

    from qteasy_research.desktop.factor_page import FactorResearchPage
    from qteasy_research.desktop.data_page import DataManagementPage
    from qteasy_research.desktop.global_etf_page import GlobalEtfPage
    from qteasy_research.desktop.portfolio_page import PortfolioPage
    from qteasy_research.pretrade import (
        add_asset_reference,
        add_project_decision,
        add_project_note,
        delete_research_report,
        discard_report_draft,
        export_report_bundle,
        export_research_report,
        get_asset_dynamic_snapshot,
        get_asset_profile,
        get_report_draft,
        list_asset_factor_snapshots,
        list_asset_reports,
        list_portfolio_asset_contexts,
        metadata_display_rows,
        create_research_project,
        create_strategy_project,
        get_research_project,
        list_asset_references,
        list_project_portfolio_assets,
        list_project_decisions,
        list_project_notes,
        list_project_versions,
        list_research_projects,
        restore_research_report,
        save_report_draft,
        ResearchProjectType,
        analyze_strategy_file,
        generate_strategy_candidate,
        run_generated_strategy_backtest,
        save_strategy_analysis,
        run_instrument_research,
        update_project_portfolio_assets,
        update_research_project,
    )
    from qteasy_research.desktop.report_view import report_chart_paths, show_report_with_charts

    settings = load_settings()
    store_dir = Path(settings.get("store_dir") or (Path(__file__).resolve().parents[2] / "research_store"))

    class Worker(QThread):
        stage_changed = Signal(str, str, str)
        completed = Signal(object)
        failed = Signal(str)

        def __init__(self, code: str, project_id: str, horizon: str, policy: str, data_mode: str, evidence_research: bool, indicator_config: dict, parent=None):
            super().__init__(parent)
            self.code = code
            self.project_id = project_id
            self.horizon = horizon
            self.policy = policy
            self.data_mode = data_mode
            self.evidence_research = evidence_research
            self.indicator_config = indicator_config

        def run(self):
            try:
                result = run_instrument_research(
                    self.code,
                    horizon=self.horizon,
                    llm_provider=None,
                    network_research=self.data_mode != "local",
                    data_mode=self.data_mode,
                    evidence_research=self.evidence_research,
                    indicator_config=self.indicator_config,
                    output_dir=str(store_dir),
                    project_id=self.project_id,
                    update_policy=self.policy,
                    progress_callback=lambda stage: self.stage_changed.emit(stage.name, stage.status, stage.message),
                )
                self.completed.emit(result)
            except Exception as exc:
                self.failed.emit(f"{type(exc).__name__}: {exc}")

    class StrategyBacktestWorker(QThread):
        completed = Signal(object)
        failed = Signal(str)

        def __init__(self, manifest_path: str, asset_pool: list[str], parent=None):
            super().__init__(parent)
            self.manifest_path = manifest_path
            self.asset_pool = asset_pool

        def run(self):
            try:
                result = run_generated_strategy_backtest(
                    self.manifest_path,
                    asset_pool=self.asset_pool,
                    output_dir=str(store_dir),
                )
                self.completed.emit(result)
            except Exception as exc:
                self.failed.emit(f"{type(exc).__name__}: {exc}")

    class MainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("ETF Research Desk · 投前研究工作台")
            self.resize(1280, 820)
            self.worker = None
            self.strategy_worker = None
            self.current_project_id = None
            self.projects = []
            self.strategy_analysis = None
            self.strategy_manifest = None

            root = QWidget()
            root_layout = QHBoxLayout(root)
            root_layout.setContentsMargins(0, 0, 0, 0)
            nav = QListWidget()
            nav.setObjectName("main-navigation")
            nav.addItems(["研究项目", "新建研究", "因子研究", "数据管理", "全球ETF宏观", "组合分析", "策略导入", "系统设置"])
            nav.setFixedWidth(190)
            self.pages = QStackedWidget()
            root_layout.addWidget(nav)
            root_layout.addWidget(self.pages, 1)
            self.setCentralWidget(root)
            nav.currentRowChanged.connect(self.pages.setCurrentIndex)

            # 一键发包（B→A 流水线：从 D 中台刷新行情 + 校验 + 发包写 systemB_ref）
            self.publish_toolbar = QToolBar("发布", self)
            self.publish_toolbar.setMovable(False)
            self.publish_button = QPushButton("一键发包")
            self.publish_button.setToolTip("从 D 中台刷新行情，校验通过后调用 run_reference_pipeline.py --real 发包")
            self.publish_button.clicked.connect(self._handle_publish_click)
            self.publish_toolbar.addWidget(self.publish_button)
            self.addToolBar(self.publish_toolbar)
            self._publish_process = None

            self.project_page = self._build_project_page()
            self.new_page = self._build_new_page()
            self.factor_page = FactorResearchPage(store_dir, self)
            self.factor_page.status_message.connect(lambda message: self.statusBar().showMessage(message))
            self.factor_page.diagnostic_action.connect(self._handle_factor_diagnostic_action)
            self.data_page = DataManagementPage(
                store_dir,
                initial_mode=settings.get("data_manager_mode", "direct"),
                parent=self,
            )
            self.data_page.status_message.connect(lambda message: self.statusBar().showMessage(message))
            self.global_etf_page = GlobalEtfPage(store_dir, parent=self)
            self.global_etf_page.status_message.connect(lambda message: self.statusBar().showMessage(message))
            self.portfolio_page = PortfolioPage(store_dir, parent=self)
            self.portfolio_page.status_message.connect(lambda message: self.statusBar().showMessage(message))
            self.strategy_page = self._build_strategy_page()
            self.settings_page = self._build_settings_page()
            for page in (self.project_page, self.new_page, self.factor_page, self.data_page, self.global_etf_page, self.portfolio_page, self.strategy_page, self.settings_page):
                self.pages.addWidget(page)
            nav.setCurrentRow(0)

            self.setStyleSheet("""
                QMainWindow, QWidget { background: #f4f1ea; color: #1d2b2b; }
                QListWidget { background: #172525; color: #dfe9df; border: 0; padding: 18px 8px; font-size: 15px; }
                QListWidget::item { padding: 13px 12px; margin: 3px 0; }
                QListWidget::item:selected { background: #b68a3c; color: #172525; }
                QGroupBox { border: 1px solid #d5d0c4; margin-top: 12px; padding: 12px; font-weight: 600; }
                QLineEdit, QPlainTextEdit, QComboBox, QTextBrowser { background: #fffdf8; border: 1px solid #cfc8ba; padding: 7px; }
                QPushButton { background: #1e5b55; color: white; border: 0; padding: 9px 16px; }
                QPushButton:hover { background: #28756d; }
                QProgressBar { border: 1px solid #cfc8ba; background: #fffdf8; text-align: center; }
                QProgressBar::chunk { background: #b68a3c; }
                QLabel#factorTitle { color: #172525; font-size: 22px; font-weight: 700; }
                QLabel#factorDetailTitle { color: #1e5b55; font-size: 18px; font-weight: 700; }
                QLabel#dataTitle { color: #172525; font-size: 22px; font-weight: 700; }
            """)
            self.refresh_projects()

        def _handle_publish_click(self):
            """一键发包：确认 → QProcess 跑 refresh_fund_daily.py --publish（刷新D行情+校验+发包）。"""
            if self._publish_process is not None and self._publish_process.state() != QProcess.ProcessState.NotRunning:
                return
            ret = QMessageBox.question(
                self,
                "一键发包",
                "确认发包？\n将从 D 中台刷新行情，校验通过后调用 run_reference_pipeline.py --real 写入 systemB_ref 共享目录（B→A 流水线）。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return

            # 日志对话框（非模态，可边跑边看其他页面）
            dlg = QDialog(self)
            dlg.setWindowTitle("一键发包日志")
            dlg.resize(780, 460)
            layout = QVBoxLayout(dlg)
            log = QTextEdit()
            log.setReadOnly(True)
            log.setPlaceholderText("正在启动刷新+发包…")
            layout.addWidget(log)
            close_button = QPushButton("关闭")
            close_button.setEnabled(False)
            layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)
            close_button.clicked.connect(dlg.close)
            dlg.setModal(False)
            dlg.show()
            self._publish_dialog = dlg
            self._publish_log = log
            self._publish_close = close_button

            self.publish_button.setEnabled(False)
            self.publish_button.setText("发包中…")

            project_root = str(Path(__file__).resolve().parents[2])
            proc = QProcess(self)
            proc.setWorkingDirectory(project_root)
            proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            proc.readyReadStandardOutput.connect(lambda: self._append_publish_output(proc))
            proc.finished.connect(lambda code, status: self._publish_finished(code))
            proc.start(sys.executable, ["-X", "utf8", "scripts/refresh_fund_daily.py", "--publish"])
            self._publish_process = proc

        def _append_publish_output(self, proc: QProcess):
            """实时回显子进程 stdout/stderr（MergedChannels 合一）。"""
            data = bytes(proc.readAllStandardOutput())
            text = data.decode("utf-8", errors="replace")
            if text.strip():
                self._publish_log.append(text.rstrip())

        def _publish_finished(self, code: int):
            """子进程结束：恢复按钮、弹结果提示。"""
            self.publish_button.setEnabled(True)
            self.publish_button.setText("一键发包")
            self._publish_close.setEnabled(True)
            tail = self._publish_log.toPlainText()[-400:]
            if code == 0:
                self._publish_log.append("—— 发包完成（退出码 0）")
                QMessageBox.information(self, "一键发包", "发包成功完成，已写入 systemB_ref 共享目录。\n\n尾部日志：\n" + tail)
            else:
                self._publish_log.append(f"—— 发包失败（退出码 {code}）")
                QMessageBox.warning(self, "一键发包", f"发包失败（退出码 {code}）。\n常见原因：D 中台(8765)未启动 / 行情校验未通过。\n\n尾部日志：\n{tail}")

        def _build_project_page(self):
            page = QWidget()
            layout = QVBoxLayout(page)
            title = QLabel("研究项目")
            title.setFont(QFont("Microsoft YaHei UI", 20, QFont.Weight.Bold))
            layout.addWidget(title)
            splitter = QSplitter(Qt.Orientation.Horizontal)
            self.project_type_filter = QComboBox()
            self.project_type_filter.addItems(["全部项目", "资产档案", "策略/组合"])
            self.project_type_filter.currentIndexChanged.connect(self.refresh_projects)
            self.project_list = QListWidget()
            self.project_list.currentRowChanged.connect(self.select_project)
            project_left = QWidget()
            project_left_layout = QVBoxLayout(project_left)
            project_left_layout.addWidget(self.project_type_filter)
            project_left_layout.addWidget(self.project_list, 1)
            splitter.addWidget(project_left)

            detail = QWidget()
            detail_layout = QVBoxLayout(detail)
            self.project_title = QLabel("请选择一个项目")
            self.project_title.setFont(QFont("Microsoft YaHei UI", 18, QFont.Weight.Bold))
            self.project_info = QLabel("暂无项目")
            self.project_report = QTextBrowser()
            self.project_report.setPlaceholderText("选择项目后查看最近一次研究版本")
            actions = QHBoxLayout()
            self.run_button = QPushButton("生成新版本")
            self.refresh_button = QPushButton("刷新项目")
            self.run_button.clicked.connect(self.run_selected_project)
            self.refresh_button.clicked.connect(self.refresh_projects)
            actions.addWidget(self.run_button)
            actions.addWidget(self.refresh_button)
            detail_layout.addWidget(self.project_title)
            detail_layout.addWidget(self.project_info)
            reference_row = QHBoxLayout()
            self.reference_input = QLineEdit()
            self.reference_input.setPlaceholderText("策略项目中输入资产档案代码，例如 518880.SH")
            reference_button = QPushButton("关联资产档案")
            reference_button.clicked.connect(self.link_asset_reference)
            reference_row.addWidget(self.reference_input, 1)
            reference_row.addWidget(reference_button)
            detail_layout.addLayout(reference_row)
            self.portfolio_assets_edit = QPlainTextEdit()
            self.portfolio_assets_edit.setFixedHeight(120)
            self.portfolio_assets_edit.setPlaceholderText(
                "组合项目每行一个标的：代码 | 目标比例 | 买入条件 | 卖出条件\n"
                "例如：518880.SH | 0.30 | 黄金趋势向上 | 跌破 MA120"
            )
            self.portfolio_assets_save = QPushButton("保存组合标的与触发条件")
            self.portfolio_assets_save.clicked.connect(self.save_project_portfolio)
            detail_layout.addWidget(QLabel("组合标的与独立触发条件"))
            detail_layout.addWidget(self.portfolio_assets_edit)
            detail_layout.addWidget(self.portfolio_assets_save)
            detail_layout.addLayout(actions)

            tabs = QTabWidget()
            self.asset_profile_tabs = QTabWidget()
            self.profile_basic_view = QTextBrowser()
            self.profile_dynamic_view = QTextBrowser()
            self.profile_factor_view = QTextBrowser()
            self.profile_portfolio_view = QTextBrowser()
            self.profile_history_view = QTextBrowser()
            self.profile_history_selector = QComboBox()
            self.profile_history_show_deleted = False
            self.asset_deleted_report_versions = []
            self.current_asset_report = None
            self.profile_history_selector.currentIndexChanged.connect(self.show_asset_report_version)
            self.profile_report_mode_button = QPushButton("查看回收站")
            self.profile_report_mode_button.clicked.connect(self.toggle_deleted_report_view)
            self.profile_delete_button = QPushButton("删除报告")
            self.profile_delete_button.clicked.connect(self.delete_selected_report)
            self.profile_restore_button = QPushButton("恢复报告")
            self.profile_restore_button.clicked.connect(self.restore_selected_report)
            self.profile_load_draft_button = QPushButton("加载草稿")
            self.profile_load_draft_button.clicked.connect(self.load_report_draft)
            self.profile_save_draft_button = QPushButton("保存草稿")
            self.profile_save_draft_button.clicked.connect(self.save_current_report_draft)
            self.profile_discard_draft_button = QPushButton("丢弃草稿")
            self.profile_discard_draft_button.clicked.connect(self.discard_current_report_draft)
            self.profile_export_md_button = QPushButton("导出 Markdown")
            self.profile_export_md_button.clicked.connect(lambda: self.export_current_report("md"))
            self.profile_export_html_button = QPushButton("导出 HTML")
            self.profile_export_html_button.clicked.connect(lambda: self.export_current_report("html"))
            self.profile_export_pdf_button = QPushButton("导出 PDF")
            self.profile_export_pdf_button.clicked.connect(lambda: self.export_current_report("pdf"))
            self.profile_export_bundle_button = QPushButton("导出完整报告包")
            self.profile_export_bundle_button.clicked.connect(lambda: self.export_current_report("bundle"))
            report_actions = QHBoxLayout()
            for button in (
                self.profile_report_mode_button,
                self.profile_delete_button,
                self.profile_restore_button,
                self.profile_load_draft_button,
                self.profile_save_draft_button,
                self.profile_discard_draft_button,
                self.profile_export_md_button,
                self.profile_export_html_button,
                self.profile_export_pdf_button,
                self.profile_export_bundle_button,
            ):
                report_actions.addWidget(button)
            report_actions.addStretch(1)
            history_tab = QWidget()
            history_layout = QVBoxLayout(history_tab)
            history_layout.addWidget(self.profile_history_selector)
            history_layout.addLayout(report_actions)
            history_layout.addWidget(self.profile_history_view, 1)
            draft_box = QGroupBox("人工研究补充（草稿）")
            draft_form = QFormLayout(draft_box)
            self.report_draft_summary = QPlainTextEdit()
            self.report_draft_summary.setFixedHeight(50)
            self.report_draft_conclusion = QPlainTextEdit()
            self.report_draft_conclusion.setFixedHeight(50)
            self.report_draft_risk = QPlainTextEdit()
            self.report_draft_risk.setFixedHeight(50)
            self.report_draft_falsification = QPlainTextEdit()
            self.report_draft_falsification.setFixedHeight(50)
            self.report_draft_followup = QPlainTextEdit()
            self.report_draft_followup.setFixedHeight(50)
            draft_form.addRow("人工摘要", self.report_draft_summary)
            draft_form.addRow("人工确认结论", self.report_draft_conclusion)
            draft_form.addRow("风险判断", self.report_draft_risk)
            draft_form.addRow("证伪/退出条件", self.report_draft_falsification)
            draft_form.addRow("后续研究计划", self.report_draft_followup)
            history_layout.addWidget(draft_box)
            self.asset_profile_tabs.addTab(self.profile_basic_view, "基本信息")
            self.asset_profile_tabs.addTab(self.profile_dynamic_view, "动态数据")
            self.asset_profile_tabs.addTab(self.profile_factor_view, "因子分析")
            self.asset_profile_tabs.addTab(history_tab, "研究报告")
            self.asset_profile_tabs.addTab(self.profile_portfolio_view, "组合使用情况")
            tabs.addTab(self.asset_profile_tabs, "资产档案")
            notes_tab, self.notes_edit = self._text_collection_tab("研究笔记", self.add_note)
            decisions_tab, self.decisions_edit = self._text_collection_tab("人工确认结论", self.add_decision)
            tabs.addTab(notes_tab, "笔记")
            tabs.addTab(decisions_tab, "人工结论")
            tabs.addTab(self.project_report, "最近报告")
            detail_layout.addWidget(tabs, 1)
            splitter.addWidget(detail)
            splitter.setSizes([280, 850])
            layout.addWidget(splitter, 1)
            return page

        def _text_collection_tab(self, label, callback):
            tab = QWidget()
            layout = QVBoxLayout(tab)
            edit = QPlainTextEdit()
            edit.setPlaceholderText(f"在此添加{label}，保存后会留下项目修订记录")
            button = QPushButton(f"保存{label}")
            button.clicked.connect(lambda: callback(edit.toPlainText()))
            layout.addWidget(edit)
            layout.addWidget(button)
            return tab, edit

        def _build_new_page(self):
            page = QWidget()
            layout = QVBoxLayout(page)
            title = QLabel("新建研究项目")
            title.setFont(QFont("Microsoft YaHei UI", 20, QFont.Weight.Bold))
            layout.addWidget(title)
            form_box = QGroupBox("项目基本信息")
            form = QFormLayout(form_box)
            self.name_input = QLineEdit()
            self.project_type_input = QComboBox()
            self.project_type_input.addItem("一级：单标的资产档案", ResearchProjectType.ASSET_PROFILE.value)
            self.project_type_input.addItem("二级：策略/组合项目", ResearchProjectType.STRATEGY_PORTFOLIO.value)
            self.project_type_input.currentIndexChanged.connect(self.project_type_changed)
            self.code_input = QLineEdit("518880.SH")
            self.strategy_input = QLineEdit()
            self.strategy_input.setPlaceholderText("可选：先记录策略名称，不会强制套用固定策略")
            self.objective_input = QPlainTextEdit()
            self.objective_input.setFixedHeight(90)
            self.horizon_input = QComboBox()
            self.horizon_input.addItems(["short", "medium", "long"])
            self.portfolio_assets_input = QPlainTextEdit()
            self.portfolio_assets_input.setFixedHeight(120)
            self.portfolio_assets_input.setPlaceholderText(
                "策略/组合项目可填写多行：代码 | 比例 | 期限 | 角色 | 买入 | 卖出 | 止盈 | 止损 | 研究假设\n"
                "例如：518880.SH | 30% | medium | 防御 | 站上 MA120 | 跌破 MA120 | 达到目标收益 | 回撤超过阈值 | 黄金用于分散组合风险\n"
                "不填写时可先创建空白组合项目，之后在项目页继续编辑。"
            )
            form.addRow("项目层级", self.project_type_input)
            form.addRow("标的代码", self.code_input)
            form.addRow("策略名称", self.strategy_input)
            form.addRow("项目名称", self.name_input)
            form.addRow("研究目标", self.objective_input)
            form.addRow("研究期限", self.horizon_input)
            layout.addWidget(form_box)
            self.portfolio_assets_box = QGroupBox("组合配置（仅保存配置，不自动套用固定回测策略）")
            portfolio_layout = QVBoxLayout(self.portfolio_assets_box)
            portfolio_layout.addWidget(self.portfolio_assets_input)
            layout.addWidget(self.portfolio_assets_box)
            create_button = QPushButton("创建项目")
            create_button.clicked.connect(self.create_project)
            layout.addWidget(create_button)
            layout.addStretch(1)
            self.project_type_changed()
            return page

        def _build_strategy_page(self):
            page = QWidget()
            layout = QVBoxLayout(page)
            title = QLabel("策略代码导入")
            title.setFont(QFont("Microsoft YaHei UI", 20, QFont.Weight.Bold))
            layout.addWidget(title)
            intro = QLabel(
                "支持 TXT 和 Jupyter Notebook。解析阶段只读源码；生成的是候选策略模板，"
                "必须人工核对后才能运行回测。原始源码不会自动执行。"
            )
            intro.setWordWrap(True)
            layout.addWidget(intro)

            source_row = QHBoxLayout()
            self.strategy_source_input = QLineEdit()
            self.strategy_source_input.setPlaceholderText("选择 .txt 或 .ipynb 文件")
            source_row.addWidget(self.strategy_source_input, 1)
            choose = QPushButton("选择文件")
            choose.clicked.connect(self.choose_strategy_file)
            source_row.addWidget(choose)
            analyze = QPushButton("解析策略")
            analyze.clicked.connect(self.analyze_strategy)
            source_row.addWidget(analyze)
            layout.addLayout(source_row)

            self.strategy_analysis_view = QTextBrowser()
            self.strategy_analysis_view.setPlaceholderText("解析结果会显示在这里")
            layout.addWidget(self.strategy_analysis_view, 1)

            candidate_box = QGroupBox("候选回测代码")
            candidate_layout = QVBoxLayout(candidate_box)
            self.strategy_code_view = QPlainTextEdit()
            self.strategy_code_view.setReadOnly(True)
            self.strategy_code_view.setPlaceholderText("解析后生成候选代码")
            candidate_layout.addWidget(self.strategy_code_view, 1)
            candidate_actions = QHBoxLayout()
            generate = QPushButton("生成候选代码")
            generate.clicked.connect(self.generate_strategy)
            candidate_actions.addWidget(generate)
            candidate_actions.addWidget(QLabel("回测资产池："))
            self.strategy_asset_pool_input = QLineEdit("518880.SH,159941.SZ,513050.SH")
            candidate_actions.addWidget(self.strategy_asset_pool_input, 1)
            run = QPushButton("确认并运行候选回测")
            run.clicked.connect(self.run_imported_backtest)
            candidate_actions.addWidget(run)
            candidate_layout.addLayout(candidate_actions)
            layout.addWidget(candidate_box, 1)
            return page

        def _build_settings_page(self):
            page = QWidget()
            layout = QVBoxLayout(page)
            title = QLabel("系统设置")
            title.setFont(QFont("Microsoft YaHei UI", 20, QFont.Weight.Bold))
            layout.addWidget(title)
            box = QGroupBox("本地存储")
            form = QFormLayout(box)
            self.store_input = QLineEdit(str(store_dir))
            browse = QPushButton("选择目录")
            browse.clicked.connect(self.choose_store_dir)
            line = QHBoxLayout()
            line.addWidget(self.store_input)
            line.addWidget(browse)
            form.addRow("研究数据目录", line)
            self.data_mode_input = QComboBox()
            self.data_mode_input.addItem("直连优先：AKShare → Tushare → 本地", "direct")
            self.data_mode_input.addItem("本地优先：本地 → AKShare → Tushare", "hybrid")
            self.data_mode_input.addItem("仅本地离线：CSV / 研究快照", "local")
            saved_mode = settings.get("data_mode", "direct")
            mode_index = self.data_mode_input.findData(saved_mode)
            self.data_mode_input.setCurrentIndex(max(0, mode_index))
            form.addRow("数据模式", self.data_mode_input)
            self.evidence_input = QCheckBox("启用联网定性证据补研")
            self.evidence_input.setChecked(bool(settings.get("evidence_research", True)))
            form.addRow("研究证据", self.evidence_input)
            indicator_config = settings.get("indicator_config", {})
            self.macd_fast_input = QSpinBox()
            self.macd_fast_input.setRange(2, 100)
            self.macd_fast_input.setValue(int(indicator_config.get("macd_fast", 12)))
            self.macd_slow_input = QSpinBox()
            self.macd_slow_input.setRange(3, 200)
            self.macd_slow_input.setValue(int(indicator_config.get("macd_slow", 26)))
            self.macd_signal_input = QSpinBox()
            self.macd_signal_input.setRange(2, 100)
            self.macd_signal_input.setValue(int(indicator_config.get("macd_signal", 9)))
            self.rsi_period_input = QSpinBox()
            self.rsi_period_input.setRange(2, 100)
            self.rsi_period_input.setValue(int(indicator_config.get("rsi_period", 14)))
            self.atr_period_input = QSpinBox()
            self.atr_period_input.setRange(2, 100)
            self.atr_period_input.setValue(int(indicator_config.get("atr_period", 14)))
            self.adx_period_input = QSpinBox()
            self.adx_period_input.setRange(2, 100)
            self.adx_period_input.setValue(int(indicator_config.get("adx_period", 14)))
            self.bollinger_period_input = QSpinBox()
            self.bollinger_period_input.setRange(2, 200)
            self.bollinger_period_input.setValue(int(indicator_config.get("bollinger_period", 20)))
            self.bollinger_std_input = QDoubleSpinBox()
            self.bollinger_std_input.setRange(0.1, 5.0)
            self.bollinger_std_input.setSingleStep(0.1)
            self.bollinger_std_input.setValue(float(indicator_config.get("bollinger_std", 2.0)))
            form.addRow("MACD快线", self.macd_fast_input)
            form.addRow("MACD慢线", self.macd_slow_input)
            form.addRow("MACD信号线", self.macd_signal_input)
            form.addRow("RSI周期", self.rsi_period_input)
            form.addRow("ATR周期", self.atr_period_input)
            form.addRow("ADX周期", self.adx_period_input)
            form.addRow("Bollinger周期", self.bollinger_period_input)
            form.addRow("Bollinger倍数", self.bollinger_std_input)
            self.source_status_label = QLabel("当前顺序：AKShare → Tushare → 本地 CSV / 研究快照")
            form.addRow("数据源状态", self.source_status_label)
            check_sources = QPushButton("检查连接")
            check_sources.clicked.connect(self.check_data_sources)
            self.data_mode_input.currentIndexChanged.connect(self.update_data_mode_label)
            self.update_data_mode_label()
            form.addRow("连接检查", check_sources)
            layout.addWidget(box)
            save = QPushButton("保存设置")
            save.clicked.connect(self.save_settings)
            layout.addWidget(save)
            layout.addStretch(1)
            return page

        def refresh_projects(self):
            all_projects = list_research_projects(output_dir=store_dir)
            filter_index = self.project_type_filter.currentIndex()
            type_map = {1: ResearchProjectType.ASSET_PROFILE.value, 2: ResearchProjectType.STRATEGY_PORTFOLIO.value}
            selected_type = type_map.get(filter_index)
            self.projects = [item for item in all_projects if not selected_type or item.project_type == selected_type]
            self.project_list.clear()
            for project in self.projects:
                label = "资产档案" if project.project_type == ResearchProjectType.ASSET_PROFILE.value else "策略/组合"
                self.project_list.addItem(f"[{label}] {project.name}  ·  {project.status}")

        def project_type_changed(self):
            is_strategy = self.project_type_input.currentData() == ResearchProjectType.STRATEGY_PORTFOLIO.value
            # 组合不是单一代码，但仍允许先填写首个标的，之后可在下方扩展多标的配置。
            self.code_input.setEnabled(True)
            self.strategy_input.setEnabled(is_strategy)
            if is_strategy:
                self.code_input.setPlaceholderText("可选：首个标的代码；完整组合请填写下方清单")
                if self.code_input.text().strip().upper() == "PORTFOLIO":
                    self.code_input.clear()
            else:
                self.code_input.setPlaceholderText("必填，例如 518880.SH")
            self.portfolio_assets_box.setVisible(is_strategy)
            if not is_strategy and not self.code_input.text().strip():
                self.code_input.setText("518880.SH")

        @staticmethod
        def _parse_portfolio_text(text: str) -> list[dict]:
            assets = []
            for line_no, raw_line in enumerate(text.splitlines(), start=1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                fields = [item.strip() for item in line.split("|")]
                if len(fields) < 2:
                    raise ValueError(f"第 {line_no} 行格式错误，应为：代码 | 比例 | 买入条件 | 卖出条件")
                try:
                    weight_text = fields[1].rstrip("%").strip()
                    weight = float(weight_text)
                    if fields[1].endswith("%"):
                        weight /= 100
                except ValueError as exc:
                    raise ValueError(f"第 {line_no} 行比例无效：{fields[1]}") from exc
                assets.append({
                    "code": fields[0],
                    "weight": weight,
                    # 兼容旧格式：代码 | 比例 | 买入条件 | 卖出条件 | 角色
                    "horizon": fields[2] if len(fields) > 5 and fields[2] else "medium",
                    "role": fields[3] if len(fields) > 5 and fields[3] else (fields[4] if len(fields) > 4 else "candidate"),
                    "buy_condition": fields[4] if len(fields) > 5 else (fields[2] if len(fields) > 2 else ""),
                    "sell_condition": fields[5] if len(fields) > 5 else (fields[3] if len(fields) > 3 else ""),
                    "take_profit_condition": fields[6] if len(fields) > 6 else "",
                    "stop_loss_condition": fields[7] if len(fields) > 7 else "",
                    "hypothesis": fields[8] if len(fields) > 8 else "",
                })
            return assets

        @staticmethod
        def _portfolio_text(assets: list[dict]) -> str:
            return "\n".join(
                f"{item.get('code', '')} | {float(item.get('weight', 0)):.2%} | "
                f"{item.get('horizon', 'medium')} | {item.get('role', 'candidate')} | "
                f"{item.get('buy_condition', '')} | {item.get('sell_condition', '')} | "
                f"{item.get('take_profit_condition', '')} | {item.get('stop_loss_condition', '')} | "
                f"{item.get('hypothesis', '')}"
                for item in assets
            )

        def select_project(self, index):
            if index < 0 or index >= len(self.projects):
                self.current_project_id = None
                return
            project = self.projects[index]
            self.current_project_id = project.project_id
            references = list_asset_references(project.project_id, output_dir=store_dir)
            reference_text = ""
            if references:
                reference_text = "\n关联资产：" + ", ".join(item.asset_code for item in references)
            type_label = "资产档案" if project.project_type == ResearchProjectType.ASSET_PROFILE.value else "策略/组合"
            self.run_button.setEnabled(project.project_type == ResearchProjectType.ASSET_PROFILE.value)
            self.reference_input.setEnabled(project.project_type == ResearchProjectType.STRATEGY_PORTFOLIO.value)
            is_strategy = project.project_type == ResearchProjectType.STRATEGY_PORTFOLIO.value
            self.asset_profile_tabs.setEnabled(not is_strategy)
            self.portfolio_assets_edit.setEnabled(is_strategy)
            self.portfolio_assets_save.setEnabled(is_strategy)
            self.portfolio_assets_edit.setPlainText(
                self._portfolio_text(list_project_portfolio_assets(project.project_id, output_dir=store_dir))
                if is_strategy else ""
            )
            self.project_title.setText(project.name)
            self.project_info.setText(f"{type_label} · {project.code} · {project.status}\n{project.objective or '未填写研究目标'}{reference_text}")
            versions = list_project_versions(project.project_id, output_dir=store_dir)
            if versions:
                latest = versions[0]
                self._show_report(self.project_report, latest)
            else:
                self.project_report.setMarkdown("项目尚未生成研究版本。")
            self.refresh_asset_profile_views(project)

        @staticmethod
        def _profile_markdown(profile) -> str:
            if profile is None:
                return "暂无资产档案。"
            rows = [
                ("名称", profile.name or "未知"),
                ("代码", profile.code),
                ("类型", profile.asset_type),
                ("市场", profile.exchange or "未知"),
                ("跟踪基准", profile.benchmark or "未知"),
                ("档案版本", profile.profile_version),
                ("资料来源", profile.source or "未知"),
                ("资料截至", profile.as_of or "未知"),
            ]
            rows.extend(metadata_display_rows(profile.fixed_metadata))
            lines = ["## 固定资产档案", "", "| 项目 | 内容 |", "|---|---|"]
            lines.extend(f"| {key} | {str(value).replace('|', '\\|')} |" for key, value in rows)
            lines.extend(["", "固定信息不会因每日行情更新而覆盖；如资料发生变化，将生成新的档案版本。"])
            return "\n".join(lines)

        @staticmethod
        def _dynamic_markdown(dynamic) -> str:
            if dynamic is None:
                return "尚未生成动态研究数据，请先生成一个研究版本。"
            metrics = dynamic.quantitative_metrics or {}
            windows = metrics.get("windows", {})
            lines = [
                f"## 动态数据（截至 {dynamic.as_of or '未知'}）",
                "",
                f"研究版本：`{dynamic.run_id}`",
                "",
                "| 期限 | 区间收益 | 年化收益 | 年化波动 | 最大回撤 | 夏普 |",
                "|---|---:|---:|---:|---:|---:|",
            ]
            labels = {"1m": "近1个月", "3m": "近3个月", "6m": "近6个月", "1y": "近1年", "3y": "近3年"}
            for key, label in labels.items():
                item = windows.get(key, {})
                fmt = lambda value: "不可用" if value is None else f"{float(value):.2%}"
                ratio = lambda value: "不可用" if value is None else f"{float(value):.2f}"
                lines.append(f"| {label} | {fmt(item.get('total_return'))} | {fmt(item.get('annual_return'))} | {fmt(item.get('annual_volatility'))} | {fmt(item.get('max_drawdown'))} | {ratio(item.get('sharpe'))} |")
            lines.extend([
                "",
                f"VaR95：{fmt(metrics.get('var95'))}；ES95：{fmt(metrics.get('es95'))}；最新收盘价：{metrics.get('latest_close', '不可用')}",
                "",
                "数据快照和技术指标随研究版本保存，历史版本不会被新数据覆盖。",
            ])
            return "\n".join(lines)

        @staticmethod
        def _factor_markdown(dynamic) -> str:
            if dynamic is None:
                return "尚未生成因子分析。"
            factor_analysis = dynamic.quantitative_metrics.get("factor_analysis", {})
            if not factor_analysis:
                return "当前研究版本没有因子结果，请重新生成研究版本。"
            lines = ["## 分期限因子分析", "", "因子结果用于研究确认，不直接生成交易指令；当前版本不包含 KDJ。"]
            labels = {"short": "短线", "medium": "中线", "long": "长线参考"}
            for horizon, label in labels.items():
                item = factor_analysis.get(horizon, {})
                if not item:
                    continue
                lines.extend([
                    "",
                    f"### {label}",
                    "",
                    f"综合评分：{item.get('composite_score') if item.get('composite_score') is not None else '不可用'} / 100；状态：{item.get('state', '不可用')}；一致性：{item.get('consistency', '不可用')}。",
                    "",
                    "| 因子 | 值 | 方向 | 评分 | 置信度 |",
                    "|---|---:|---|---:|---|",
                ])
                for factor in item.get("factors", []):
                    value = factor.get("value")
                    display = "不可用" if value is None else f"{float(value):.4f}"
                    if any(token in str(factor.get("name", "")) for token in ("动量", "波动", "回撤", "ATR", "Bollinger")) and value is not None:
                        display = f"{float(value):.2%}"
                    lines.append(f"| {factor.get('name', '')} | {display} | {factor.get('direction', '不可用')} | {factor.get('score', '不可用')} | {factor.get('confidence', '不可用')} |")
            return "\n".join(lines)

        def refresh_asset_profile_views(self, project):
            if project.project_type == ResearchProjectType.STRATEGY_PORTFOLIO.value:
                message = "策略/组合项目使用资产档案引用；请在组合配置中查看各标的上下文。"
                self.profile_basic_view.setMarkdown(message)
                self.profile_dynamic_view.setMarkdown(message)
                self.profile_factor_view.setMarkdown(message)
                self.profile_portfolio_view.setMarkdown(message)
                self.profile_history_selector.blockSignals(True)
                self.profile_history_selector.clear()
                self.profile_history_selector.blockSignals(False)
                self.profile_history_view.setMarkdown(message)
                self.asset_report_versions = []
                self.asset_deleted_report_versions = []
                self.current_asset_report = None
                self._refresh_report_history_selector()
                return
            try:
                profile = get_asset_profile(project.code, output_dir=store_dir)
                dynamic = get_asset_dynamic_snapshot(project.code, output_dir=store_dir)
                contexts = list_portfolio_asset_contexts(code=project.code, output_dir=store_dir)
                self.profile_basic_view.setMarkdown(self._profile_markdown(profile))
                self.profile_dynamic_view.setMarkdown(self._dynamic_markdown(dynamic))
                self.profile_factor_view.setMarkdown(self._factor_markdown(dynamic))
                if contexts:
                    lines = ["## 组合使用情况", "", "| 组合项目 | 期限 | 比例 | 角色 | 买入条件 | 卖出条件 | 状态 |", "|---|---|---:|---|---|---|---|"]
                    for context in contexts:
                        project_ref = get_research_project(context.project_id, output_dir=store_dir)
                        lines.append(f"| {project_ref.name} | {context.horizon} | {context.weight:.2%} | {context.role} | {context.buy_condition or '—'} | {context.sell_condition or '—'} | {'启用' if context.enabled else '停用'} |")
                    self.profile_portfolio_view.setMarkdown("\n".join(lines))
                else:
                    self.profile_portfolio_view.setMarkdown("该资产尚未被策略/组合项目引用。")
                self.asset_report_versions = list_asset_reports(project.code, output_dir=store_dir)
                self.asset_deleted_report_versions = list_asset_reports(
                    project.code, include_deleted=True, output_dir=store_dir
                )
                self.asset_deleted_report_versions = [
                    item for item in self.asset_deleted_report_versions if item.deleted_at
                ]
                self.profile_history_show_deleted = False
                self._refresh_report_history_selector()
            except Exception as exc:
                message = f"资产档案加载失败：{type(exc).__name__}: {exc}"
                self.profile_basic_view.setMarkdown(message)
                self.profile_dynamic_view.setMarkdown(message)
                self.profile_factor_view.setMarkdown(message)
                self.profile_portfolio_view.setMarkdown(message)
                self.profile_history_view.setMarkdown(message)

        def _active_history_reports(self):
            return (
                getattr(self, "asset_deleted_report_versions", [])
                if self.profile_history_show_deleted
                else getattr(self, "asset_report_versions", [])
            )

        def _refresh_report_history_selector(self):
            reports = self._active_history_reports()
            self.profile_history_selector.blockSignals(True)
            self.profile_history_selector.clear()
            for report in reports:
                prefix = "已删除 · " if report.deleted_at else ""
                self.profile_history_selector.addItem(
                    f"{prefix}版本 {report.version_no} · {report.data_as_of or '未知'} · {report.run_status}"
                )
            self.profile_history_selector.blockSignals(False)
            self.profile_report_mode_button.setText(
                "退出回收站" if self.profile_history_show_deleted else "查看回收站"
            )
            self.show_asset_report_version(0)

        @staticmethod
        def _report_with_draft(report):
            if report is None:
                return "报告为空"
            body = report.report or "报告为空"
            draft = get_report_draft(report.run_id, output_dir=store_dir) if not report.deleted_at else None
            if not draft:
                return body
            sections = [
                ("人工摘要", draft.manual_summary),
                ("人工确认结论", draft.manual_conclusion),
                ("风险判断", draft.risk_judgment),
                ("证伪/退出条件", draft.falsification_conditions),
                ("后续研究计划", draft.followup_plan),
            ]
            lines = [body, "", "## 人工研究补充（草稿）", ""]
            for title, content in sections:
                if content.strip():
                    lines.extend([f"### {title}", "", content.strip(), ""])
            return "\n".join(lines).rstrip()

        def _show_report(self, browser, report):
            """显示指定版本报告，并只加载该版本自己的图表。"""
            markdown = self._report_with_draft(report)
            expected = len(report_chart_paths(report, existing_only=False))
            loaded = show_report_with_charts(browser, report, markdown)
            if expected and loaded < expected:
                self.statusBar().showMessage(
                    f"报告已显示，但有 {expected - loaded} 张图表不可用"
                )
            return loaded

        def _set_report_draft_fields(self, draft):
            values = {
                "report_draft_summary": draft.manual_summary if draft else "",
                "report_draft_conclusion": draft.manual_conclusion if draft else "",
                "report_draft_risk": draft.risk_judgment if draft else "",
                "report_draft_falsification": draft.falsification_conditions if draft else "",
                "report_draft_followup": draft.followup_plan if draft else "",
            }
            for name, value in values.items():
                getattr(self, name).setPlainText(value)

        def show_asset_report_version(self, index):
            reports = self._active_history_reports()
            if 0 <= index < len(reports):
                report = reports[index]
                self.current_asset_report = report
                self._show_report(self.profile_history_view, report)
                self._set_report_draft_fields(
                    get_report_draft(report.run_id, output_dir=store_dir) if not report.deleted_at else None
                )
                active = not bool(report.deleted_at)
                self.profile_delete_button.setEnabled(active)
                self.profile_restore_button.setEnabled(not active)
                for button in (
                    self.profile_load_draft_button,
                    self.profile_save_draft_button,
                    self.profile_discard_draft_button,
                    self.profile_export_md_button,
                    self.profile_export_html_button,
                    self.profile_export_pdf_button,
                    self.profile_export_bundle_button,
                ):
                    button.setEnabled(active)
            elif not reports:
                self.current_asset_report = None
                self.profile_history_view.setMarkdown("尚未生成历史研究报告。")
                self._set_report_draft_fields(None)
                for button in (
                    self.profile_delete_button,
                    self.profile_restore_button,
                    self.profile_load_draft_button,
                    self.profile_save_draft_button,
                    self.profile_discard_draft_button,
                    self.profile_export_md_button,
                    self.profile_export_html_button,
                    self.profile_export_pdf_button,
                    self.profile_export_bundle_button,
                ):
                    button.setEnabled(False)

        def toggle_deleted_report_view(self):
            self.profile_history_show_deleted = not self.profile_history_show_deleted
            self._refresh_report_history_selector()

        def load_report_draft(self):
            report = self.current_asset_report
            if report and not report.deleted_at:
                self._set_report_draft_fields(get_report_draft(report.run_id, output_dir=store_dir))
                self._show_report(self.profile_history_view, report)

        def save_current_report_draft(self):
            report = self.current_asset_report
            if not report or report.deleted_at:
                return
            save_report_draft(
                report.run_id,
                manual_summary=self.report_draft_summary.toPlainText(),
                manual_conclusion=self.report_draft_conclusion.toPlainText(),
                risk_judgment=self.report_draft_risk.toPlainText(),
                falsification_conditions=self.report_draft_falsification.toPlainText(),
                followup_plan=self.report_draft_followup.toPlainText(),
                output_dir=store_dir,
            )
            self._show_report(self.profile_history_view, report)
            self.statusBar().showMessage("报告人工补充草稿已保存，原始报告未修改")

        def discard_current_report_draft(self):
            report = self.current_asset_report
            if not report or report.deleted_at:
                return
            discard_report_draft(report.run_id, output_dir=store_dir)
            self._set_report_draft_fields(None)
            self._show_report(self.profile_history_view, report)
            self.statusBar().showMessage("报告人工补充草稿已丢弃")

        def export_current_report(self, fmt):
            report = self.current_asset_report
            if not report or report.deleted_at:
                return
            destination = QFileDialog.getExistingDirectory(self, "选择报告导出目录", str(store_dir))
            if not destination:
                return
            try:
                if fmt == "bundle":
                    exported = export_report_bundle(
                        report.run_id,
                        include_draft=True,
                        destination_dir=destination,
                        output_dir=store_dir,
                    )
                else:
                    exported = export_research_report(
                        report.run_id,
                        formats=(fmt,),
                        include_draft=True,
                        destination_dir=destination,
                        output_dir=store_dir,
                    )
                paths = "；".join(exported.paths.values())
                warning = f"；警告：{'；'.join(exported.warnings)}" if exported.warnings else ""
                self.statusBar().showMessage(f"报告导出完成：{paths}{warning}")
            except Exception as exc:
                QMessageBox.critical(self, "报告导出失败", f"{type(exc).__name__}: {exc}")

        def delete_selected_report(self):
            report = self.current_asset_report
            if not report or report.deleted_at:
                return
            confirmation = QMessageBox.question(
                self,
                "删除研究报告",
                f"确定逻辑删除版本 {report.version_no} 吗？原始文件和数据快照会保留。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if confirmation != QMessageBox.StandardButton.Yes:
                return
            delete_research_report(report.run_id, reason="桌面端用户删除", output_dir=store_dir)
            self.refresh_projects()
            if self.current_project_id:
                project = get_research_project(self.current_project_id, output_dir=store_dir)
                self.refresh_asset_profile_views(project)
            self.statusBar().showMessage("研究报告已逻辑删除，可从回收站恢复")

        def restore_selected_report(self):
            report = self.current_asset_report
            if not report or not report.deleted_at:
                return
            restore_research_report(report.run_id, output_dir=store_dir)
            self.refresh_projects()
            if self.current_project_id:
                project = get_research_project(self.current_project_id, output_dir=store_dir)
                self.refresh_asset_profile_views(project)
            self.profile_history_show_deleted = False
            self._refresh_report_history_selector()
            self.statusBar().showMessage("研究报告已恢复")

        def create_project(self):
            try:
                project_type = self.project_type_input.currentData()
                if project_type == ResearchProjectType.STRATEGY_PORTFOLIO.value:
                    project = create_strategy_project(
                        self.name_input.text(),
                        self.strategy_input.text() or "",
                        self.objective_input.toPlainText(),
                        horizon=self.horizon_input.currentText(),
                        output_dir=store_dir,
                    )
                    portfolio_assets = self._parse_portfolio_text(self.portfolio_assets_input.toPlainText())
                    if not portfolio_assets and self.code_input.text().strip():
                        portfolio_assets = [{"code": self.code_input.text().strip(), "weight": 1.0}]
                    if portfolio_assets:
                        update_project_portfolio_assets(
                            project.project_id,
                            portfolio_assets,
                            output_dir=store_dir,
                        )
                else:
                    project = create_research_project(
                        self.name_input.text(),
                        self.code_input.text(),
                        self.objective_input.toPlainText(),
                        project_type=ResearchProjectType.ASSET_PROFILE.value,
                        output_dir=store_dir,
                    )
                update_research_project(project.project_id, output_dir=store_dir, horizon=self.horizon_input.currentText())
                self.refresh_projects()
                QMessageBox.information(self, "项目已创建", f"已创建研究项目：{project.name}")
            except Exception as exc:
                QMessageBox.critical(self, "创建失败", str(exc))

        def save_project_portfolio(self):
            if not self.current_project_id:
                QMessageBox.warning(self, "未选择项目", "请先选择一个策略/组合项目")
                return
            try:
                update_project_portfolio_assets(
                    self.current_project_id,
                    self._parse_portfolio_text(self.portfolio_assets_edit.toPlainText()),
                    output_dir=store_dir,
                )
                self.select_project(self.project_list.currentRow())
                self.statusBar().showMessage("组合标的、比例和触发条件已保存")
            except Exception as exc:
                QMessageBox.critical(self, "保存组合配置失败", str(exc))

        def choose_strategy_file(self):
            selected, _ = QFileDialog.getOpenFileName(
                self,
                "选择策略源码",
                str(Path.home()),
                "策略文件 (*.txt *.ipynb);;文本文件 (*.txt);;Jupyter Notebook (*.ipynb)",
            )
            if selected:
                self.strategy_source_input.setText(selected)

        @staticmethod
        def _analysis_markdown(analysis) -> str:
            family_labels = {
                "equal_weight": "等权配置",
                "momentum": "动量/相对强弱",
                "risk_parity": "风险平价",
                "inverse_vol": "波动率倒数加权",
                "trend_following": "趋势跟随",
                "mean_reversion": "均值回归",
                "custom": "自定义策略",
            }
            lines = [
                f"# {analysis.source_name}",
                "",
                f"**主要思路：** {analysis.summary}",
                "",
                f"**实现方式：** {analysis.implementation_summary}",
                "",
                "| 项目 | 结果 |",
                "|---|---|",
                f"| 策略家族 | {family_labels.get(analysis.strategy_family, analysis.strategy_family)} |",
                f"| 解析置信度 | {analysis.confidence:.0%} |",
                f"| 代码框架 | {analysis.framework} |",
                f"| 数据依赖 | {'、'.join(analysis.data_dependencies) or '未识别'} |",
                f"| 技术指标 | {'、'.join(analysis.indicators) or '未识别'} |",
                f"| 可生成候选回测模板 | {'是' if analysis.runnable_template else '否，需人工补全'} |",
                "",
                "## 识别到的信号线索",
                "",
            ]
            lines.extend(f"- `{line}`" for line in analysis.signal_rules) if analysis.signal_rules else lines.append("未识别到明确买入、卖出或目标仓位规则。")
            if analysis.warnings:
                lines.extend(["", "## 注意事项", ""])
                lines.extend(f"- {warning}" for warning in analysis.warnings)
            return "\n".join(lines)

        def analyze_strategy(self):
            source_path = self.strategy_source_input.text().strip()
            if not source_path:
                QMessageBox.warning(self, "未选择文件", "请选择 .txt 或 .ipynb 策略文件")
                return
            try:
                analysis = analyze_strategy_file(source_path)
                save_strategy_analysis(analysis, store_dir)
                self.strategy_analysis = analysis
                self.strategy_manifest = None
                self.strategy_code_view.clear()
                self.strategy_analysis_view.setMarkdown(self._analysis_markdown(analysis))
                self.statusBar().showMessage(f"策略解析完成：{analysis.strategy_family}")
            except Exception as exc:
                QMessageBox.critical(self, "策略解析失败", str(exc))

        def generate_strategy(self):
            if self.strategy_analysis is None:
                self.analyze_strategy()
            if self.strategy_analysis is None:
                return
            try:
                self.strategy_manifest = generate_strategy_candidate(self.strategy_analysis, store_dir)
                self.strategy_code_view.setPlainText(self.strategy_manifest["code"])
                mode = "可运行模板" if self.strategy_manifest.get("runnable") else "待人工补全骨架"
                self.statusBar().showMessage(f"候选代码已生成：{mode}")
            except Exception as exc:
                QMessageBox.critical(self, "候选代码生成失败", str(exc))

        def run_imported_backtest(self):
            if not self.strategy_manifest:
                QMessageBox.warning(self, "尚未生成候选代码", "请先解析策略并生成候选代码")
                return
            if not self.strategy_manifest.get("runnable"):
                QMessageBox.warning(self, "暂不可回测", "当前策略未识别为可支持模板，请先人工补全候选代码")
                return
            asset_pool = [item.strip().upper() for item in self.strategy_asset_pool_input.text().split(",") if item.strip()]
            if not asset_pool:
                QMessageBox.warning(self, "资产池为空", "请输入至少一个回测资产代码")
                return
            confirmation = QMessageBox.question(
                self,
                "确认运行候选回测",
                "只会运行本系统生成且未被修改的候选代码，不会执行原始上传文件。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if confirmation != QMessageBox.StandardButton.Yes:
                return
            self.strategy_worker = StrategyBacktestWorker(
                self.strategy_manifest["manifest_path"],
                asset_pool,
                self,
            )
            self.strategy_worker.completed.connect(self.strategy_backtest_completed)
            self.strategy_worker.failed.connect(self.strategy_backtest_failed)
            self.strategy_worker.start()
            self.statusBar().showMessage("候选策略回测运行中…")

        def strategy_backtest_completed(self, result):
            if result.success:
                raw = result.raw_result or {}
                lines = ["## 候选回测结果", "", f"策略：{result.strategy_name}", ""]
                for key, label in (("rtn", "总收益率"), ("annual_rtn", "年化收益率"), ("sharp", "夏普比率"), ("mdd", "最大回撤"), ("final_value", "最终价值")):
                    if key not in raw:
                        continue
                    value = raw[key]
                    if key in {"rtn", "annual_rtn", "mdd"} and isinstance(value, (int, float)):
                        display = f"{value:.2%}"
                    elif key == "final_value" and isinstance(value, (int, float)):
                        display = f"{value:,.2f}"
                    else:
                        display = f"{value:.4f}" if isinstance(value, float) else str(value)
                    lines.append(f"- **{label}：** {display}")
                self.strategy_analysis_view.append("\n".join(lines))
                self.statusBar().showMessage("候选策略回测完成；结果仅作为人工复核材料")
            else:
                self.strategy_backtest_failed(result.error or "回测失败")

        def strategy_backtest_failed(self, message):
            self.statusBar().showMessage("候选策略回测失败")
            QMessageBox.critical(self, "候选回测失败", message)

        def link_asset_reference(self):
            if not self.current_project_id or not self.reference_input.text().strip():
                QMessageBox.warning(self, "无法关联", "请先选择策略/组合项目并输入资产档案代码")
                return
            code = self.reference_input.text().strip().upper()
            candidates = [
                item for item in list_research_projects(output_dir=store_dir)
                if item.project_type == ResearchProjectType.ASSET_PROFILE.value
                and item.code.upper().split(".", 1)[0] == code.split(".", 1)[0]
            ]
            if not candidates:
                QMessageBox.warning(self, "未找到资产档案", "请先为该标的创建一级资产档案")
                return
            add_asset_reference(self.current_project_id, candidates[0].project_id, output_dir=store_dir)
            self.reference_input.clear()
            self.select_project(self.project_list.currentRow())
            self.statusBar().showMessage(f"已关联资产档案：{candidates[0].code}")

        def run_selected_project(self):
            if not self.current_project_id:
                QMessageBox.warning(self, "未选择项目", "请先选择一个研究项目")
                return
            project = get_research_project(self.current_project_id, output_dir=store_dir)
            self.run_button.setEnabled(False)
            self.worker = Worker(
                project.code,
                project.project_id,
                project.horizon,
                "refresh",
                self.data_mode_input.currentData(),
                self.evidence_input.isChecked(),
                {
                    "macd_fast": self.macd_fast_input.value(),
                    "macd_slow": self.macd_slow_input.value(),
                    "macd_signal": self.macd_signal_input.value(),
                    "rsi_period": self.rsi_period_input.value(),
                    "atr_period": self.atr_period_input.value(),
                    "adx_period": self.adx_period_input.value(),
                    "bollinger_period": self.bollinger_period_input.value(),
                    "bollinger_std": self.bollinger_std_input.value(),
                },
                self,
            )
            self.worker.stage_changed.connect(lambda name, status, message: self.statusBar().showMessage(f"{name} · {status} · {message}"))
            self.worker.completed.connect(self.research_completed)
            self.worker.failed.connect(self.research_failed)
            self.worker.start()

        def research_completed(self, result):
            self.run_button.setEnabled(True)
            self.statusBar().showMessage(f"研究完成：版本 {result.version_no} · {result.run_status}")
            self.refresh_projects()
            for index, project in enumerate(self.projects):
                if project.project_id == result.project_id:
                    self.project_list.setCurrentRow(index)
                    break

        def research_failed(self, message):
            self.run_button.setEnabled(True)
            QMessageBox.critical(self, "研究失败", message)

        def add_note(self, content):
            if self.current_project_id and content.strip():
                add_project_note(self.current_project_id, content.strip(), output_dir=store_dir)
                self.notes_edit.clear()
                self.statusBar().showMessage("研究笔记已保存")

        def add_decision(self, content):
            if self.current_project_id and content.strip():
                add_project_decision(self.current_project_id, content.strip(), output_dir=store_dir)
                self.decisions_edit.clear()
                self.statusBar().showMessage("人工确认结论已保存")

        def choose_store_dir(self):
            selected = QFileDialog.getExistingDirectory(self, "选择研究数据目录", str(store_dir))
            if selected:
                self.store_input.setText(selected)

        def check_data_sources(self):
            ak_status = "可用" if importlib.util.find_spec("akshare") else "未安装"
            token_status = "已配置" if os.getenv("TUSHARE_TOKEN", "").strip() else "未配置"
            tushare_url = os.getenv("TUSHARE_API_URL", "https://ts.gyzcloud.top/api")
            self.source_status_label.setText(
                f"AKShare：{ak_status}；Tushare：{token_status}；接口：{tushare_url}；本地 CSV / 快照：可用"
            )

        def update_data_mode_label(self):
            orders = {
                "direct": "AKShare → Tushare → 本地 CSV / 研究快照",
                "hybrid": "本地 CSV / 研究快照 → AKShare → Tushare",
                "local": "本地 CSV / 研究快照",
            }
            self.source_status_label.setText(f"当前顺序：{orders.get(self.data_mode_input.currentData(), '未配置')}")

        def _handle_factor_diagnostic_action(self, action: str, payload: object) -> None:
            data = payload if isinstance(payload, dict) else {}
            if action in {"open_data", "prepare_factor"}:
                self.data_page.prepare_from_factor_diagnostic(
                    list(data.get("codes") or []),
                    str(data.get("asset_type") or "ETF"),
                    str(data.get("factor_id") or "") or None,
                )
                self.pages.setCurrentWidget(self.data_page)
                self.statusBar().showMessage("已打开数据管理，请按提示更新行情并生成因子数据。")
                return
            if action == "open_factor_config":
                self.pages.setCurrentWidget(self.factor_page)
                self.statusBar().showMessage("请检查相关因子的资产类型、投资期限和启用配置。")

        def save_settings(self):
            nonlocal store_dir
            store_dir = Path(self.store_input.text()).expanduser()
            store_dir.mkdir(parents=True, exist_ok=True)
            self.factor_page.set_store_root(store_dir)
            self.data_page.set_store_root(store_dir)
            self.global_etf_page.set_store_root(store_dir)
            self.portfolio_page.set_store_root(store_dir)
            save_settings({
                "store_dir": str(store_dir),
                "default_horizon": "medium",
                "default_update_policy": "reuse",
                "data_mode": self.data_mode_input.currentData(),
                "data_manager_mode": self.data_page.mode,
                "evidence_research": self.evidence_input.isChecked(),
                "indicator_config": {
                    "macd_fast": self.macd_fast_input.value(),
                    "macd_slow": self.macd_slow_input.value(),
                    "macd_signal": self.macd_signal_input.value(),
                    "rsi_period": self.rsi_period_input.value(),
                    "atr_period": self.atr_period_input.value(),
                    "adx_period": self.adx_period_input.value(),
                    "bollinger_period": self.bollinger_period_input.value(),
                    "bollinger_std": self.bollinger_std_input.value(),
                },
            })
            self.statusBar().showMessage("设置已保存")

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    return app.exec()
