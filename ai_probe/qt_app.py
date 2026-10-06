"""PySide6 desktop application for AI Probe."""

from __future__ import annotations

import copy
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .client import OpenAIClient
from .config import DATA_FILE, MAX_LOG_LINES, MAX_WORKERS, PROBE_TIMEOUT, RELAY_ERROR_LOG, USAGE_FILE
from .projects import _project_keys, _sync_project_keys, api_key_label, new_project, project_key_for_model
from .relay import RelayServer
from .store_service import StoreService
from .usage import UsageStats
from .utils import parse_channel_import, parse_custom_headers, parse_manual_headers, utc_timestamp

_SKIP_PROBE_KEYWORDS = (
    "dall-e",
    "dalle",
    "gpt-image",
    "image-1",
    "flux",
    "sdxl",
    "stable-diffusion",
    "midjourney",
    "video",
    "veo",
    "sora",
    "kling",
    "runway",
    "pika",
    "tts",
    "whisper",
    "asr",
    "stt",
    "embedding",
    "embed",
    "rerank",
)


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            self.signals.result.emit(self.function())
        except Exception as exc:  # pragma: no cover - depends on network/UI timing
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()


class RelayHost(QObject):
    post_requested = Signal(object, object)
    log_requested = Signal(str)

    def __init__(self, owner):
        super().__init__(owner if isinstance(owner, QObject) else None)
        self.owner = owner
        self.post_requested.connect(self._dispatch)
        self.log_requested.connect(owner._append_log)

    def _dispatch(self, callback, args):
        callback(*args)

    def _post(self, callback, *args):
        self.post_requested.emit(callback, args)

    def _log(self, message):
        self.log_requested.emit(str(message))

    def record_relay_usage(self, project, model, input_tokens, output_tokens, cached_tokens):
        self.owner.usage_stats.record(project, model, input_tokens, output_tokens, cached_tokens)


class QtRelayDialog(QDialog):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.setWindowTitle("订阅与本地中转")
        self.resize(900, 700)
        self.setModal(False)
        self.tabs = QTabWidget()
        self.config_tab = self._build_config_tab()
        self.prompt_tab = self._build_prompt_tab()
        self.stats_tab = self._build_stats_tab()
        self.tabs.addTab(self.config_tab, "中转配置")
        self.tabs.addTab(self.prompt_tab, "全局提示词")
        self.tabs.addTab(self.stats_tab, "今日统计")
        root = QVBoxLayout(self)
        root.addWidget(self.tabs)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_stats)
        self.refresh_timer.start(1200)
        self.refresh_stats()

    def _build_config_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        settings = QGroupBox("中转设置")
        form = QFormLayout(settings)
        relay = self.app.store.get("relay", {})
        self.host = QLineEdit(str(relay.get("host", "127.0.0.1")))
        self.port = QLineEdit(str(relay.get("port", 8040)))
        self.key = QLineEdit(str(relay.get("api_key", "")))
        self.key.setEchoMode(QLineEdit.Password)
        self.user_agent = QLineEdit(str(relay.get("user_agent", "")))
        self.user_agent.setPlaceholderText("留空表示不覆盖客户端和项目的 User-Agent")
        form.addRow("监听地址", self.host)
        form.addRow("本地端口", self.port)
        form.addRow("访问密钥", self.key)
        form.addRow("上游 User-Agent", self.user_agent)
        self.error_logging = QCheckBox("记录中转非 200 精简复现日志")
        self.error_logging.setChecked(bool(relay.get("error_logging_enabled", True)))
        self.request_logging = QCheckBox("记录每个中转请求的转发结果、耗时与用量")
        self.request_logging.setChecked(bool(relay.get("request_logging_enabled", True)))
        self.debug_capture = QCheckBox("调试模式：完整录制请求与返回报文")
        self.debug_capture.setChecked(bool(relay.get("request_debug_capture", False)))
        form.addRow(self.error_logging)
        form.addRow(self.request_logging)
        form.addRow(self.debug_capture)
        layout.addWidget(settings)

        project_box = QGroupBox("启用的 AI 接口")
        project_layout = QVBoxLayout(project_box)
        project_tools = QHBoxLayout()
        select_all = QPushButton("全选")
        clear = QPushButton("清空")
        select_all.clicked.connect(lambda: self._set_all_projects(True))
        clear.clicked.connect(lambda: self._set_all_projects(False))
        project_tools.addWidget(select_all)
        project_tools.addWidget(clear)
        project_tools.addStretch()
        project_layout.addLayout(project_tools)
        self.project_search = QLineEdit()
        self.project_search.setPlaceholderText("搜索项目")
        self.project_search.textChanged.connect(self._filter_projects)
        project_layout.addWidget(self.project_search)
        self.projects = QListWidget()
        self.projects.itemChanged.connect(self._project_changed)
        project_layout.addWidget(self.projects)
        layout.addWidget(project_box, 1)

        actions = QHBoxLayout()
        self.url_label = QLabel("")
        actions.addWidget(self.url_label, 1)
        copy_url = QPushButton("复制地址")
        copy_local = QPushButton("复制 localhost")
        self.stop_button = QPushButton("停止")
        self.start_button = QPushButton("启动中转")
        open_logs = QPushButton("打开日志目录")
        copy_url.clicked.connect(self._copy_url)
        copy_local.clicked.connect(self._copy_local)
        open_logs.clicked.connect(self.app.open_log_dir)
        self.stop_button.clicked.connect(self.app.stop_relay)
        self.start_button.clicked.connect(self.app.start_relay)
        actions.addWidget(copy_url)
        actions.addWidget(copy_local)
        actions.addWidget(open_logs)
        actions.addWidget(self.stop_button)
        actions.addWidget(self.start_button)
        layout.addLayout(actions)
        for field in (self.host, self.port, self.key, self.user_agent):
            field.editingFinished.connect(self.save_settings)
        for field in (self.error_logging, self.request_logging, self.debug_capture):
            field.stateChanged.connect(self.save_settings)
        self.refresh_projects()
        self.update_controls()
        return widget

    def _build_prompt_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        relay = self.app.store.get("relay", {})
        layout.addWidget(QLabel("全局系统提示词"))
        self.prompt = QPlainTextEdit(str(relay.get("system_prompt", "")))
        self.prompt.setPlaceholderText("留空不启用；修改后对新请求即时生效")
        self.prompt.textChanged.connect(self._prompt_changed)
        layout.addWidget(self.prompt, 1)
        self.append_prompt = QCheckBox("拼接客户端传入的系统 / developer 提示词")
        self.append_prompt.setChecked(bool(relay.get("append_user_prompt", True)))
        self.append_prompt.stateChanged.connect(self._prompt_changed)
        layout.addWidget(self.append_prompt)
        return widget

    def _build_stats_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.stats_summary = QLabel("")
        layout.addWidget(self.stats_summary)
        self.stats_table = QTableWidget(0, 7)
        self.stats_table.setHorizontalHeaderLabels(["排名", "项目-模型", "请求", "输入", "输出", "总量", "缓存率"])
        self.stats_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.stats_table, 1)
        clear = QPushButton("清空今日统计")
        clear.clicked.connect(self._clear_stats)
        layout.addWidget(clear, alignment=Qt.AlignRight)
        return widget

    def _prompt_changed(self, *_):
        relay = self.app.store.setdefault("relay", {})
        relay["system_prompt"] = self.prompt.toPlainText()
        relay["append_user_prompt"] = self.append_prompt.isChecked()
        if self.app.relay_server:
            self.app.relay_server.prompt_settings = (relay["system_prompt"], relay["append_user_prompt"])
        self.app.schedule_save()

    def save_settings(self, *_):
        try:
            port = int(self.port.text().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "本地中转", "端口必须是 1 到 65535 之间的数字")
            return
        relay = self.app.store.setdefault("relay", {})
        relay.update(
            host=self.host.text().strip() or "127.0.0.1",
            port=port,
            api_key=self.key.text().strip(),
            user_agent=self.user_agent.text().strip(),
            error_logging_enabled=self.error_logging.isChecked(),
            request_logging_enabled=self.request_logging.isChecked(),
            request_debug_capture=self.debug_capture.isChecked(),
        )
        if self.app.relay_server:
            self.app.relay_server.auth_key = relay["api_key"]
            self.app.relay_server.user_agent = relay["user_agent"]
            self.app.relay_server.error_logging_enabled = relay["error_logging_enabled"]
            self.app.relay_server.request_logging_enabled = relay["request_logging_enabled"]
            self.app.relay_server.request_debug_capture = relay["request_debug_capture"]
        self.app.schedule_save()
        self.update_controls()

    def refresh_projects(self):
        relay = self.app.store.get("relay", {})
        enabled = set(relay.get("project_ids", []))
        self.projects.blockSignals(True)
        self.projects.clear()
        for project in self.app.store.get("projects", []):
            item = QListWidgetItem(
                f"{project.get('name', '未命名项目')} · {len(_project_keys(project))} 个密钥 · "
                f"{len(project.get('models', []))} 个模型 · {project.get('api_mode', 'chat')}"
            )
            item.setData(Qt.UserRole, project["id"])
            item.setData(Qt.UserRole + 1, str(project.get("name", "")))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if project["id"] in enabled else Qt.Unchecked)
            self.projects.addItem(item)
        self.projects.blockSignals(False)
        self._filter_projects()

    def _filter_projects(self, *_):
        # Hide rather than remove rows so filtered-out projects keep their enabled state.
        query = self.project_search.text().strip().casefold()
        for index in range(self.projects.count()):
            item = self.projects.item(index)
            name = str(item.data(Qt.UserRole + 1) or "").casefold()
            item.setHidden(bool(query) and query not in name)

    def _project_changed(self, item, *_):
        relay = self.app.store.setdefault("relay", {})
        ids = []
        for index in range(self.projects.count()):
            row = self.projects.item(index)
            if row.checkState() == Qt.Checked:
                ids.append(row.data(Qt.UserRole))
        relay["project_ids"] = ids
        if self.app.relay_server:
            self.app.relay_server.invalidate_routes()
        self.app.schedule_save()

    def _set_all_projects(self, checked):
        self.projects.blockSignals(True)
        for index in range(self.projects.count()):
            item = self.projects.item(index)
            if not item.isHidden():
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.projects.blockSignals(False)
        self._project_changed(self.projects.item(0) if self.projects.count() else QListWidgetItem())

    def update_controls(self):
        running = self.app.relay_server is not None
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        if running:
            display = "127.0.0.1" if self.app.relay_server.host in {"0.0.0.0", "::"} else self.app.relay_server.host
            self.url_label.setText(f"OpenAI Base URL：http://{display}:{self.app.relay_server.port}/v1")
        else:
            self.url_label.setText("未启动")

    def _copy_url(self):
        if self.app.relay_server:
            QApplication.clipboard().setText(self.url_label.text().split("：", 1)[-1])

    def _copy_local(self):
        if self.app.relay_server:
            QApplication.clipboard().setText(f"http://localhost:{self.app.relay_server.port}/v1")

    def _clear_stats(self):
        if QMessageBox.question(self, "清空统计", "确定清空今日用量统计吗？") == QMessageBox.Yes:
            self.app.usage_stats.clear_today()
            self.refresh_stats()

    def refresh_stats(self):
        snapshot = self.app.usage_stats.snapshot()
        input_tokens = int(snapshot.get("input_tokens", 0))
        output_tokens = int(snapshot.get("output_tokens", 0))
        cached = int(snapshot.get("cached_tokens", 0))
        total = input_tokens + output_tokens
        cache_rate = f"{cached / input_tokens * 100:.1f}%" if input_tokens else "-"
        self.stats_summary.setText(
            f"请求 {snapshot.get('requests', 0)} · 输入 {input_tokens:,} · 输出 {output_tokens:,} · "
            f"总量 {total:,} · 缓存 {cache_rate}"
        )
        rows = list(snapshot.get("models", {}).values())
        rows.sort(key=lambda row: row.get("input_tokens", 0) + row.get("output_tokens", 0), reverse=True)
        self.stats_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            in_count = int(row.get("input_tokens", 0))
            out_count = int(row.get("output_tokens", 0))
            cached_count = int(row.get("cached_tokens", 0))
            values = [
                str(index + 1),
                f"{row.get('project_name', '')}-{row.get('model', '')}",
                str(row.get("requests", 0)),
                f"{in_count:,}",
                f"{out_count:,}",
                f"{in_count + out_count:,}",
                f"{cached_count / in_count * 100:.1f}%" if in_count else "-",
            ]
            for col, value in enumerate(values):
                self.stats_table.setItem(index, col, QTableWidgetItem(value))

    def closeEvent(self, event):
        self.app.commit_form()
        self.app._save_store()
        self.app.relay_dialog = None
        self.refresh_timer.stop()
        event.accept()


class QtMainWindow(QMainWindow):
    post_requested = Signal(object, object)
    log_requested = Signal(str)

    def __init__(self, config_key: bytes, data_file: Path = DATA_FILE, usage_file: Path = USAGE_FILE):
        super().__init__()
        self.config_key = config_key
        self.store_service = StoreService(config_key, data_file)
        self.store = self.store_service.load()
        self.current_id = self.store.get("selected_project_id")
        self.usage_stats = UsageStats(usage_file)
        self.relay_server = None
        self.relay_dialog = None
        self.loading_form = False
        self.busy = False
        self._workers = set()
        self.threadpool = QThreadPool(self)
        self.threadpool.setMaxThreadCount(MAX_WORKERS)
        self.remote_model_entries = []
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._flush_form_save)
        self._probe_save_dirty = False
        self.post_requested.connect(self._dispatch_post)
        self.log_requested.connect(self._append_log)
        self.relay_host = RelayHost(self)
        self._ensure_selection()
        self._build_window()
        self._load_current_project()
        self._setup_tray()
        self._load_store_timer = QTimer(self)
        self._load_store_timer.setSingleShot(True)
        if self.store_service.loaded_plaintext:
            self._load_store_timer.timeout.connect(self._save_store)
            self._load_store_timer.start(0)

    def _build_window(self):
        self.setWindowTitle("AI Probe · 多项目测活与本地中转")
        self.resize(1500, 940)
        self.setMinimumSize(1120, 720)
        toolbar = QToolBar("工具")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        actions = [
            ("新建项目", self.new_project),
            ("删除项目", self.delete_project),
            ("渠道导入", self.import_channels),
            ("备份配置", self.backup_config),
            ("导入配置", self.import_config),
            ("本地中转", self.open_relay),
            ("最小化", self.minimize_to_tray),
        ]
        for text, callback in actions:
            action = QAction(text, self)
            action.triggered.connect(callback)
            toolbar.addAction(action)
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_project_sidebar())
        splitter.addWidget(self._build_workspace())
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 1180])
        root.addWidget(splitter, 1)
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("statusLabel")
        root.addWidget(self.status_label)
        self.setCentralWidget(central)
        status = QStatusBar()
        self.setStatusBar(status)
        self.statusBar().showMessage("就绪")
        self.setStyleSheet(
            "QMainWindow { background: #f7f9fc; } QGroupBox { font-weight: 600; margin-top: 8px; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; } "
            "QToolBar { spacing: 6px; padding: 5px; } QListWidget, QTreeWidget, QPlainTextEdit { background: white; }"
        )

    def _build_project_sidebar(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        title = QLabel("项目")
        title.setFont(QFont("Microsoft YaHei UI", 14, QFont.Bold))
        layout.addWidget(title)
        self.project_search = QLineEdit()
        self.project_search.setPlaceholderText("搜索项目")
        self.project_search.textChanged.connect(self.refresh_project_list)
        layout.addWidget(self.project_search)
        self.project_list = QListWidget()
        self.project_list.currentRowChanged.connect(self._select_project_row)
        self.project_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.project_list.customContextMenuRequested.connect(self._project_menu)
        layout.addWidget(self.project_list, 1)
        controls = QHBoxLayout()
        new_button = QPushButton("新建")
        delete_button = QPushButton("删除")
        rename_button = QPushButton("重命名")
        new_button.clicked.connect(self.new_project)
        delete_button.clicked.connect(self.delete_project)
        rename_button.clicked.connect(self.rename_project)
        controls.addWidget(new_button)
        controls.addWidget(rename_button)
        controls.addWidget(delete_button)
        layout.addLayout(controls)
        self.refresh_project_list()
        return widget

    def _build_workspace(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addWidget(self._build_project_form())
        layout.addWidget(self._build_models_panel(), 1)
        log_box = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_box)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(MAX_LOG_LINES)
        self.log_text.setMinimumHeight(110)
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_box)
        scroll.setWidget(container)
        return scroll

    def _build_project_form(self):
        box = QGroupBox("项目配置")
        root = QVBoxLayout(box)
        form = QFormLayout()
        self.project_name = QLineEdit()
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("https://api.example.com/v1")
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.proxy_url = QLineEdit()
        self.api_mode = QComboBox()
        self.api_mode.addItem("Chat Completions", "chat")
        self.api_mode.addItem("Responses", "responses")
        self.api_mode.addItem("Anthropic Messages", "anthropic")
        form.addRow("项目名称", self.project_name)
        form.addRow("API 地址", self.base_url)
        form.addRow("API 密钥", self.api_key)
        form.addRow("API 模式", self.api_mode)
        root.addLayout(form)
        buttons = QHBoxLayout()
        show_key = QPushButton("显示密钥")
        keys = QPushButton("管理密钥")
        advanced = QPushButton("高级设置")
        show_key.setCheckable(True)
        show_key.toggled.connect(
            lambda checked: self.api_key.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        )
        keys.clicked.connect(self.manage_api_keys)
        advanced.clicked.connect(self.toggle_advanced)
        buttons.addWidget(show_key)
        buttons.addWidget(keys)
        buttons.addWidget(advanced)
        buttons.addStretch()
        root.addLayout(buttons)
        self.advanced_box = QGroupBox("高级设置")
        advanced_layout = QVBoxLayout(self.advanced_box)
        advanced_form = QFormLayout()
        self.skip_ssl = QCheckBox("跳过 SSL 证书校验")
        self.test_prompt = QPlainTextEdit()
        self.test_prompt.setMaximumHeight(75)
        self.test_prompt.setPlaceholderText("测活提示词")
        advanced_form.addRow("HTTP 代理", self.proxy_url)
        advanced_form.addRow(self.skip_ssl)
        advanced_form.addRow("测活提示词", self.test_prompt)
        advanced_layout.addLayout(advanced_form)
        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("自定义请求头"))
        self.headers_mode = QComboBox()
        self.headers_mode.addItem("JSON", "json")
        self.headers_mode.addItem("手动", "manual")
        self.headers_mode.currentIndexChanged.connect(self.switch_headers_mode)
        add_agent = QPushButton("添加 User-Agent")
        add_header = QPushButton("添加请求头")
        add_agent.clicked.connect(self.add_user_agent)
        add_header.clicked.connect(self.add_manual_header)
        header_row.addWidget(self.headers_mode)
        header_row.addWidget(add_agent)
        header_row.addWidget(add_header)
        header_row.addStretch()
        advanced_layout.addLayout(header_row)
        self.headers_stack = QStackedWidget()
        self.headers_json = QPlainTextEdit()
        self.headers_json.setPlaceholderText('{"X-Test": "value"}')
        self.manual_headers = QTableWidget(0, 3)
        self.manual_headers.setHorizontalHeaderLabels(["名称", "值", "操作"])
        self.manual_headers.itemChanged.connect(lambda _item: self.schedule_save())
        self.manual_headers.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.manual_headers.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.manual_headers.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.headers_stack.addWidget(self.headers_json)
        self.headers_stack.addWidget(self.manual_headers)
        advanced_layout.addWidget(self.headers_stack)
        self.advanced_box.setVisible(False)
        root.addWidget(self.advanced_box)
        for field in (self.project_name, self.base_url, self.api_key, self.proxy_url):
            field.textChanged.connect(self.schedule_save)
        self.api_mode.currentIndexChanged.connect(self.schedule_save)
        self.skip_ssl.stateChanged.connect(self.schedule_save)
        self.test_prompt.textChanged.connect(self.schedule_save)
        self.headers_json.textChanged.connect(self.schedule_save)
        return box

    def _build_models_panel(self):
        box = QGroupBox("模型")
        root = QVBoxLayout(box)
        panes = QSplitter(Qt.Horizontal)
        remote_box = QGroupBox("远程模型")
        remote_layout = QVBoxLayout(remote_box)
        self.remote_list = QListWidget()
        self.remote_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.remote_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.remote_list.customContextMenuRequested.connect(self._remote_menu)
        copy_shortcut = QShortcut(QKeySequence.Copy, self.remote_list)
        copy_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        copy_shortcut.activated.connect(self.copy_remote_model)
        remote_layout.addWidget(self.remote_list)
        remote_buttons = QHBoxLayout()
        fetch = QPushButton("获取模型列表")
        add_selected = QPushButton("添加选中")
        add_all = QPushButton("添加全部")
        fetch.clicked.connect(self.fetch_models)
        add_selected.clicked.connect(self.add_selected_models)
        add_all.clicked.connect(self.add_all_models)
        remote_buttons.addWidget(fetch)
        remote_buttons.addWidget(add_selected)
        remote_buttons.addWidget(add_all)
        remote_layout.addLayout(remote_buttons)
        panes.addWidget(remote_box)
        local_box = QGroupBox("项目模型")
        local_layout = QVBoxLayout(local_box)
        self.model_tree = QTreeWidget()
        self.model_tree.setColumnCount(5)
        self.model_tree.setHeaderLabels(["模型", "密钥", "状态", "首字延时", "响应 / 错误"])
        self.model_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.model_tree.header().setSectionResizeMode(4, QHeaderView.Stretch)
        self.model_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.model_tree.itemDoubleClicked.connect(lambda *_: self.show_model_detail())
        self.model_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.model_tree.customContextMenuRequested.connect(self._model_menu)
        delete_shortcut = QShortcut(QKeySequence.Delete, self.model_tree)
        delete_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        delete_shortcut.activated.connect(self.remove_selected)
        local_layout.addWidget(self.model_tree)
        custom_row = QHBoxLayout()
        self.custom_model = QLineEdit()
        self.custom_model.setPlaceholderText("自定义模型 ID")
        add_custom = QPushButton("添加")
        add_custom.clicked.connect(self.add_custom_model)
        self.custom_model.returnPressed.connect(self.add_custom_model)
        custom_row.addWidget(self.custom_model)
        custom_row.addWidget(add_custom)
        local_layout.addLayout(custom_row)
        local_buttons = QHBoxLayout()
        test_all = QPushButton("测活全部")
        test_selected = QPushButton("测活选中")
        detect = QPushButton("检测并仅添加可用")
        remove = QPushButton("删除选中")
        test_all.clicked.connect(self.test_all)
        test_selected.clicked.connect(self.test_selected)
        detect.clicked.connect(self.detect_all)
        remove.clicked.connect(self.remove_selected)
        for button in (test_all, test_selected, detect, remove):
            local_buttons.addWidget(button)
        local_layout.addLayout(local_buttons)
        panes.addWidget(local_box)
        panes.setSizes([420, 780])
        root.addWidget(panes, 1)
        return box

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self._make_icon())
        menu = QMenu()
        show = menu.addAction("显示窗口")
        show.triggered.connect(self.restore_from_tray)
        menu.addSeparator()
        light = menu.addAction("轻量模式")
        light.setCheckable(True)
        light.toggled.connect(self.switch_lightweight)
        quit_action = menu.addAction("退出")
        quit_action.triggered.connect(self.close)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.restore_from_tray() if reason == QSystemTrayIcon.Trigger else None
        )
        self.tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        if self.tray_available:
            self.tray.show()

    @staticmethod
    def _make_icon():
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setBrush(QColor("#0b74de"))
        painter.drawRoundedRect(4, 4, 56, 56, 12, 12)
        painter.setBrush(QColor("white"))
        painter.drawRoundedRect(18, 13, 28, 38, 5, 5)
        painter.setBrush(QColor("#0b74de"))
        painter.drawRect(25, 20, 14, 24)
        painter.end()
        return QIcon(pixmap)

    def _ensure_selection(self):
        ids = {project["id"] for project in self.store["projects"]}
        if self.current_id not in ids:
            self.current_id = self.store["projects"][0]["id"]
        self.store["selected_project_id"] = self.current_id

    def project(self, project_id=None):
        project_id = project_id or self.current_id
        return next((item for item in self.store["projects"] if item["id"] == project_id), None)

    def refresh_project_list(self):
        query = self.project_search.text().strip().casefold()
        self.project_list.blockSignals(True)
        self.project_list.clear()
        for project in self.store["projects"]:
            if query and query not in str(project.get("name", "")).casefold():
                continue
            item = QListWidgetItem(str(project.get("name", "未命名项目")))
            item.setData(Qt.UserRole, project["id"])
            self.project_list.addItem(item)
        self._highlight_current_project()
        self.project_list.blockSignals(False)

    def _highlight_current_project(self):
        """把高亮拨到 current_id 所在行，调用方需自行 blockSignals。"""
        for row in range(self.project_list.count()):
            if self.project_list.item(row).data(Qt.UserRole) == self.current_id:
                self.project_list.setCurrentRow(row)
                return

    def _select_project_row(self, row):
        if row < 0 or row >= self.project_list.count():
            # 点到列表空白处时 Qt 会清掉当前项，这里补回高亮，保持列表与右侧表单一致。
            self.project_list.blockSignals(True)
            self._highlight_current_project()
            self.project_list.blockSignals(False)
            return
        project_id = self.project_list.item(row).data(Qt.UserRole)
        if project_id == self.current_id:
            return
        self.commit_form()
        self.current_id = project_id
        self.store["selected_project_id"] = project_id
        self._load_current_project()
        # commit_form 可能因改名重建列表并停留在旧项目上，这里把高亮拨回本次点击的项目。
        self.project_list.blockSignals(True)
        self._highlight_current_project()
        self.project_list.blockSignals(False)
        self._save_store()

    def _project_menu(self, pos):
        item = self.project_list.itemAt(pos)
        if item:
            self.project_list.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction("重命名", self.rename_project)
        menu.addAction("复制项目", self.copy_project)
        menu.addAction("复制渠道", self.copy_channel)
        menu.addAction("删除项目", self.delete_project)
        menu.exec(self.project_list.mapToGlobal(pos))

    def _load_current_project(self):
        project = self.project()
        if not project:
            return
        self.loading_form = True
        self.project_name.setText(str(project.get("name", "")))
        self.base_url.setText(str(project.get("base_url", "")))
        self.api_key.setText(str(project.get("api_key", "")))
        self.proxy_url.setText(str(project.get("proxy_url", "")))
        self.skip_ssl.setChecked(bool(project.get("skip_ssl_verify", False)))
        mode = str(project.get("api_mode", "chat"))
        self.api_mode.setCurrentIndex(max(0, self.api_mode.findData(mode)))
        self.test_prompt.setPlainText(str(project.get("test_prompt", "")))
        self.headers_json.setPlainText(self._headers_json(project))
        header_mode = str(project.get("headers_mode", "json"))
        self.headers_mode.setCurrentIndex(1 if header_mode == "manual" else 0)
        self._load_manual_headers(project)
        self.loading_form = False
        self.refresh_models()

    @staticmethod
    def _headers_json(project):
        value = project.get("custom_headers", "")
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False, indent=2)
        return str(value or "")

    def _load_manual_headers(self, project):
        rows = project.get("manual_headers", [])
        if not isinstance(rows, list) or not rows:
            try:
                parsed = parse_custom_headers(self._headers_json(project))
                rows = [{"name": name, "value": value} for name, value in parsed.items()]
            except ValueError:
                rows = []
        self.manual_headers.setRowCount(0)
        for row in rows:
            if not isinstance(row, dict):
                continue
            self._add_manual_row(str(row.get("name") or ""), str(row.get("value") or ""), schedule=False)

    def _add_manual_row(self, name="", value="", schedule=True):
        row = self.manual_headers.rowCount()
        self.manual_headers.insertRow(row)
        self.manual_headers.setItem(row, 0, QTableWidgetItem(name))
        self.manual_headers.setItem(row, 1, QTableWidgetItem(value))
        remove = QPushButton("删除")
        remove.clicked.connect(lambda: self.manual_headers.removeRow(self.manual_headers.indexAt(remove.pos()).row()))
        self.manual_headers.setCellWidget(row, 2, remove)
        for column in (0, 1):
            editor = self.manual_headers.item(row, column)
            if editor is not None:
                editor.setFlags(editor.flags() | Qt.ItemIsEditable)
        if schedule:
            self.schedule_save()

    def add_manual_header(self):
        if self.headers_mode.currentData() != "manual":
            self.headers_mode.setCurrentIndex(1)
        self._add_manual_row()

    def add_user_agent(self):
        if self.headers_mode.currentData() == "manual":
            for row in range(self.manual_headers.rowCount()):
                if (self.manual_headers.item(row, 0).text() or "").strip().lower() == "user-agent":
                    return
            self._add_manual_row("User-Agent", "")
            return
        try:
            headers = parse_custom_headers(self.headers_json.toPlainText())
        except ValueError as exc:
            QMessageBox.warning(self, "请求头格式错误", str(exc))
            return
        if not any(name.lower() == "user-agent" for name in headers):
            headers["User-Agent"] = ""
            self.headers_json.setPlainText(json.dumps(headers, ensure_ascii=False, indent=2))

    def switch_headers_mode(self, *_):
        if self.loading_form:
            self.headers_stack.setCurrentIndex(self.headers_mode.currentIndex())
            return
        mode = self.headers_mode.currentData()
        try:
            if mode == "manual":
                parsed = parse_custom_headers(self.headers_json.toPlainText())
                self.manual_headers.setRowCount(0)
                for name, value in parsed.items():
                    self._add_manual_row(name, value, schedule=False)
            else:
                parsed = self._manual_header_values()
                self.headers_json.setPlainText(json.dumps(parsed, ensure_ascii=False, indent=2) if parsed else "")
        except ValueError as exc:
            QMessageBox.warning(self, "请求头格式错误", str(exc))
            self.loading_form = True
            self.headers_mode.setCurrentIndex(0 if mode == "manual" else 1)
            self.loading_form = False
            return
        self.headers_stack.setCurrentIndex(self.headers_mode.currentIndex())
        self.schedule_save()

    def _manual_header_values(self):
        result = []
        for row in range(self.manual_headers.rowCount()):
            result.append(
                {
                    "name": self.manual_headers.item(row, 0).text() if self.manual_headers.item(row, 0) else "",
                    "value": self.manual_headers.item(row, 1).text() if self.manual_headers.item(row, 1) else "",
                }
            )
        return result

    def commit_form(self):
        project = self.project()
        if not project:
            return
        name = self.project_name.text().strip() or "未命名项目"
        renamed = name != project.get("name")
        project["name"] = name
        project["base_url"] = self.base_url.text().strip()
        key = self.api_key.text().strip()
        project["api_key"] = key
        keys = project.get("api_keys")
        if isinstance(keys, list) and keys:
            keys[0]["value"] = key
        else:
            project["api_keys"] = [{"id": "default", "name": "默认", "value": key}]
        _sync_project_keys(project)
        project["proxy_url"] = self.proxy_url.text().strip()
        project["skip_ssl_verify"] = self.skip_ssl.isChecked()
        project["api_mode"] = self.api_mode.currentData()
        project["test_prompt"] = self.test_prompt.toPlainText().strip()
        project["headers_mode"] = self.headers_mode.currentData()
        project["custom_headers"] = self.headers_json.toPlainText().strip()
        project["manual_headers"] = self._manual_header_values()
        self.store["selected_project_id"] = self.current_id
        if renamed:
            # 只有改名才需要重建侧栏；否则每次提交都会打断列表滚动位置与高亮。
            self.refresh_project_list()

    def schedule_save(self):
        if self.loading_form:
            return
        self._save_timer.start(450)

    def _flush_form_save(self):
        self.commit_form()
        self._save_store()

    def _save_store(self):
        if self.relay_server:
            self.relay_server.invalidate_routes()
        try:
            self.store_service.save(self.store)
        except (OSError, RuntimeError) as exc:
            self.set_status(f"保存失败：{exc}")

    def refresh_models(self):
        project = self.project()
        if not project:
            return
        keys = _project_keys(project)
        self.remote_model_entries = [
            item if isinstance(item, dict) else {"id": str(item)} for item in project.get("discovered_models", [])
        ]
        self.remote_list.clear()
        for entry in self.remote_model_entries:
            key = project_key_for_model(project, entry)
            self.remote_list.addItem(
                f"{entry.get('id', '')}  [{api_key_label(key)}]" if len(keys) > 1 else entry.get("id", "")
            )
        self.model_tree.clear()
        for model in project.get("models", []):
            self._render_model(model)

    @staticmethod
    def _format_ms(value):
        return "-" if value is None else f"{value / 1000:.1f}s"

    def _render_model(self, model):
        key = api_key_label(project_key_for_model(self.project(), model))
        status = str(model.get("status", "未测试"))
        detail = (
            (model.get("error") or model.get("reply"))
            if status == "不可用"
            else (model.get("reply") or model.get("error"))
        )
        item = QTreeWidgetItem(
            [
                str(model.get("id", "")),
                key,
                status,
                self._format_ms(model.get("first_ms")),
                str(detail or "").replace("\n", " ")[:180],
            ]
        )
        item.setData(0, Qt.UserRole, model.get("id", ""))
        if status == "可用":
            item.setForeground(2, QColor("#087f23"))
        elif status == "不可用":
            item.setForeground(2, QColor("#c9342b"))
        self.model_tree.addTopLevelItem(item)

    def selected_model_ids(self):
        return [item.data(0, Qt.UserRole) for item in self.model_tree.selectedItems()]

    def _model_menu(self, pos):
        item = self.model_tree.itemAt(pos)
        menu = QMenu(self)
        menu.addAction("复制模型名称", lambda: self.copy_text(str(item.data(0, Qt.UserRole))) if item else None)
        menu.addAction("复制返回内容", self.copy_model_reply).setEnabled(item is not None)
        menu.addAction("设置渠道模型名", self.set_route_name).setEnabled(item is not None)
        menu.addAction("设置密钥", self.assign_selected_key).setEnabled(item is not None)
        menu.addAction("删除", self.remove_selected).setEnabled(item is not None)
        menu.addSeparator()
        menu.addAction("移除全部模型", self.remove_all_models).setEnabled(self.model_tree.topLevelItemCount() > 0)
        menu.exec(self.model_tree.mapToGlobal(pos))

    def remote_selected_ids(self):
        ids = []
        for row in range(self.remote_list.count()):
            item = self.remote_list.item(row)
            if item is not None and item.isSelected() and row < len(self.remote_model_entries):
                ids.append(str(self.remote_model_entries[row].get("id", "")))
        return [model_id for model_id in ids if model_id]

    def copy_remote_model(self):
        ids = self.remote_selected_ids()
        if ids:
            self.copy_text("\n".join(ids))
            self.set_status(f"已复制模型：{', '.join(ids)}")

    def _remote_menu(self, pos):
        item = self.remote_list.itemAt(pos)
        if item is not None and not item.isSelected():
            # 右键点击未选中项时切换选中，已选中的多项则保留，方便批量操作。
            self.remote_list.clearSelection()
            item.setSelected(True)
            self.remote_list.setCurrentItem(item)
        menu = self._build_remote_menu()
        menu.exec(self.remote_list.mapToGlobal(pos))

    def _build_remote_menu(self):
        has_selection = bool(self.remote_selected_ids())
        menu = QMenu(self)
        menu.addAction("复制模型名称", self.copy_remote_model).setEnabled(has_selection)
        menu.addSeparator()
        menu.addAction("添加选中", self.add_selected_models).setEnabled(has_selection)
        menu.addAction("添加全部", self.add_all_models).setEnabled(self.remote_list.count() > 0)
        return menu

    def new_project(self):
        self.commit_form()
        project = new_project(f"项目 {len(self.store['projects']) + 1}")
        self.store["projects"].append(project)
        self.current_id = project["id"]
        self._ensure_selection()
        self.refresh_project_list()
        self._load_current_project()
        self._save_store()
        self._sync_relay_dialog()

    def delete_project(self):
        project = self.project()
        if not project or QMessageBox.question(self, "删除项目", f"确定删除“{project['name']}”吗？") != QMessageBox.Yes:
            return
        self.store["projects"] = [item for item in self.store["projects"] if item["id"] != project["id"]]
        if not self.store["projects"]:
            self.store["projects"] = [new_project("默认项目")]
        self.current_id = self.store["projects"][0]["id"]
        self._ensure_selection()
        self.refresh_project_list()
        self._load_current_project()
        self._save_store()
        self._sync_relay_dialog()

    def rename_project(self):
        project = self.project()
        if not project:
            return
        name, ok = QInputDialog.getText(self, "重命名项目", "项目名称：", text=project["name"])
        if not ok:
            return
        name = name.strip()
        if not name:
            QMessageBox.warning(self, "重命名项目", "项目名称不能为空")
            return
        # 先同步到表单再提交，否则 commit_form 会用输入框里的旧名称覆盖掉新名称。
        self.project_name.setText(name)
        self.commit_form()
        self._save_store()
        self._sync_relay_dialog()

    def copy_project(self):
        project = self.project()
        if not project:
            return
        self.commit_form()
        duplicate = copy.deepcopy(project)
        duplicate["id"] = uuid.uuid4().hex
        base = f"{project['name']} 副本"
        names = {item["name"] for item in self.store["projects"]}
        name = base
        suffix = 2
        while name in names:
            name = f"{base} {suffix}"
            suffix += 1
        duplicate["name"] = name
        index = self.store["projects"].index(project)
        self.store["projects"].insert(index + 1, duplicate)
        self.current_id = duplicate["id"]
        self.refresh_project_list()
        self._load_current_project()
        self._save_store()
        self._sync_relay_dialog()

    def copy_channel(self):
        project = self.project()
        if not project:
            return
        self.commit_form()
        if not project.get("base_url") or not project.get("api_key"):
            QMessageBox.warning(self, "复制渠道", "项目缺少 API 地址或密钥")
            return
        value = json.dumps(
            {"_type": "newapi_channel_conn", "key": project["api_key"], "url": project["base_url"]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.copy_text(value)
        self.set_status(f"已复制渠道：{project['name']}")

    def import_channels(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("渠道导入")
        dialog.resize(700, 440)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("支持 newapi_channel_conn JSON、Markdown 链接，或 URL 与密钥分行粘贴"))
        editor = QPlainTextEdit()
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText("识别并导入")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            channels = parse_channel_import(editor.toPlainText())
        except ValueError as exc:
            QMessageBox.warning(self, "渠道导入失败", str(exc))
            return
        self.commit_form()
        existing = {
            (str(item.get("base_url", "")).rstrip("/"), str(item.get("api_key", "")).strip())
            for item in self.store["projects"]
        }
        names = {item["name"] for item in self.store["projects"]}
        imported = []
        duplicates = 0
        for channel in channels:
            signature = (channel["url"].rstrip("/"), channel["key"])
            if signature in existing:
                duplicates += 1
                continue
            host = urlsplit(channel["url"]).hostname or "导入渠道"
            name = host
            suffix = 2
            while name in names:
                name = f"{host} {suffix}"
                suffix += 1
            project = new_project(name)
            project["base_url"] = channel["url"]
            project["api_key"] = channel["key"]
            project["api_keys"] = [{"id": "default", "name": "默认", "value": channel["key"]}]
            self.store["projects"].append(project)
            existing.add(signature)
            names.add(name)
            imported.append(project)
        if imported:
            self.current_id = imported[0]["id"]
            self._ensure_selection()
            self.refresh_project_list()
            self._load_current_project()
            self._save_store()
            self._sync_relay_dialog()
        QMessageBox.information(self, "渠道导入", f"新增 {len(imported)} 个渠道，跳过 {duplicates} 个重复渠道。")

    def manage_api_keys(self):
        project = self.project()
        if not project:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("管理 API 密钥")
        dialog.resize(700, 420)
        layout = QVBoxLayout(dialog)
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["名称", "密钥", "操作"])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(table)
        rows = []

        def add_row(key=None):
            key = key or {"id": uuid.uuid4().hex, "name": "", "value": ""}
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(str(key.get("name", ""))))
            table.setItem(row, 1, QTableWidgetItem(str(key.get("value", ""))))
            button = QPushButton("删除")
            button.clicked.connect(lambda: table.removeRow(table.indexAt(button.pos()).row()))
            table.setCellWidget(row, 2, button)
            rows.append(str(key.get("id") or uuid.uuid4().hex))

        for key in _project_keys(project):
            add_row(key)
        actions = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        add_button = QPushButton("新增密钥")
        actions.addButton(add_button, QDialogButtonBox.ActionRole)
        add_button.clicked.connect(lambda: add_row())
        actions.accepted.connect(dialog.accept)
        actions.rejected.connect(dialog.reject)
        layout.addWidget(actions)
        if dialog.exec() != QDialog.Accepted:
            return
        keys = []
        for row in range(table.rowCount()):
            keys.append(
                {
                    "id": rows[row] if row < len(rows) else uuid.uuid4().hex,
                    "name": table.item(row, 0).text().strip(),
                    "value": table.item(row, 1).text(),
                }
            )
        if not keys:
            keys = [{"id": "default", "name": "默认", "value": ""}]
        project["api_keys"] = keys
        _sync_project_keys(project)
        valid = {key["id"] for key in keys}
        for model in project.get("models", []):
            if model.get("api_key_id") not in valid:
                model["api_key_id"] = keys[0]["id"]
        self._save_store()
        self._load_current_project()

    def toggle_advanced(self, *_):
        self.advanced_box.setVisible(not self.advanced_box.isVisible())

    def _snapshot_clients(self):
        self.commit_form()
        project = self.project()
        if not project:
            return None
        try:
            headers = (
                parse_manual_headers(project.get("manual_headers", []))
                if project.get("headers_mode") == "manual"
                else parse_custom_headers(project.get("custom_headers", ""))
            )
            clients = {}
            for key in _project_keys(project):
                clients[key["id"]] = OpenAIClient(
                    project["base_url"],
                    key["value"],
                    project["api_mode"],
                    project.get("test_prompt", ""),
                    headers,
                    project.get("proxy_url", ""),
                    verify_ssl=not bool(project.get("skip_ssl_verify", False)),
                )
            return project["id"], clients
        except (ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "配置错误", str(exc))
            return None

    @staticmethod
    def _collect_models(clients):
        items = list(clients.items())
        found = {}
        errors = []
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(items) or 1)) as pool:
            futures = {pool.submit(client.list_models): key_id for key_id, client in items}
            for future in as_completed(futures):
                key_id = futures[future]
                try:
                    for model_id in future.result():
                        found.setdefault(model_id, key_id)
                except Exception as exc:
                    errors.append(str(exc))
        return [
            {"id": model_id, "api_key_id": key_id}
            for model_id, key_id in sorted(found.items(), key=lambda pair: pair[0].lower())
        ], errors

    def run_worker(self, function, on_result, label):
        if self.busy:
            QMessageBox.information(self, "任务进行中", "请等待当前网络任务完成")
            return
        self.busy = True
        self.set_status(label)
        worker = Worker(function)
        self._workers.add(worker)
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(lambda error: self.job_error(error))
        worker.signals.finished.connect(lambda: self.finish_worker(worker))
        self.threadpool.start(worker)

    def finish_worker(self, worker):
        self._workers.discard(worker)
        self.busy = bool(self._workers)

    def job_error(self, error):
        self.set_status(f"失败：{error}")
        self._append_log(f"ERROR {error}")
        QMessageBox.critical(self, "请求失败", error)

    def fetch_models(self):
        snapshot = self._snapshot_clients()
        if not snapshot:
            return
        project_id, clients = snapshot
        self.run_worker(
            lambda: self._collect_models(clients),
            lambda value: self._apply_discovered(project_id, value),
            "正在获取模型列表...",
        )

    def _apply_discovered(self, project_id, value):
        models, errors = value
        project = self.project(project_id)
        if not project:
            return
        project["discovered_models"] = models
        self._save_store()
        self.refresh_models()
        self.set_status(f"获取完成：{len(models)} 个模型")
        if errors:
            self._append_log("获取模型列表失败：" + "；".join(errors))

    def add_selected_models(self):
        rows = sorted(self.remote_list.row(item) for item in self.remote_list.selectedItems())
        self.add_models([self.remote_model_entries[row] for row in rows if row < len(self.remote_model_entries)])

    def add_all_models(self):
        self.add_models(self.remote_model_entries)

    def add_models(self, entries):
        project = self.project()
        if not project:
            return
        existing = {item["id"] for item in project["models"]}
        default_key = _project_keys(project)[0]["id"]
        for entry in entries:
            model_id = str(entry.get("id", "")).strip()
            if model_id and model_id not in existing:
                project["models"].append(
                    {
                        "id": model_id,
                        "api_key_id": entry.get("api_key_id", default_key),
                        "route_name": "",
                        "status": "未测试",
                        "first_ms": None,
                        "total_ms": None,
                        "reply": "",
                        "error": "",
                    }
                )
                existing.add(model_id)
        project["models"].sort(key=lambda item: item["id"].lower())
        self._save_store()
        self.refresh_models()

    def add_custom_model(self):
        model_id = self.custom_model.text().strip()
        if not model_id:
            QMessageBox.information(self, "添加自定义模型", "请输入模型 ID")
            return
        self.add_models([{"id": model_id}])
        self.custom_model.clear()

    @staticmethod
    def _is_probe_suitable(model_id):
        lowered = model_id.lower()
        return not any(keyword in lowered for keyword in _SKIP_PROBE_KEYWORDS)

    def _probe_models(self, clients, model_entries):
        default = next(iter(clients.values()), None)
        result = []
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(model_entries) or 1)) as pool:
            futures = {
                pool.submit((clients.get(entry.get("api_key_id")) or default).probe, entry["id"]): entry
                for entry in model_entries
            }
            for future in as_completed(futures):
                entry = futures[future]
                try:
                    value = future.result(timeout=PROBE_TIMEOUT + 3)
                except Exception as exc:
                    value = {
                        "ok": False,
                        "status": "不可用",
                        "first_ms": None,
                        "total_ms": None,
                        "reply": "",
                        "error": str(exc),
                        "tested_at": utc_timestamp(),
                    }
                result.append((entry, value))
        return result

    def test_selected(self):
        ids = self.selected_model_ids()
        if not ids:
            QMessageBox.information(self, "测试模型", "请先选择模型")
            return
        self.test_models(ids)

    def test_all(self):
        project = self.project()
        if not project:
            return
        self.test_models([item["id"] for item in project["models"] if self._is_probe_suitable(item["id"])])

    def detect_all(self):
        snapshot = self._snapshot_clients()
        if not snapshot:
            return
        project_id, clients = snapshot

        def work():
            entries, errors = self._collect_models(clients)
            probes = [entry for entry in entries if self._is_probe_suitable(entry["id"])]
            return entries, self._probe_models(clients, probes), errors

        self.run_worker(
            lambda: work(), lambda value: self._apply_detected(project_id, value), "正在获取并检测全部模型..."
        )

    def _apply_detected(self, project_id, value):
        entries, results, errors = value
        project = self.project(project_id)
        if not project:
            return
        available = []
        for entry, result in results:
            if result.get("ok"):
                available.append(
                    {
                        "id": entry["id"],
                        "api_key_id": entry.get("api_key_id", ""),
                        **{key: val for key, val in result.items() if key != "ok"},
                        "route_name": "",
                    }
                )
        project["discovered_models"] = entries
        project["models"] = sorted(available, key=lambda item: item["id"].lower())
        self._save_store()
        self.refresh_models()
        self.set_status(f"检测完成：{len(available)}/{len(results)} 个模型可用")
        if errors:
            self._append_log("检测时部分密钥失败：" + "；".join(errors))

    def test_models(self, model_ids):
        snapshot = self._snapshot_clients()
        if not snapshot:
            return
        project_id, clients = snapshot
        project = self.project(project_id)
        by_id = {item["id"]: item for item in project.get("models", [])}
        entries = [
            {"id": model_id, "api_key_id": by_id.get(model_id, {}).get("api_key_id", "")} for model_id in model_ids
        ]

        def work():
            return self._probe_models(clients, entries)

        self.run_worker(
            work, lambda value: self._apply_probe_results(project_id, value), f"正在测试 {len(entries)} 个模型..."
        )

    def _apply_probe_results(self, project_id, results):
        project = self.project(project_id)
        if not project:
            return
        models = {item["id"]: item for item in project["models"]}
        for entry, result in results:
            if entry["id"] in models:
                models[entry["id"]].update({key: value for key, value in result.items() if key != "ok"})
        self._save_store()
        self.refresh_models()
        self.set_status(f"测试完成：{sum(1 for _, value in results if value.get('ok'))}/{len(results)} 个模型可用")

    def copy_text(self, text):
        QApplication.clipboard().setText(text)

    def copy_model_reply(self):
        ids = self.selected_model_ids()
        if not ids:
            return
        model = next((item for item in self.project()["models"] if item["id"] == ids[0]), None)
        if model:
            self.copy_text(str(model.get("reply") or model.get("error") or ""))

    def set_route_name(self):
        ids = self.selected_model_ids()
        if not ids:
            return
        project = self.project()
        model = next((item for item in project["models"] if item["id"] == ids[0]), None)
        if not model:
            return
        name, ok = QInputDialog.getText(self, "设置渠道模型名", "对外模型名：", text=model.get("route_name", ""))
        if ok:
            if "\n" in name or "\r" in name:
                QMessageBox.warning(self, "设置渠道模型名", "模型名不能包含换行")
                return
            model["route_name"] = name.strip()
            self._save_store()
            self.refresh_models()

    def assign_selected_key(self):
        ids = self.selected_model_ids()
        project = self.project()
        if not ids or not project:
            return
        keys = _project_keys(project)
        values = [api_key_label(key) for key in keys]
        selected, ok = QInputDialog.getItem(self, "设置密钥", "请选择模型使用的 API 密钥：", values, 0, False)
        if ok and selected:
            key_id = keys[values.index(selected)]["id"]
            for model in project["models"]:
                if model["id"] in ids:
                    model["api_key_id"] = key_id
            self._save_store()
            self.refresh_models()

    def remove_selected(self):
        ids = set(self.selected_model_ids())
        project = self.project()
        if not project or not ids:
            return
        project["models"] = [item for item in project["models"] if item["id"] not in ids]
        self._save_store()
        self.refresh_models()

    def remove_all_models(self):
        project = self.project()
        if not project or not project.get("models"):
            return
        if QMessageBox.question(self, "移除全部", "确定移除当前项目的全部模型吗？") != QMessageBox.Yes:
            return
        project["models"] = []
        self._save_store()
        self.refresh_models()

    def show_model_detail(self):
        ids = self.selected_model_ids()
        if not ids:
            return
        model = next((item for item in self.project()["models"] if item["id"] == ids[0]), None)
        if model:
            QMessageBox.information(
                self, str(model.get("id", "模型")), str(model.get("reply") or model.get("error") or "暂无返回内容")
            )

    def backup_config(self):
        self.commit_form()
        path, _ = QFileDialog.getSaveFileName(
            self, "备份配置", "ai_probe_projects_backup.json", "JSON 配置 (*.json);;所有文件 (*.*)"
        )
        if not path:
            return
        encrypted = QMessageBox.question(self, "备份格式", "是否使用 AES 加密导出？") == QMessageBox.Yes
        try:
            if encrypted:
                secret, ok = QInputDialog.getText(self, "设置备份密钥", "请输入备份密码：", QLineEdit.Password)
                if not ok or not secret.strip():
                    return
                StoreService.write_encrypted_file(Path(path), self.store, secret.strip())
            else:
                StoreService.write_plain_file(Path(path), self.store)
            self.set_status(f"备份完成：{Path(path).name}")
        except (OSError, RuntimeError) as exc:
            QMessageBox.critical(self, "备份失败", str(exc))

    def import_config(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入配置", "", "JSON 配置 (*.json);;所有文件 (*.*)")
        if (
            not path
            or QMessageBox.question(self, "导入配置", "导入后将替换当前全部项目，是否继续？") != QMessageBox.Yes
        ):
            return
        try:
            raw = Path(path).read_text(encoding="utf-8")
            payload = json.loads(raw)
            secret = None
            if isinstance(payload, dict) and payload.get("format"):
                secret, ok = QInputDialog.getText(self, "解密密钥", "请输入 AES 解密密码：", QLineEdit.Password)
                if not ok:
                    return
            data, imported_key = self.store_service.import_payload(Path(path), secret)
            self.store_service.activate_import(data, imported_key)
            self.config_key = self.store_service.config_key
            self.store = data
            self.current_id = self.store.get("selected_project_id")
            self._ensure_selection()
            self.refresh_project_list()
            self._load_current_project()
            if self.relay_server:
                # 中转路由缓存指向旧 store，导入后必须失效，否则仍按旧项目转发。
                self.relay_server.invalidate_routes()
            self._sync_relay_dialog()
            self.set_status(f"导入完成：{len(self.store['projects'])} 个项目")
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            QMessageBox.critical(self, "导入失败", str(exc))

    def open_relay(self):
        if self.relay_dialog is None:
            self.relay_dialog = QtRelayDialog(self)
        self.relay_dialog.refresh_projects()
        self.relay_dialog.show()
        self.relay_dialog.raise_()
        self.relay_dialog.activateWindow()

    def _sync_relay_dialog(self):
        """中转对话框可以一直开着，项目增删改后同步其中的启用接口列表。"""
        if self.relay_dialog is not None:
            self.relay_dialog.refresh_projects()
            self.relay_dialog.update_controls()

    def start_relay(self):
        if self.relay_server:
            return
        self.commit_form()
        if self.relay_dialog:
            self.relay_dialog.save_settings()
        relay = self.store.get("relay", {})
        enabled = set(relay.get("project_ids", []))
        models = sum(len(project.get("models", [])) for project in self.store["projects"] if project["id"] in enabled)
        if not enabled or not models:
            QMessageBox.information(self, "本地中转", "请至少启用一个已添加模型的 AI 接口")
            return
        try:
            self.relay_server = RelayServer(
                self,
                relay.get("host", "127.0.0.1"),
                int(relay.get("port", 8040)),
                str(relay.get("api_key", "")),
                bool(relay.get("error_logging_enabled", True)),
                bool(relay.get("request_logging_enabled", True)),
                bool(relay.get("request_debug_capture", False)),
                system_prompt=str(relay.get("system_prompt", "")),
                append_user_prompt=bool(relay.get("append_user_prompt", True)),
                user_agent=str(relay.get("user_agent", "")),
            )
            self.relay_server.start()
            self.set_status(f"本地中转已启动：http://{self.relay_server.host}:{self.relay_server.port}/v1")
            if self.relay_dialog:
                self.relay_dialog.update_controls()
        except OSError as exc:
            self.relay_server = None
            QMessageBox.critical(self, "本地中转", str(exc))

    def stop_relay(self):
        if self.relay_server:
            self.relay_server.stop()
            self.relay_server = None
            self.set_status("本地中转已停止")
        if self.relay_dialog:
            self.relay_dialog.update_controls()

    def schedule_relay_save(self):
        self.schedule_save()

    def open_log_dir(self):
        path = RELAY_ERROR_LOG.parent
        path.mkdir(parents=True, exist_ok=True)
        import os

        os.startfile(str(path)) if sys.platform == "win32" else None

    def set_status(self, text):
        self.status_label.setText(str(text))
        self.statusBar().showMessage(str(text))

    def _append_log(self, message):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.appendPlainText(f"[{stamp}] {message}")

    def _post(self, callback, *args):
        self.post_requested.emit(callback, args)

    def _dispatch_post(self, callback, args):
        callback(*args)

    def _log(self, message):
        self.log_requested.emit(str(message))

    def record_relay_usage(self, project, model, input_tokens, output_tokens, cached_tokens):
        self.usage_stats.record(project, model, input_tokens, output_tokens, cached_tokens)

    def restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def minimize_to_tray(self):
        if self.tray_available:
            self.hide()
        else:
            self.showMinimized()

    def switch_lightweight(self, enabled):
        if not enabled:
            return
        self.commit_form()
        self._save_store()
        self.stop_relay()
        from .process import restart_application

        if restart_application(True):
            self.close()

    def closeEvent(self, event):
        self._save_timer.stop()
        self.commit_form()
        self.usage_stats.save()
        self.stop_relay()
        if self.relay_dialog:
            self.relay_dialog.close()
        if getattr(self, "tray", None):
            self.tray.hide()
        event.accept()


# Backwards-compatible name for code that imported the old application class.
ProbeApp = QtMainWindow
