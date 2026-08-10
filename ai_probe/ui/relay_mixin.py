"""本地转发窗口与用量视图。"""

from __future__ import annotations

from tkinter import VERTICAL, BooleanVar, Canvas, Toplevel, messagebox, ttk

from ..projects import _project_keys
from ..relay import RelayServer
from ..utils import compact_number, format_cache_summary


class RelayMixin:
    def _open_relay_window(self):
        self._commit_form()
        if self.relay_window and self.relay_window.winfo_exists():
            self._refresh_relay_projects()
            self._refresh_relay_stats()
            self.relay_window.deiconify()
            self.relay_window.lift()
            self.relay_window.focus_force()
            return

        window = Toplevel(self.root)
        self.relay_window = window
        window.title("订阅与本地中转")
        window.geometry("720x720")
        window.minsize(680, 560)
        window.configure(bg="#f2f2f7")
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self._close_relay_window)
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(1, weight=1)

        header = ttk.Frame(window, style="Toolbar.TFrame", padding=(16, 12))
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ttk.Label(header, text="订阅与本地中转", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.relay_status, style="ToolbarMuted.TLabel").grid(row=0, column=1, sticky="e")

        notebook = ttk.Notebook(window)
        notebook.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))
        config_tab = ttk.Frame(notebook, style="Main.TFrame")
        stats_tab = ttk.Frame(notebook, style="Main.TFrame", padding=12)
        notebook.add(config_tab, text="中转配置")
        notebook.add(stats_tab, text="今日统计")

        config_tab.grid_columnconfigure(0, weight=1)
        config_tab.grid_rowconfigure(1, weight=1)
        settings = ttk.Frame(config_tab, style="Panel.TFrame", padding=14)
        settings.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        settings.grid_columnconfigure(1, weight=1)
        settings.grid_columnconfigure(3, weight=1)
        ttk.Label(settings, text="监听地址", style="Panel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(settings, textvariable=self.relay_host).grid(row=0, column=1, sticky="ew", padx=(0, 14))
        ttk.Label(settings, text="本地端口", style="Panel.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 8))
        ttk.Entry(settings, textvariable=self.relay_port, width=10).grid(row=0, column=3, sticky="ew")
        ttk.Label(settings, text="访问密钥", style="Panel.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=(9, 0)
        )
        ttk.Entry(settings, textvariable=self.relay_key, show="*").grid(
            row=1, column=1, columnspan=3, sticky="ew", pady=(9, 0)
        )
        ttk.Label(settings, text="留空表示不校验访问密钥", style="Muted.TLabel").grid(
            row=2, column=1, columnspan=3, sticky="w", pady=(3, 0)
        )

        projects_panel = ttk.Frame(config_tab, style="Panel.TFrame", padding=14)
        projects_panel.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        projects_panel.grid_columnconfigure(0, weight=1)
        projects_panel.grid_rowconfigure(2, weight=1)
        projects_header = ttk.Frame(projects_panel, style="Panel.TFrame")
        projects_header.grid(row=0, column=0, sticky="ew")
        projects_header.grid_columnconfigure(0, weight=1)
        ttk.Label(projects_header, text="启用的 AI 接口", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self._mac_button(projects_header, "全选", self._select_all_relay_projects, surface="#ffffff", width=5).grid(
            row=0, column=1, padx=(8, 4)
        )
        self._mac_button(projects_header, "清空", self._clear_relay_projects, surface="#ffffff", width=5).grid(
            row=0, column=2
        )
        ttk.Label(projects_panel, text="同名模型会自动轮询，并使用每个模型绑定的密钥", style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(3, 8)
        )

        list_host = ttk.Frame(projects_panel, style="Panel.TFrame")
        list_host.grid(row=2, column=0, sticky="nsew")
        list_host.grid_columnconfigure(0, weight=1)
        list_host.grid_rowconfigure(0, weight=1)
        self.relay_projects_canvas = Canvas(
            list_host, borderwidth=0, highlightthickness=1, highlightbackground="#d6d6dc", background="#fbfbfd"
        )
        relay_scroll = ttk.Scrollbar(list_host, orient=VERTICAL, command=self.relay_projects_canvas.yview)
        self.relay_projects_canvas.configure(yscrollcommand=relay_scroll.set)
        self.relay_projects_canvas.grid(row=0, column=0, sticky="nsew")
        relay_scroll.grid(row=0, column=1, sticky="ns")
        self.relay_projects_frame = ttk.Frame(self.relay_projects_canvas, style="Panel.TFrame", padding=(8, 5))
        self.relay_projects_window = self.relay_projects_canvas.create_window(
            (0, 0), window=self.relay_projects_frame, anchor="nw"
        )
        self.relay_projects_canvas.bind(
            "<Configure>",
            lambda event: self.relay_projects_canvas.itemconfigure(self.relay_projects_window, width=event.width),
        )
        self.relay_projects_frame.bind(
            "<Configure>",
            lambda _event: self.relay_projects_canvas.configure(scrollregion=self.relay_projects_canvas.bbox("all")),
        )
        self._bind_relay_project_wheel(self.relay_projects_canvas)
        self._bind_relay_project_wheel(self.relay_projects_frame)
        self._refresh_relay_projects()

        stats_tab.grid_columnconfigure(0, weight=1)
        stats_tab.grid_rowconfigure(1, weight=1)
        metrics = ttk.Frame(stats_tab, style="Main.TFrame")
        metrics.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        metrics.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="stat")
        self._relay_stat_card(metrics, "当日总用量", self.relay_stats_total, 0)
        self._relay_stat_card(metrics, "缓存", self.relay_stats_cache_rate, 1)
        self._relay_stat_card(metrics, "总输入", self.relay_stats_input, 2)
        self._relay_stat_card(metrics, "总输出", self.relay_stats_output, 3)

        leader_panel = ttk.Frame(stats_tab, style="Panel.TFrame", padding=14)
        leader_panel.grid(row=1, column=0, sticky="nsew")
        leader_panel.grid_columnconfigure(0, weight=1)
        leader_panel.grid_rowconfigure(1, weight=1)
        leader_header = ttk.Frame(leader_panel, style="Panel.TFrame")
        leader_header.grid(row=0, column=0, sticky="ew")
        leader_header.grid_columnconfigure(0, weight=1)
        ttk.Label(leader_header, text="模型用量排行榜", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(leader_header, text="AI项目-模型名字", style="Muted.TLabel").grid(row=0, column=1, sticky="e")
        self._mac_button(leader_header, "清空", self._clear_relay_stats, surface="#ffffff", width=5).grid(
            row=0, column=2, padx=(8, 0)
        )
        tree_host = ttk.Frame(leader_panel, style="Panel.TFrame")
        tree_host.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        tree_host.grid_columnconfigure(0, weight=1)
        tree_host.grid_rowconfigure(0, weight=1)
        columns = ("rank", "model", "input", "output", "total", "cache")
        self.relay_stats_tree = ttk.Treeview(tree_host, columns=columns, show="headings", height=8)
        self.relay_stats_tree.tag_configure("cache_low", foreground="#c9342b")
        self.relay_stats_tree.tag_configure("cache_high", foreground="#087f23")
        headings = {
            "rank": ("排名", 44, "center"),
            "model": ("AI项目-模型名字", 210, "w"),
            "input": ("输入", 72, "e"),
            "output": ("输出", 72, "e"),
            "total": ("总用量", 78, "e"),
            "cache": ("缓存率", 98, "e"),
        }
        for column, (text, width, anchor) in headings.items():
            self.relay_stats_tree.heading(column, text=text)
            self.relay_stats_tree.column(column, width=width, anchor=anchor, stretch=column in {"model"})
        stats_scroll = ttk.Scrollbar(tree_host, orient=VERTICAL, command=self.relay_stats_tree.yview)
        self.relay_stats_tree.configure(yscrollcommand=stats_scroll.set)
        self.relay_stats_tree.grid(row=0, column=0, sticky="nsew")
        stats_scroll.grid(row=0, column=1, sticky="ns")
        self._refresh_relay_stats()

        footer = ttk.Frame(window, style="Panel.TFrame", padding=14)
        footer.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        footer.grid_columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.relay_url, style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self._mac_button(footer, "复制地址", self._copy_relay_url, surface="#ffffff").grid(row=0, column=1, padx=(8, 4))
        self.relay_stop_button = self._mac_button(
            footer, "停止", self._stop_relay, kind="danger", surface="#ffffff", width=6
        )
        self.relay_stop_button.grid(row=0, column=2, padx=(4, 4))
        self.relay_start_button = self._mac_button(
            footer, "启动中转", self._start_relay, kind="primary", surface="#ffffff"
        )
        self.relay_start_button.grid(row=0, column=3, padx=(4, 0))
        self._update_relay_controls()

    def _bind_relay_project_wheel(self, widget):
        widget.bind("<MouseWheel>", self._scroll_relay_projects)
        widget.bind("<Button-4>", self._scroll_relay_projects)
        widget.bind("<Button-5>", self._scroll_relay_projects)

    def _scroll_relay_projects(self, event):
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        self.relay_projects_canvas.yview_scroll(delta, "units")
        return "break"

    def _close_relay_window(self):
        if self.relay_window:
            self.relay_window.destroy()
        self.relay_window = None

    def _refresh_relay_projects(self):
        if not self.relay_window or not self.relay_window.winfo_exists():
            return
        for child in self.relay_projects_frame.winfo_children():
            child.destroy()
        enabled = set(self.store.get("relay", {}).get("project_ids", []))
        self.relay_project_vars = {}
        for row, project in enumerate(self.store.get("projects", [])):
            variable = BooleanVar(value=project.get("id") in enabled)
            self.relay_project_vars[project["id"]] = variable
            model_count = len(project.get("models", []))
            key_count = len(_project_keys(project))
            text = f"{project.get('name', '未命名项目')}  ·  {key_count} 个密钥  ·  {model_count} 个模型  ·  {project.get('api_mode', 'chat')}"
            check = ttk.Checkbutton(
                self.relay_projects_frame,
                text=text,
                variable=variable,
                command=self._relay_selection_changed,
            )
            self._bind_relay_project_wheel(check)
            check.grid(row=row, column=0, sticky="w", pady=4)

    def _select_all_relay_projects(self):
        for variable in self.relay_project_vars.values():
            variable.set(True)
        self._relay_selection_changed()

    def _clear_relay_projects(self):
        for variable in self.relay_project_vars.values():
            variable.set(False)
        self._relay_selection_changed()

    def _relay_selection_changed(self):
        if not self.relay_project_vars:
            return
        relay = self.store.setdefault("relay", {})
        relay["project_ids"] = [
            project_id for project_id, variable in self.relay_project_vars.items() if variable.get()
        ]
        self._save_store()
        if self.relay_server:
            model_count = sum(
                len(project.get("models", []))
                for project in self.store.get("projects", [])
                if project.get("id") in set(relay["project_ids"])
            )
            self.relay_status.set(f"运行中 · {model_count} 个模型")

    def _relay_key_changed(self, *_):
        key = self.relay_key.get().strip()
        self.store.setdefault("relay", {})["api_key"] = key
        if self.relay_server:
            self.relay_server.auth_key = key
        self._save_store()

    def _save_relay_config(self) -> bool:
        host = self.relay_host.get().strip() or "127.0.0.1"
        try:
            port = int(self.relay_port.get().strip())
        except ValueError:
            messagebox.showerror("本地中转", "端口必须是数字", parent=self.relay_window)
            return False
        if not 1 <= port <= 65535:
            messagebox.showerror("本地中转", "端口必须在 1 到 65535 之间", parent=self.relay_window)
            return False
        project_ids = [project_id for project_id, variable in self.relay_project_vars.items() if variable.get()]
        self.store["relay"] = {
            "host": host,
            "port": port,
            "api_key": self.relay_key.get().strip(),
            "project_ids": project_ids,
        }
        self.relay_host.set(host)
        self.relay_port.set(str(port))
        self._save_store()
        return True

    def _start_relay(self):
        if self.relay_server:
            return
        self._commit_form()
        if not self._save_relay_config():
            return
        relay = self.store["relay"]
        enabled = set(relay["project_ids"])
        model_count = sum(
            len(project.get("models", [])) for project in self.store["projects"] if project["id"] in enabled
        )
        if not enabled:
            messagebox.showinfo("本地中转", "请至少启用一个 AI 接口", parent=self.relay_window)
            return
        if not model_count:
            messagebox.showinfo("本地中转", "启用的接口中还没有已添加模型", parent=self.relay_window)
            return
        try:
            server = RelayServer(self, relay["host"], relay["port"], relay["api_key"])
            server.start()
        except OSError as exc:
            messagebox.showerror(
                "本地中转", f"无法监听 {relay['host']}:{relay['port']}\n{exc}", parent=self.relay_window
            )
            return
        self.relay_server = server
        display_host = "127.0.0.1" if relay["host"] in {"0.0.0.0", "::"} else relay["host"]
        self.relay_url.set(f"OpenAI Base URL：http://{display_host}:{server.port}/v1")
        self.relay_status.set(f"运行中 · {model_count} 个模型")
        self._update_relay_controls()
        self.status.set(f"本地中转已启动：http://{display_host}:{server.port}/v1")
        self._log(f"本地中转启动：{display_host}:{server.port}，{model_count} 个模型")

    def _stop_relay(self):
        server = self.relay_server
        if not server:
            return
        self.relay_server = None
        server.stop()
        self.relay_status.set("未启动")
        self.relay_url.set("")
        self._update_relay_controls()
        self.status.set("本地中转已停止")
        self._log("本地中转停止")

    def _copy_relay_url(self):
        value = self.relay_url.get()
        if not value:
            return
        url = value.split("：", 1)[-1]
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.root.update_idletasks()
        self.status.set(f"已复制中转地址：{url}")

    def _update_relay_controls(self):
        if not self.relay_window or not self.relay_window.winfo_exists():
            return
        running = self.relay_server is not None
        self.relay_start_button.state(["disabled"] if running else ["!disabled"])
        self.relay_stop_button.state(["!disabled"] if running else ["disabled"])

    def _relay_stat_card(self, parent, title, variable, column):
        card = ttk.Frame(parent, style="Panel.TFrame", padding=(14, 12))
        card.grid(row=0, column=column, sticky="ew", padx=(0, 8) if column < 3 else 0)
        ttk.Label(card, text=title, style="Muted.TLabel").pack(anchor="w")
        ttk.Label(card, textvariable=variable, style="StatsValue.TLabel").pack(anchor="w", pady=(6, 0))

    def _refresh_relay_stats(self):
        if not self.relay_window or not self.relay_window.winfo_exists():
            return
        day = self.usage_stats.snapshot()
        input_tokens = day.get("input_tokens", 0)
        output_tokens = day.get("output_tokens", 0)
        cached_tokens = day.get("cached_tokens", 0)
        self.relay_stats_total.set(compact_number(input_tokens + output_tokens))
        self.relay_stats_input.set(compact_number(input_tokens))
        self.relay_stats_output.set(compact_number(output_tokens))
        self.relay_stats_cache_rate.set(format_cache_summary(input_tokens, cached_tokens))
        if self.relay_stats_tree is None or not self.relay_stats_tree.winfo_exists():
            return
        for item in self.relay_stats_tree.get_children():
            self.relay_stats_tree.delete(item)
        projects_by_id = {project.get("id"): project.get("name", "") for project in self.store.get("projects", [])}
        entries = []
        for entry in day.get("models", {}).values():
            entries.append((entry.get("input_tokens", 0) + entry.get("output_tokens", 0), entry))
        for rank, (_total, entry) in enumerate(sorted(entries, key=lambda item: item[0], reverse=True), start=1):
            project_name = projects_by_id.get(entry.get("project_id"), entry.get("project_name", ""))
            label = f"{project_name}-{entry.get('model', '')}"
            entry_input = entry.get("input_tokens", 0)
            cache_percent = entry.get("cached_tokens", 0) / entry_input * 100 if entry_input else None
            cache_rate = f"{cache_percent:.1f}%" if cache_percent is not None else "-"
            if cache_percent is not None and cache_percent >= 90:
                tags = ("cache_high",)
            elif cache_percent is not None and cache_percent < 80:
                tags = ("cache_low",)
            else:
                tags = ()
            self.relay_stats_tree.insert(
                "",
                "end",
                tags=tags,
                values=(
                    rank,
                    label,
                    compact_number(entry_input),
                    compact_number(entry.get("output_tokens", 0)),
                    compact_number(_total),
                    cache_rate,
                ),
            )

    def record_relay_usage(self, project, model, input_tokens, output_tokens, cached_tokens):
        self.usage_stats.record(project, model, input_tokens, output_tokens, cached_tokens)
        self._post(self._refresh_relay_stats)

    def _clear_relay_stats(self):
        if not messagebox.askyesno("今日统计", "确定清空今日全部用量统计吗？", parent=self.relay_window):
            return
        self.usage_stats.clear_today()
        self._refresh_relay_stats()
        self._log("已清空今日中转用量统计")

    def _update_status_style(self, *_):
        value = self.status.get()
        if value.startswith(("失败", "保存失败")):
            style = "StatusError.TLabel"
        elif value.startswith("正在") or "：" in value and "/" in value:
            style = "StatusBusy.TLabel"
        elif value.startswith(("完成", "获取完成", "检测完成", "测试完成")):
            style = "StatusSuccess.TLabel"
        else:
            style = "Status.TLabel"
        self.status_label.configure(style=style)
