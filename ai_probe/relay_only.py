"""Lightweight Qt process that keeps only the local relay and tray alive."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .config import USAGE_FILE
from .relay import RelayServer
from .store_service import StoreService
from .usage import UsageStats


def restart_application(lightweight: bool) -> bool:
    """Start the alternate process mode without inheriting the current UI."""

    compiled = getattr(sys, "frozen", False) or globals().get("__compiled__") is not None
    command = [sys.executable] if compiled else [sys.executable, "-m", "ai_probe"]
    if lightweight:
        command.append("--lightweight")
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200
    try:
        subprocess.Popen(command, cwd=str(Path.cwd()), close_fds=True, creationflags=flags)
    except OSError:
        return False
    return True


class RelayOnlyApp(QObject):
    """Qt adapter exposing the callbacks required by ``RelayServer``."""

    def __init__(self, application: QApplication, config_key: bytes):
        super().__init__()
        self.application = application
        self.config_key = config_key
        self.store_service = StoreService(config_key)
        self.store = self.store_service.load()
        self.usage_stats = UsageStats(USAGE_FILE)
        self.relay_server = None
        self._build_tray()
        self._start_relay()

    def _build_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self._make_icon())
        menu = QMenu()
        restore = QAction("退出轻量模式", self)
        restore.triggered.connect(lambda: self._switch_mode(False))
        menu.addAction(restore)
        close = QAction("退出", self)
        close.triggered.connect(self._on_close)
        menu.addAction(close)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self._show_message() if reason == QSystemTrayIcon.Trigger else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    @staticmethod
    def _make_icon():
        pixmap = QPixmap(64, 64)
        pixmap.fill(0)
        painter = QPainter(pixmap)
        painter.setBrush(QColor("#0b74de"))
        painter.drawRoundedRect(4, 4, 56, 56, 12, 12)
        painter.setBrush(QColor("white"))
        painter.drawRoundedRect(18, 13, 28, 38, 5, 5)
        painter.end()
        return QIcon(pixmap)

    def _load_store(self):
        return self.store_service.load()

    def _start_relay(self):
        relay = self.store.get("relay", {})
        enabled = set(relay.get("project_ids", []))
        model_count = sum(
            len(project.get("models", [])) for project in self.store.get("projects", []) if project.get("id") in enabled
        )
        if not enabled or not model_count:
            self._log("轻量模式未启动中转：请先在主界面启用接口并添加模型")
            return
        try:
            self.relay_server = RelayServer(
                self,
                str(relay.get("host", "127.0.0.1")),
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
            self._log(f"轻量模式已启动本地中转：{self.relay_server.host}:{self.relay_server.port}")
        except (OSError, ValueError) as exc:
            self.relay_server = None
            self._log(f"轻量模式启动中转失败：{exc}")

    def _stop_relay(self):
        if self.relay_server:
            self.relay_server.stop()
            self.relay_server = None

    def _post(self, callback, *args):
        callback(*args)

    @staticmethod
    def _log(message: str):
        logging.getLogger("ai_probe.relay").info(message)

    def record_relay_usage(self, project, model, input_tokens, output_tokens, cached_tokens):
        self.usage_stats.record(project, model, input_tokens, output_tokens, cached_tokens)

    def _show_message(self):
        if self.tray.supportsMessages():
            self.tray.showMessage("AI Probe", "本地中转仍在后台运行")

    def _switch_mode(self, lightweight: bool):
        self._stop_relay()
        self.usage_stats.save()
        if not restart_application(lightweight):
            self._start_relay()
            return
        self.tray.hide()
        self.application.quit()

    def _on_close(self):
        self._stop_relay()
        self.usage_stats.save()
        self.tray.hide()
        self.application.quit()
