"""主窗口的组装与应用生命周期。"""

from __future__ import annotations

import queue
from tkinter import BooleanVar, StringVar, Tk, ttk

from ..config import USAGE_FILE
from ..usage import UsageStats
from .layout_mixin import LayoutMixin
from .models_mixin import ModelsMixin
from .projects_mixin import ProjectsMixin
from .relay_mixin import RelayMixin
from .store_mixin import StoreMixin
from .widgets import MacButton


class ProbeApp(LayoutMixin, RelayMixin, StoreMixin, ProjectsMixin, ModelsMixin):
    def __init__(self, root: Tk, config_key: bytes):
        self.root = root
        self.events = queue.Queue()
        self.config_key = config_key
        self.store = self._load_store()
        self.usage_stats = UsageStats(USAGE_FILE)
        self.current_id = self.store.get("selected_project_id")
        self.loading_form = False
        self.updating_projects = False
        self.save_timer = None
        self.probe_save_after = None
        self.busy = False
        self.network_buttons = []
        self.tree_model_ids = {}
        self.model_tree_items = {}
        self.remote_model_entries = []
        self.next_model_tree_item = 0
        self.visible_project_ids = []
        self.header_rows = []
        self.active_headers_mode = "json"
        self.advanced_settings_visible = False
        self.context_model_id = None
        self.context_project_id = None
        self.relay_server = None
        self.relay_window = None
        self.relay_project_vars = {}
        self.relay_stats_tree = None
        self.relay_stats_total = StringVar(value="-")
        self.relay_stats_cache_rate = StringVar(value="-")
        self.relay_stats_input = StringVar(value="-")
        self.relay_stats_output = StringVar(value="-")

        self.project_name = StringVar()
        self.project_search = StringVar()
        self.base_url = StringVar()
        self.api_key = StringVar()
        self.proxy_url = StringVar()
        self.skip_ssl_verify = BooleanVar(value=False)
        self.api_mode = StringVar(value="chat")
        self.headers_mode = StringVar(value="json")
        self.show_key = StringVar(value="0")
        self.custom_model_id = StringVar()
        self.status = StringVar(value="就绪")
        self.discovered_count = StringVar(value="远程模型 0")
        self.added_count = StringVar(value="已添加 0")
        relay = self.store.get("relay", {})
        self.relay_host = StringVar(value=str(relay.get("host", "127.0.0.1")))
        self.relay_port = StringVar(value=str(relay.get("port", 8040)))
        self.relay_key = StringVar(value=str(relay.get("api_key", "")))
        self.relay_error_logging_enabled = BooleanVar(value=bool(relay.get("error_logging_enabled", True)))
        self.relay_status = StringVar(value="未启动")
        self.relay_url = StringVar(value="")
        self.relay_key.trace_add("write", self._relay_key_changed)

        self._configure_window()
        self._build_ui()
        self.status.trace_add("write", self._update_status_style)
        for variable in (
            self.project_name,
            self.base_url,
            self.api_key,
            self.proxy_url,
            self.skip_ssl_verify,
            self.api_mode,
        ):
            variable.trace_add("write", self._schedule_save)
        self._ensure_selection()
        self._refresh_project_list()
        if getattr(self, "_loaded_plaintext", False):
            self._save_store()
        self.root.after(80, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_window(self):
        self.root.title("AI Probe · 多项目测活 · 作者QQ168889526")
        self.root.geometry("1320x820")
        self.root.minsize(1040, 720)
        self.root.configure(bg="#f2f2f7")

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f2f2f7")
        style.configure("Main.TFrame", background="#f2f2f7")
        style.configure("Toolbar.TFrame", background="#ffffff")
        style.configure("Sidebar.TFrame", background="#f1f2f5")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("Segment.TFrame", background="#eef0f4")
        style.configure("TLabel", background="#f2f2f7", foreground="#1d1d1f", font=("Microsoft YaHei UI", 9))
        style.configure("Panel.TLabel", background="#ffffff", foreground="#1d1d1f", font=("Microsoft YaHei UI", 9))
        style.configure(
            "Title.TLabel", background="#ffffff", foreground="#111113", font=("Microsoft YaHei UI", 15, "bold")
        )
        style.configure(
            "ToolbarTitle.TLabel", background="#ffffff", foreground="#111113", font=("Microsoft YaHei UI", 11, "bold")
        )
        style.configure(
            "ToolbarMuted.TLabel", background="#ffffff", foreground="#8b8b91", font=("Microsoft YaHei UI", 8)
        )
        style.configure(
            "SidebarTitle.TLabel", background="#f1f2f5", foreground="#111113", font=("Microsoft YaHei UI", 15, "bold")
        )
        style.configure(
            "Section.TLabel", background="#ffffff", foreground="#111113", font=("Microsoft YaHei UI", 10, "bold")
        )
        style.configure("Muted.TLabel", background="#ffffff", foreground="#6e6e73", font=("Microsoft YaHei UI", 8))
        style.configure(
            "StatsValue.TLabel", background="#ffffff", foreground="#111113", font=("Microsoft YaHei UI", 11, "bold")
        )
        style.configure(
            "SidebarMuted.TLabel", background="#f1f2f5", foreground="#8b8b91", font=("Microsoft YaHei UI", 8)
        )
        style.configure(
            "TButton",
            padding=(11, 7),
            font=("Microsoft YaHei UI", 9),
            background="#ffffff",
            foreground="#1d1d1f",
            borderwidth=0,
            relief="flat",
        )
        style.map(
            "TButton",
            background=[("pressed", "#d9d9de"), ("active", "#e8e8ed"), ("disabled", "#f0f0f2")],
            foreground=[("disabled", "#a1a1a6")],
        )
        style.configure("Accent.TButton", background="#007aff", foreground="#ffffff")
        style.map(
            "Accent.TButton",
            background=[("pressed", "#005ecb"), ("active", "#006fe6"), ("disabled", "#9bc8f7")],
            foreground=[("disabled", "#f4f8ff")],
        )
        style.configure("Danger.TButton", foreground="#c9342b")
        style.map("Danger.TButton", foreground=[("active", "#a51f18"), ("disabled", "#e1aaa6")])
        style.configure("TRadiobutton", background="#ffffff", foreground="#1d1d1f", font=("Microsoft YaHei UI", 9))
        style.configure(
            "Segment.TRadiobutton",
            background="#eef0f4",
            foreground="#6e6e73",
            padding=(10, 5),
            font=("Microsoft YaHei UI", 9),
        )
        style.map(
            "Segment.TRadiobutton",
            background=[("selected", "#ffffff"), ("active", "#e3e5e9")],
            foreground=[("selected", "#1d1d1f")],
        )
        # Keep keyboard focus usable, but remove the theme's dotted focus ring from segmented controls.
        style.layout(
            "Segment.TRadiobutton",
            [
                (
                    "Radiobutton.padding",
                    {
                        "sticky": "nswe",
                        "children": [
                            ("Radiobutton.indicator", {"side": "left", "sticky": ""}),
                            ("Radiobutton.label", {"side": "left", "sticky": "nswe"}),
                        ],
                    },
                )
            ],
        )
        style.configure("TCheckbutton", background="#ffffff", foreground="#1d1d1f", font=("Microsoft YaHei UI", 9))
        style.configure("TEntry", padding=(7, 5), fieldbackground="#fbfbfd", foreground="#1d1d1f")
        style.configure(
            "Treeview",
            rowheight=30,
            font=("Microsoft YaHei UI", 9),
            background="#ffffff",
            fieldbackground="#ffffff",
            foreground="#1d1d1f",
            borderwidth=0,
        )
        style.map("Treeview", background=[("selected", "#dbeeff")], foreground=[("selected", "#0a4b8f")])
        style.configure(
            "Treeview.Heading",
            background="#f5f5f7",
            foreground="#6e6e73",
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=(7, 8),
            relief="flat",
        )
        style.configure(
            "Status.TLabel",
            background="#e8f6f1",
            foreground="#087f6c",
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=(9, 5),
        )
        style.configure(
            "StatusBusy.TLabel",
            background="#e8f1fc",
            foreground="#0064c8",
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=(9, 5),
        )
        style.configure(
            "StatusError.TLabel",
            background="#fff0ef",
            foreground="#b3261e",
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=(9, 5),
        )
        style.configure(
            "StatusSuccess.TLabel",
            background="#e8f6f1",
            foreground="#087f23",
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=(9, 5),
        )

    @staticmethod
    def _mac_button(parent, text, command, kind="secondary", surface="#ffffff", **kwargs):
        return MacButton(parent, text=text, command=command, kind=kind, surface=surface, **kwargs)
