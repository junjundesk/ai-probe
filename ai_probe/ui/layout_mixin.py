"""主窗口布局构建。"""

from __future__ import annotations

from tkinter import EXTENDED, HORIZONTAL, VERTICAL, Canvas, Listbox, Menu, Text, ttk

from ..config import TEST_PROMPT


class LayoutMixin:
    def _build_ui(self):
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(1, weight=1)

        self.remote_model_context_menu = Menu(self.root, tearoff=False)
        self.remote_model_context_menu.add_command(label="复制模型名称", command=self._copy_context_model)
        self.project_model_context_menu = Menu(self.root, tearoff=False)
        self.project_model_context_menu.add_command(label="复制模型名称", command=self._copy_context_model)
        self.project_model_context_menu.add_separator()
        self.project_model_context_menu.add_command(label="设置密钥...", command=self._assign_selected_model_key)
        self.project_model_context_menu.add_separator()
        self.project_model_context_menu.add_command(label="移除当前模型", command=self._remove_context_model)
        self.project_model_context_menu.add_command(label="移除全部模型", command=self._remove_all)
        self.project_context_menu = Menu(self.root, tearoff=False)
        self.project_context_menu.add_command(label="新增项目", command=self._new_project)
        self.project_context_menu.add_command(label="重命名项目", command=self._rename_context_project)
        self.project_context_menu.add_command(label="复制项目", command=self._copy_context_project)
        self.project_context_menu.add_command(label="删除当前项目", command=self._delete_context_project)
        self.project_context_menu.add_separator()
        self.project_context_menu.add_command(label="删除全部项目", command=self._delete_all_projects)

        toolbar = ttk.Frame(self.root, style="Toolbar.TFrame", padding=(14, 7))
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
        toolbar.grid_columnconfigure(1, weight=1)
        traffic = Canvas(toolbar, width=48, height=18, background="#ffffff", highlightthickness=0)
        traffic.grid(row=0, column=0, sticky="w", padx=(0, 10))
        for index, color in enumerate(("#ff5f57", "#febc2e", "#28c840")):
            traffic.create_oval(4 + index * 15, 5, 14 + index * 15, 15, fill=color, outline=color)
        ttk.Label(toolbar, text="AI Probe", style="ToolbarTitle.TLabel").grid(row=0, column=1, sticky="w")
        self._mac_button(toolbar, "本地中转", self._open_relay_window, kind="primary", surface="#ffffff").grid(
            row=0, column=2, sticky="e", padx=(8, 0)
        )
        ttk.Label(toolbar, text="多项目测活", style="ToolbarMuted.TLabel").grid(
            row=0, column=3, sticky="e", padx=(8, 0)
        )
        ttk.Separator(self.root, orient=HORIZONTAL).grid(row=0, column=0, columnspan=2, sticky="se")

        sidebar = ttk.Frame(self.root, style="Sidebar.TFrame", padding=(14, 14))
        sidebar.grid(row=1, column=0, sticky="nsew", padx=(0, 1))
        sidebar.grid_rowconfigure(3, weight=1)
        sidebar.grid_columnconfigure(0, weight=1)

        ttk.Label(sidebar, text="项目", style="SidebarTitle.TLabel").grid(row=0, column=0, sticky="w")
        project_actions = ttk.Frame(sidebar, style="Sidebar.TFrame")
        project_actions.grid(row=1, column=0, sticky="ew", pady=(12, 8))
        project_actions.grid_columnconfigure((0, 1, 2), weight=1)
        self._mac_button(project_actions, "新建项目", self._new_project, surface="#f1f2f5").grid(
            row=0, column=0, sticky="ew", padx=(0, 3)
        )
        self._mac_button(project_actions, "删除", self._delete_project, kind="danger", surface="#f1f2f5").grid(
            row=0, column=1, sticky="ew", padx=(3, 0)
        )
        self._mac_button(project_actions, "导入", self._import_config, surface="#f1f2f5").grid(
            row=1, column=0, sticky="ew", padx=(0, 3), pady=(6, 0)
        )
        self._mac_button(project_actions, "备份", self._backup_config, surface="#f1f2f5").grid(
            row=1, column=1, sticky="ew", padx=3, pady=(6, 0)
        )
        self._mac_button(project_actions, "加密文件", self._encrypt_config_file, surface="#f1f2f5").grid(
            row=1, column=2, sticky="ew", padx=(3, 0), pady=(6, 0)
        )

        project_search_bar = ttk.Frame(sidebar, style="Sidebar.TFrame")
        project_search_bar.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        project_search_bar.grid_columnconfigure(0, weight=1)
        self.project_search_entry = ttk.Entry(project_search_bar, textvariable=self.project_search)
        self.project_search_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.project_search_entry.bind("<Return>", self._search_projects)
        self._mac_button(project_search_bar, "搜索", self._search_projects, surface="#f1f2f5", width=5).grid(
            row=0, column=1
        )

        self.project_list = Listbox(
            sidebar,
            width=25,
            exportselection=False,
            borderwidth=0,
            highlightthickness=0,
            background="#f1f2f5",
            foreground="#1d1d1f",
            selectbackground="#d8eaff",
            selectforeground="#0a4b8f",
            activestyle="none",
            font=("Microsoft YaHei UI", 10),
        )
        self.project_list.grid(row=3, column=0, sticky="nsew")
        self.project_list.bind("<<ListboxSelect>>", self._on_project_selected)
        self.project_list.bind("<Button-3>", self._show_project_list_menu)
        ttk.Label(sidebar, text="配置自动保存到本机", style="SidebarMuted.TLabel").grid(
            row=4, column=0, sticky="w", pady=(10, 0)
        )

        main = ttk.Frame(self.root, style="Main.TFrame", padding=(12, 12, 12, 10))
        main.grid(row=1, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        config = ttk.Frame(main, style="Panel.TFrame", padding=12)
        config.grid(row=0, column=0, sticky="ew", pady=(0, 9))
        config.grid_columnconfigure(1, weight=1)
        config.grid_columnconfigure(3, weight=1)
        config_header = ttk.Frame(config, style="Panel.TFrame")
        config_header.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 7))
        config_header.grid_columnconfigure(0, weight=1)
        ttk.Label(config_header, text="项目配置", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.advanced_toggle_button = self._mac_button(
            config_header,
            "高级设置",
            self._toggle_advanced_settings,
            surface="#ffffff",
        )
        self.advanced_toggle_button.grid(row=0, column=1, sticky="e")
        ttk.Label(config, text="项目名称", style="Panel.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(config, textvariable=self.project_name).grid(row=1, column=1, sticky="ew", padx=(0, 16))
        ttk.Label(config, text="API 方式", style="Panel.TLabel").grid(row=1, column=2, sticky="w", padx=(0, 8))
        modes = ttk.Frame(config, style="Segment.TFrame", padding=2)
        modes.grid(row=1, column=3, sticky="w")
        ttk.Radiobutton(modes, text="Chat", value="chat", variable=self.api_mode, style="Segment.TRadiobutton").pack(
            side="left"
        )
        ttk.Radiobutton(
            modes, text="Responses", value="responses", variable=self.api_mode, style="Segment.TRadiobutton"
        ).pack(side="left")
        ttk.Radiobutton(
            modes, text="Anthropic", value="anthropic", variable=self.api_mode, style="Segment.TRadiobutton"
        ).pack(side="left")

        ttk.Label(config, text="API 地址", style="Panel.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=(7, 0)
        )
        ttk.Entry(config, textvariable=self.base_url).grid(row=2, column=1, columnspan=3, sticky="ew", pady=(7, 0))
        ttk.Label(config, text="API 密钥", style="Panel.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 8), pady=(7, 0)
        )
        self.key_entry = ttk.Entry(config, textvariable=self.api_key, show="*")
        self.key_entry.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(7, 0), padx=(0, 8))
        key_controls = ttk.Frame(config, style="Panel.TFrame")
        key_controls.grid(row=3, column=3, sticky="w", pady=(7, 0))
        ttk.Checkbutton(key_controls, text="显示", variable=self.show_key, command=self._toggle_key).pack(side="left")
        self._mac_button(key_controls, "管理密钥", self._manage_api_keys, surface="#ffffff").pack(
            side="left", padx=(8, 0)
        )

        self.advanced_settings = ttk.Frame(config, style="Panel.TFrame")
        self.advanced_settings.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(7, 0))
        self.advanced_settings.grid_columnconfigure(1, weight=1)
        self.advanced_settings.grid_columnconfigure(3, weight=1)

        ttk.Label(self.advanced_settings, text="HTTP 代理（可选）", style="Panel.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        ttk.Entry(self.advanced_settings, textvariable=self.proxy_url).grid(row=0, column=1, columnspan=3, sticky="ew")
        ttk.Checkbutton(
            self.advanced_settings,
            text="跳过 SSL 证书验证（不安全）",
            variable=self.skip_ssl_verify,
        ).grid(row=1, column=1, columnspan=3, sticky="w", pady=(7, 0))

        prompt_label = ttk.Frame(self.advanced_settings, style="Panel.TFrame")
        prompt_label.grid(row=2, column=0, sticky="nw", padx=(0, 8), pady=(7, 0))
        ttk.Label(prompt_label, text="测活内容", style="Panel.TLabel").pack(anchor="w")
        ttk.Label(
            prompt_label,
            text=f"留空使用默认：{TEST_PROMPT}",
            style="Muted.TLabel",
            wraplength=120,
        ).pack(anchor="w", pady=(3, 0))
        self.prompt_text = Text(
            self.advanced_settings,
            height=1,
            wrap="word",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#c8ced2",
            highlightcolor="#087f6c",
            font=("Microsoft YaHei UI", 9),
        )
        self.prompt_text.grid(row=2, column=1, columnspan=3, sticky="ew", pady=(7, 0))
        self.prompt_text.bind("<<Modified>>", self._on_text_modified)

        ttk.Label(self.advanced_settings, text="自定义请求头", style="Panel.TLabel").grid(
            row=3, column=0, sticky="nw", padx=(0, 8), pady=(7, 0)
        )
        header_editor = ttk.Frame(self.advanced_settings, style="Panel.TFrame")
        header_editor.grid(row=3, column=1, columnspan=3, sticky="ew", pady=(7, 0))
        header_editor.grid_columnconfigure(0, weight=1)

        header_toolbar = ttk.Frame(header_editor, style="Panel.TFrame")
        header_toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        header_mode = ttk.Frame(header_toolbar, style="Segment.TFrame", padding=2)
        header_mode.pack(side="left")
        ttk.Radiobutton(
            header_mode,
            text="JSON",
            value="json",
            variable=self.headers_mode,
            style="Segment.TRadiobutton",
            command=self._switch_headers_mode,
        ).pack(side="left")
        ttk.Radiobutton(
            header_mode,
            text="手动",
            value="manual",
            variable=self.headers_mode,
            style="Segment.TRadiobutton",
            command=self._switch_headers_mode,
        ).pack(side="left")
        self.quick_user_agent_button = self._mac_button(
            header_toolbar,
            "快速添加 User-Agent",
            self._quick_add_user_agent,
            surface="#ffffff",
        )
        self.quick_user_agent_button.pack(side="right", padx=(8, 0))
        self.add_header_button = self._mac_button(
            header_toolbar,
            "+ 添加请求头",
            self._add_header_row,
            surface="#ffffff",
        )
        self.add_header_button.pack(side="right")

        self.header_body = ttk.Frame(header_editor, style="Panel.TFrame")
        self.header_body.grid(row=1, column=0, sticky="ew")
        self.header_body.grid_columnconfigure(0, weight=1)

        self.headers_json_text = Text(
            self.header_body,
            height=3,
            wrap="none",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#d6d6dc",
            highlightcolor="#007aff",
            background="#fbfbfd",
            foreground="#1d1d1f",
            insertbackground="#1d1d1f",
            font=("Consolas", 9),
        )
        self.headers_json_text.bind("<<Modified>>", self._on_text_modified)

        self.manual_headers_area = ttk.Frame(self.header_body, style="Panel.TFrame")
        self.manual_headers_area.grid_columnconfigure(0, weight=1)
        manual_headings = ttk.Frame(self.manual_headers_area, style="Panel.TFrame")
        manual_headings.grid(row=0, column=0, sticky="ew", padx=(1, 18), pady=(0, 3))
        manual_headings.grid_columnconfigure((0, 1), weight=1)
        ttk.Label(manual_headings, text="请求头名称", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(6, 4)
        )
        ttk.Label(manual_headings, text="请求头值", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(6, 4))

        manual_list = ttk.Frame(self.manual_headers_area, style="Panel.TFrame")
        manual_list.grid(row=1, column=0, sticky="ew")
        manual_list.grid_columnconfigure(0, weight=1)
        self.headers_canvas = Canvas(
            manual_list,
            height=58,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#d6d6dc",
            background="#fbfbfd",
        )
        header_scroll = ttk.Scrollbar(manual_list, orient=VERTICAL, command=self.headers_canvas.yview)
        self.headers_canvas.configure(yscrollcommand=header_scroll.set)
        self.headers_canvas.grid(row=0, column=0, sticky="ew")
        header_scroll.grid(row=0, column=1, sticky="ns")
        self.manual_headers_frame = ttk.Frame(self.headers_canvas, style="Panel.TFrame")
        self.manual_headers_window = self.headers_canvas.create_window(
            (0, 0), window=self.manual_headers_frame, anchor="nw"
        )
        self.headers_canvas.bind("<Configure>", self._resize_manual_headers)
        self.manual_headers_frame.bind("<Configure>", self._update_manual_scrollregion)
        self.advanced_settings.grid_remove()

        panes = ttk.Panedwindow(main, orient=HORIZONTAL)
        panes.grid(row=1, column=0, sticky="nsew")

        remote_panel = ttk.Frame(panes, style="Panel.TFrame", padding=12)
        added_panel = ttk.Frame(panes, style="Panel.TFrame", padding=12)
        panes.add(remote_panel, weight=1)
        panes.add(added_panel, weight=3)

        remote_panel.grid_columnconfigure(0, weight=1)
        remote_panel.grid_rowconfigure(2, weight=1)
        ttk.Label(remote_panel, text="可发现模型", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(remote_panel, textvariable=self.discovered_count, style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(2, 8)
        )
        remote_list_frame = ttk.Frame(remote_panel, style="Panel.TFrame")
        remote_list_frame.grid(row=2, column=0, sticky="nsew")
        remote_list_frame.grid_columnconfigure(0, weight=1)
        remote_list_frame.grid_rowconfigure(0, weight=1)
        self.remote_list = Listbox(
            remote_list_frame,
            selectmode=EXTENDED,
            exportselection=False,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#d6d6dc",
            selectbackground="#007aff",
            background="#fbfbfd",
            foreground="#1d1d1f",
            activestyle="none",
            font=("Consolas", 9),
        )
        remote_scroll = ttk.Scrollbar(remote_list_frame, orient=VERTICAL, command=self.remote_list.yview)
        self.remote_list.configure(yscrollcommand=remote_scroll.set)
        self.remote_list.grid(row=0, column=0, sticky="nsew")
        self.remote_list.bind("<Button-3>", self._show_remote_model_menu)
        self.remote_list.bind("<Control-c>", self._copy_selected_remote_model)
        remote_scroll.grid(row=0, column=1, sticky="ns")

        remote_actions = ttk.Frame(remote_panel, style="Panel.TFrame")
        remote_actions.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        remote_actions.grid_columnconfigure((0, 1), weight=1)
        fetch_button = self._mac_button(
            remote_actions, "获取模型列表", self._fetch_models, kind="primary", surface="#ffffff"
        )
        fetch_button.grid(row=0, column=0, columnspan=2, sticky="ew")
        self._mac_button(remote_actions, "添加选中", self._add_selected, surface="#ffffff").grid(
            row=1, column=0, sticky="ew", pady=(7, 0), padx=(0, 4)
        )
        self._mac_button(remote_actions, "添加全部", self._add_all, surface="#ffffff").grid(
            row=1, column=1, sticky="ew", pady=(7, 0), padx=(4, 0)
        )
        detect_button = self._mac_button(
            remote_actions, "检测全部并仅添加可用", self._detect_all_available, surface="#ffffff"
        )
        detect_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        self.network_buttons.extend([fetch_button, detect_button])

        added_panel.grid_columnconfigure(0, weight=1)
        added_panel.grid_rowconfigure(3, weight=1, minsize=280)
        added_header = ttk.Frame(added_panel, style="Panel.TFrame")
        added_header.grid(row=0, column=0, sticky="ew")
        added_header.grid_columnconfigure(0, weight=1)
        ttk.Label(added_header, text="项目模型", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        test_all = self._mac_button(added_header, "测活全部", self._test_all, kind="primary", surface="#ffffff")
        test_all.grid(row=0, column=1, sticky="e")
        ttk.Label(added_panel, textvariable=self.added_count, style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(2, 7)
        )

        custom_model_bar = ttk.Frame(added_panel, style="Panel.TFrame")
        custom_model_bar.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        custom_model_bar.grid_columnconfigure(1, weight=1)
        ttk.Label(custom_model_bar, text="自定义模型 ID", style="Panel.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.custom_model_entry = ttk.Entry(custom_model_bar, textvariable=self.custom_model_id)
        self.custom_model_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.custom_model_entry.bind("<Return>", self._add_custom_model)
        self._mac_button(
            custom_model_bar, "添加", self._add_custom_model, kind="primary", surface="#ffffff", width=5
        ).grid(row=0, column=2)

        tree_frame = ttk.Frame(added_panel, style="Panel.TFrame")
        tree_frame.grid(row=3, column=0, sticky="nsew")
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)
        self.model_tree = ttk.Treeview(
            tree_frame,
            columns=("key", "status", "first", "total", "reply"),
            show="tree headings",
            selectmode="extended",
            height=9,
        )
        self.model_tree.heading("#0", text="模型")
        self.model_tree.heading("key", text="密钥")
        self.model_tree.heading("status", text="状态")
        self.model_tree.heading("first", text="首字延时")
        self.model_tree.heading("total", text="总耗时")
        self.model_tree.heading("reply", text="响应 / 错误")
        self.model_tree.column("#0", width=210, minwidth=130)
        self.model_tree.column("key", width=130, minwidth=90, anchor="w")
        self.model_tree.column("status", width=75, minwidth=65, anchor="center", stretch=False)
        self.model_tree.column("first", width=90, minwidth=80, anchor="e", stretch=False)
        self.model_tree.column("total", width=80, minwidth=75, anchor="e", stretch=False)
        self.model_tree.column("reply", width=330, minwidth=150)
        self.model_tree.tag_configure("ok", foreground="#087f23")
        self.model_tree.tag_configure("fail", foreground="#b3261e")
        self.model_tree.tag_configure("testing", foreground="#9a6700")
        self.model_tree.tag_configure("unknown", foreground="#687078")
        tree_y = ttk.Scrollbar(tree_frame, orient=VERTICAL, command=self.model_tree.yview)
        tree_x = ttk.Scrollbar(tree_frame, orient=HORIZONTAL, command=self.model_tree.xview)
        self.model_tree.configure(yscrollcommand=tree_y.set, xscrollcommand=tree_x.set)
        self.model_tree.grid(row=0, column=0, sticky="nsew")
        tree_y.grid(row=0, column=1, sticky="ns")
        tree_x.grid(row=1, column=0, sticky="ew")
        self.model_tree.bind("<Double-1>", self._show_model_detail)
        self.model_tree.bind("<Delete>", lambda _event: self._remove_selected())
        self.model_tree.bind("<Button-3>", self._show_project_model_menu)
        self.model_tree.bind("<Control-c>", self._copy_selected_project_model)

        added_actions = ttk.Frame(added_panel, style="Panel.TFrame")
        added_actions.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        test_selected = self._mac_button(added_actions, "测活选中", self._test_selected, surface="#ffffff")
        test_selected.pack(side="left")
        self._mac_button(added_actions, "设置密钥", self._assign_selected_model_key, surface="#ffffff").pack(
            side="left", padx=(7, 0)
        )
        self._mac_button(added_actions, "删除选中", self._remove_selected, surface="#ffffff").pack(
            side="right", padx=(7, 0)
        )
        self._mac_button(added_actions, "清空列表", self._remove_all, kind="danger", surface="#ffffff").pack(
            side="right"
        )
        self.network_buttons.extend([test_selected, test_all])

        footer = ttk.Frame(main, style="Panel.TFrame", padding=(10, 7))
        footer.grid(row=2, column=0, sticky="ew", pady=(9, 0))
        footer.grid_columnconfigure(1, weight=1)
        ttk.Label(footer, text="状态", style="Section.TLabel").grid(row=0, column=0, sticky="nw", padx=(0, 12))
        self.status_label = ttk.Label(footer, textvariable=self.status, style="Status.TLabel")
        self.status_label.grid(row=0, column=1, sticky="w")
        self.log_text = Text(
            footer,
            height=4,
            borderwidth=0,
            highlightthickness=0,
            bg="#f5f5f7",
            fg="#3a3a3c",
            selectbackground="#dbeeff",
            font=("Consolas", 8),
            state="disabled",
        )
        self.log_scroll = ttk.Scrollbar(footer, orient=VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=self.log_scroll.set)
        self.log_text.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(7, 0))
        self.log_scroll.grid(row=1, column=2, sticky="ns", pady=(7, 0))
