"""Lightweight process that keeps only the local relay and system tray alive."""

from __future__ import annotations

import logging
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from tkinter import Tk

from .config import DATA_FILE, USAGE_FILE, decrypt_config, encrypt_config
from .relay import RelayServer
from .tray import TrayController
from .ui.store_mixin import StoreMixin
from .usage import UsageStats


def restart_application(lightweight: bool) -> bool:
    """Start the alternate process mode without inheriting the current UI."""

    compiled = getattr(sys, "frozen", False) or globals().get("__compiled__") is not None
    command = [sys.executable] if compiled else [sys.executable, "-m", "ai_probe"]
    if lightweight:
        command.append("--lightweight")
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(command, cwd=str(Path.cwd()), close_fds=True, creationflags=flags)
    except OSError:
        return False
    return True


class RelayOnlyApp:
    """Small adapter exposing only the callbacks required by ``RelayServer``."""

    def __init__(self, root: Tk, config_key: bytes):
        self.root = root
        self.config_key = config_key
        self.store = self._load_store()
        self.usage_stats = UsageStats(USAGE_FILE)
        self.relay_server = None
        self.tray = TrayController(
            root,
            on_restore=self._restore_full_app,
            on_lightweight=self._lightweight_changed,
            on_close=self._on_close,
        )
        self.tray.lightweight_mode = True
        if not self.tray.start():
            raise RuntimeError("轻量模式需要 Windows 系统托盘支持，请安装 pystray 和 Pillow")
        self.root.withdraw()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._start_relay()

    def _load_store(self) -> dict:
        try:
            raw = DATA_FILE.read_text(encoding="utf-8")
            data, _encrypted = decrypt_config(raw, self.config_key, allow_legacy=False)
            return StoreMixin._normalize_store(data)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            raise RuntimeError(f"无法载入配置：{exc}") from exc

    def _save_store(self) -> None:
        temp = DATA_FILE.with_name(f".{DATA_FILE.name}.tmp")
        try:
            temp.write_text(encrypt_config(self.store, self.config_key), encoding="utf-8")
            temp.replace(DATA_FILE)
        except OSError:
            temp.unlink(missing_ok=True)

    def _start_relay(self) -> None:
        relay = self.store.get("relay", {})
        enabled = set(relay.get("project_ids", []))
        model_count = sum(
            len(project.get("models", []))
            for project in self.store.get("projects", [])
            if project.get("id") in enabled
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
            )
            self.relay_server.start()
            self._log(f"轻量模式已启动本地中转：{self.relay_server.host}:{self.relay_server.port}")
        except (OSError, ValueError) as exc:
            self.relay_server = None
            self._log(f"轻量模式启动中转失败：{exc}")

    def _stop_relay(self) -> None:
        if self.relay_server:
            self.relay_server.stop()
            self.relay_server = None

    def _post(self, callback, *args) -> None:
        with suppress(Exception):
            self.root.after(0, callback, *args)

    @staticmethod
    def _log(message: str) -> None:
        logging.getLogger("ai_probe.relay").info(message)

    def record_relay_usage(self, project, model, input_tokens, output_tokens, cached_tokens):
        self.usage_stats.record(project, model, input_tokens, output_tokens, cached_tokens)

    def _restore_full_app(self) -> None:
        self._switch_mode(False)

    def _lightweight_changed(self, enabled: bool) -> None:
        if not enabled:
            self._switch_mode(False)

    def _switch_mode(self, lightweight: bool) -> None:
        self._stop_relay()
        self.usage_stats.save()
        self.tray.stop()
        if not restart_application(lightweight):
            self._start_relay()
            return
        self.root.destroy()

    def _on_close(self) -> None:
        self._stop_relay()
        self.usage_stats.save()
        self.tray.stop()
        self.root.destroy()
