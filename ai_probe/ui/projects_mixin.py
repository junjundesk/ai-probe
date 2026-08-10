"""项目编辑、请求头和 API Key 管理。"""

from __future__ import annotations

import json
import uuid
from tkinter import END, VERTICAL, Canvas, Listbox, StringVar, Text, Toplevel, messagebox, simpledialog, ttk

from ..config import QUICK_USER_AGENT
from ..projects import (
    _project_keys,
    _sync_project_keys,
    api_key_label,
    new_project,
    project_key_for_model,
)
from ..utils import parse_custom_headers, parse_manual_headers


class ProjectsMixin:
    def _ensure_selection(self):
        ids = {project["id"] for project in self.store["projects"]}
        if self.current_id not in ids:
            self.current_id = self.store["projects"][0]["id"]
        self.store["selected_project_id"] = self.current_id

    def _project(self, project_id=None):
        project_id = project_id or self.current_id
        return next((p for p in self.store["projects"] if p["id"] == project_id), None)

    def _refresh_project_list(self, load_form=True):
        self.updating_projects = True
        self.project_list.delete(0, END)
        self.visible_project_ids.clear()
        query = self.project_search.get().strip().lower()
        selected_index = None
        for project in self.store["projects"]:
            if query and query not in project["name"].lower():
                continue
            index = len(self.visible_project_ids)
            self.visible_project_ids.append(project["id"])
            self.project_list.insert(END, project["name"])
            if project["id"] == self.current_id:
                selected_index = index
        if selected_index is not None:
            self.project_list.selection_set(selected_index)
            self.project_list.activate(selected_index)
            self.project_list.see(selected_index)
        self.updating_projects = False
        if load_form:
            self._load_current_project()

    def _search_projects(self, _event=None):
        self._commit_form()
        self._refresh_project_list(load_form=False)
        count = len(self.visible_project_ids)
        query = self.project_search.get().strip()
        self.status.set(f"项目搜索：{count} 个结果" if query else f"显示全部项目：{count} 个")
        return "break"

    def _load_current_project(self):
        project = self._project()
        if not project:
            return
        self.loading_form = True
        self.project_name.set(project["name"])
        self.base_url.set(project["base_url"])
        self.api_key.set(project["api_key"])
        self.proxy_url.set(project.get("proxy_url", ""))
        self.api_mode.set(project["api_mode"])
        self._set_text(self.prompt_text, project.get("test_prompt", ""))
        custom_headers = project.get("custom_headers", "")
        if isinstance(custom_headers, dict):
            custom_headers = json.dumps(custom_headers, ensure_ascii=False, indent=2)
        self._set_text(self.headers_json_text, str(custom_headers))
        manual_headers = project.get("manual_headers", [])
        if not isinstance(manual_headers, list):
            manual_headers = []
        if not manual_headers and custom_headers:
            try:
                parsed_headers = parse_custom_headers(str(custom_headers))
                manual_headers = [{"name": name, "value": value} for name, value in parsed_headers.items()]
            except ValueError:
                pass
        self._set_manual_header_rows(manual_headers)
        mode = project.get("headers_mode", "json")
        if mode not in {"json", "manual"}:
            mode = "json"
        self.headers_mode.set(mode)
        self.active_headers_mode = mode
        self._show_headers_mode()
        self.loading_form = False
        self._refresh_models()

    def _schedule_save(self, *_):
        if self.loading_form:
            return
        if self.save_timer:
            self.root.after_cancel(self.save_timer)
        self.save_timer = self.root.after(400, self._commit_form)

    def _on_text_modified(self, event):
        widget = event.widget
        if not widget.edit_modified():
            return
        widget.edit_modified(False)
        self._schedule_save()

    def _resize_manual_headers(self, event):
        self.headers_canvas.itemconfigure(self.manual_headers_window, width=event.width)

    def _update_manual_scrollregion(self, _event=None):
        self.headers_canvas.configure(scrollregion=self.headers_canvas.bbox("all"))

    def _add_header_row(self, name="", value="", schedule=True):
        row_frame = ttk.Frame(self.manual_headers_frame, style="Panel.TFrame")
        row_frame.grid(row=len(self.header_rows), column=0, sticky="ew", padx=5, pady=3)
        row_frame.grid_columnconfigure((0, 1), weight=1)
        name_var = StringVar(value=name)
        value_var = StringVar(value=value)
        ttk.Entry(row_frame, textvariable=name_var).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ttk.Entry(row_frame, textvariable=value_var).grid(row=0, column=1, sticky="ew", padx=(0, 5))
        record = {"frame": row_frame, "name": name_var, "value": value_var}
        self._mac_button(
            row_frame,
            "×",
            lambda: self._remove_header_row(record),
            kind="danger",
            surface="#ffffff",
            width=3,
        ).grid(row=0, column=2)
        name_var.trace_add("write", self._schedule_save)
        value_var.trace_add("write", self._schedule_save)
        self.header_rows.append(record)
        self.root.after_idle(self._update_manual_scrollregion)
        if schedule:
            self._schedule_save()

    def _remove_header_row(self, record):
        if record not in self.header_rows:
            return
        self.header_rows.remove(record)
        record["frame"].destroy()
        for index, row in enumerate(self.header_rows):
            row["frame"].grid_configure(row=index)
        self._update_manual_scrollregion()
        self._schedule_save()

    def _quick_add_user_agent(self):
        if self.headers_mode.get() == "manual":
            existing = {row["name"].get().strip().lower() for row in self.header_rows}
            if "user-agent" not in existing:
                self._add_header_row("User-Agent", QUICK_USER_AGENT)
            return

        try:
            headers = parse_custom_headers(self.headers_json_text.get("1.0", "end-1c"))
        except ValueError as exc:
            messagebox.showerror("请求头格式错误", str(exc))
            return
        if not any(name.lower() == "user-agent" for name in headers):
            headers["User-Agent"] = QUICK_USER_AGENT
            self._set_text(
                self.headers_json_text,
                json.dumps(headers, ensure_ascii=False, indent=2),
            )
            self._schedule_save()

    def _set_manual_header_rows(self, rows: list[dict]):
        previous_loading = self.loading_form
        self.loading_form = True
        for row in self.header_rows:
            row["frame"].destroy()
        self.header_rows.clear()
        for row in rows:
            self._add_header_row(
                str(row.get("name", "")),
                str(row.get("value", "")),
                schedule=False,
            )
        if not self.header_rows:
            self._add_header_row(schedule=False)
        self.loading_form = previous_loading
        self.root.after_idle(self._update_manual_scrollregion)

    def _manual_header_values(self) -> list[dict]:
        return [{"name": row["name"].get(), "value": row["value"].get()} for row in self.header_rows]

    def _switch_headers_mode(self):
        new_mode = self.headers_mode.get()
        old_mode = self.active_headers_mode
        if new_mode == old_mode:
            return
        try:
            if new_mode == "manual":
                parsed = parse_custom_headers(self.headers_json_text.get("1.0", "end-1c"))
                self._set_manual_header_rows([{"name": name, "value": value} for name, value in parsed.items()])
            else:
                parsed = parse_manual_headers(self._manual_header_values())
                text = json.dumps(parsed, ensure_ascii=False, indent=2) if parsed else ""
                self._set_text(self.headers_json_text, text)
        except ValueError as exc:
            self.loading_form = True
            self.headers_mode.set(old_mode)
            self.loading_form = False
            messagebox.showerror("请求头格式错误", str(exc))
            return
        self.active_headers_mode = new_mode
        self._show_headers_mode()
        self._schedule_save()

    def _show_headers_mode(self):
        self.headers_json_text.grid_remove()
        self.manual_headers_area.grid_remove()
        self.add_header_button.pack_forget()
        if self.headers_mode.get() == "manual":
            self.manual_headers_area.grid(row=0, column=0, sticky="ew")
            self.add_header_button.pack(side="right")
        else:
            self.headers_json_text.grid(row=0, column=0, sticky="ew")

    @staticmethod
    def _set_text(widget: Text, value: str):
        widget.delete("1.0", END)
        widget.insert("1.0", value)
        widget.edit_modified(False)

    def _commit_form(self):
        self.save_timer = None
        project = self._project()
        if not project:
            return
        previous_name = project.get("name", "")
        project["name"] = self.project_name.get().strip() or "未命名项目"
        project["base_url"] = self.base_url.get().strip()
        api_key_value = self.api_key.get().strip()
        project["api_key"] = api_key_value
        raw_keys = project.get("api_keys")
        if isinstance(raw_keys, list) and raw_keys:
            raw_keys[0]["value"] = api_key_value
        else:
            project["api_keys"] = [{"id": "default", "name": "默认", "value": api_key_value}]
        _sync_project_keys(project)
        project["proxy_url"] = self.proxy_url.get().strip()
        project["api_mode"] = self.api_mode.get()
        project["test_prompt"] = self.prompt_text.get("1.0", "end-1c").strip()
        project["headers_mode"] = self.headers_mode.get()
        project["custom_headers"] = self.headers_json_text.get("1.0", "end-1c").strip()
        project["manual_headers"] = self._manual_header_values()
        self.store["selected_project_id"] = self.current_id
        self._save_store()

        if previous_name != project["name"]:
            self._refresh_project_list(load_form=False)

    def _on_project_selected(self, _event=None):
        if self.updating_projects:
            return
        selection = self.project_list.curselection()
        if not selection:
            return
        index = selection[0]
        if index >= len(self.visible_project_ids):
            return
        project = self._project(self.visible_project_ids[index])
        if not project:
            return
        if project["id"] == self.current_id:
            return
        self._commit_form()
        self.current_id = project["id"]
        self.store["selected_project_id"] = self.current_id
        self._load_current_project()
        self._save_store()

    def _show_project_list_menu(self, event):
        self.context_project_id = None
        row_state = "disabled"
        if self.project_list.size():
            index = self.project_list.nearest(event.y)
            bounds = self.project_list.bbox(index)
            if bounds and bounds[1] <= event.y <= bounds[1] + bounds[3] and index < len(self.visible_project_ids):
                self.project_list.selection_clear(0, END)
                self.project_list.selection_set(index)
                self.project_list.activate(index)
                self.context_project_id = self.visible_project_ids[index]
                row_state = "normal"
                self._on_project_selected()
        self.project_context_menu.entryconfigure(1, state=row_state)
        self.project_context_menu.entryconfigure(2, state=row_state)
        try:
            self.project_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.project_context_menu.grab_release()
        return "break"

    def _rename_context_project(self):
        project = self._project(self.context_project_id)
        if not project:
            return
        name = simpledialog.askstring(
            "重命名项目",
            "请输入新的项目名称：",
            initialvalue=project["name"],
            parent=self.root,
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showerror("重命名项目", "项目名称不能为空")
            return
        project["name"] = name
        if project["id"] == self.current_id:
            self.loading_form = True
            self.project_name.set(name)
            self.loading_form = False
        self._save_store()
        self._refresh_project_list(load_form=False)
        self.status.set(f"项目已重命名：{name}")

    def _delete_context_project(self):
        if not self.context_project_id:
            return
        if self.context_project_id != self.current_id:
            self._commit_form()
            self.current_id = self.context_project_id
            self.store["selected_project_id"] = self.current_id
        self._delete_project()
        self.context_project_id = None

    def _delete_all_projects(self):
        if not messagebox.askyesno(
            "删除全部项目",
            "确定删除全部项目吗？删除后会创建一个空白默认项目。",
        ):
            return
        project = new_project("默认项目")
        self.store["projects"] = [project]
        self.current_id = project["id"]
        self.store["selected_project_id"] = self.current_id
        self.project_search.set("")
        self.context_project_id = None
        self._refresh_project_list()
        self._save_store()
        self.status.set("已删除全部项目")
        self._log("删除全部项目并创建默认项目")

    def _new_project(self):
        self._commit_form()
        project = new_project(f"项目 {len(self.store['projects']) + 1}")
        self.store["projects"].append(project)
        self.current_id = project["id"]
        self.store["selected_project_id"] = self.current_id
        self.project_search.set("")
        self._refresh_project_list()
        self._save_store()

    def _delete_project(self):
        project = self._project()
        if not project or not messagebox.askyesno("删除项目", f"确定删除“{project['name']}”吗？"):
            return
        self.store["projects"] = [p for p in self.store["projects"] if p["id"] != project["id"]]
        if not self.store["projects"]:
            self.store["projects"].append(new_project("默认项目"))
        self.current_id = self.store["projects"][0]["id"]
        self.store["selected_project_id"] = self.current_id
        self.project_search.set("")
        self._refresh_project_list()
        self._save_store()

    def _toggle_key(self):
        self.key_entry.configure(show="" if self.show_key.get() == "1" else "*")

    def _manage_api_keys(self):
        self._commit_form()
        project = self._project()
        if not project:
            return
        window = Toplevel(self.root)
        window.title("管理 API 密钥")
        window.geometry("720x460")
        window.minsize(600, 360)
        window.configure(bg="#f2f2f7")
        window.transient(self.root)
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(1, weight=1)

        header = ttk.Frame(window, style="Toolbar.TFrame", padding=(16, 12))
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ttk.Label(header, text="管理 API 密钥", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="同一站点可使用多个密钥，每个模型可绑定其中一个",
            style="ToolbarMuted.TLabel",
        ).grid(row=0, column=1, sticky="e")

        list_host = ttk.Frame(window, style="Panel.TFrame", padding=12)
        list_host.grid(row=1, column=0, sticky="nsew", padx=12, pady=(10, 8))
        list_host.grid_columnconfigure(0, weight=1)
        list_host.grid_rowconfigure(0, weight=1)
        canvas = Canvas(
            list_host, borderwidth=0, highlightthickness=1, highlightbackground="#d6d6dc", background="#fbfbfd"
        )
        scroll = ttk.Scrollbar(list_host, orient=VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        rows_frame = ttk.Frame(canvas, style="Panel.TFrame", padding=(8, 5))
        rows_window = canvas.create_window((0, 0), window=rows_frame, anchor="nw")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(rows_window, width=event.width))
        rows_frame.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        rows_frame.grid_columnconfigure((0, 1), weight=1)

        headings = ttk.Frame(rows_frame, style="Panel.TFrame")
        headings.grid(row=0, column=0, sticky="ew", padx=(1, 34), pady=(0, 4))
        headings.grid_columnconfigure((0, 1), weight=1)
        ttk.Label(headings, text="密钥名称", style="Muted.TLabel").grid(row=0, column=0, sticky="w", padx=(6, 4))
        ttk.Label(headings, text="密钥内容", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(6, 4))

        rows = []

        def reflow_rows():
            for index, row in enumerate(rows):
                row["frame"].grid_configure(row=index + 1)
            canvas.configure(scrollregion=canvas.bbox("all"))

        def remove_row(row):
            if row not in rows:
                return
            rows.remove(row)
            row["frame"].destroy()
            reflow_rows()

        def add_row(key, schedule=False):
            row_frame = ttk.Frame(rows_frame, style="Panel.TFrame")
            row_frame.grid(row=len(rows) + 1, column=0, sticky="ew", padx=5, pady=3)
            row_frame.grid_columnconfigure((0, 1), weight=1)
            name_var = StringVar(value=str(key.get("name") or ""))
            value_var = StringVar(value=str(key.get("value") or ""))
            ttk.Entry(row_frame, textvariable=name_var).grid(row=0, column=0, sticky="ew", padx=(0, 5))
            ttk.Entry(row_frame, textvariable=value_var).grid(row=0, column=1, sticky="ew", padx=(0, 5))
            record = {
                "id": str(key.get("id") or "") or uuid.uuid4().hex,
                "frame": row_frame,
                "name": name_var,
                "value": value_var,
            }
            self._mac_button(
                row_frame,
                "×",
                lambda: remove_row(record),
                kind="danger",
                surface="#ffffff",
                width=3,
            ).grid(row=0, column=2)
            rows.append(record)

        for key in _project_keys(project):
            add_row(key)
        if not rows:
            add_row({"id": "", "name": "默认", "value": ""})
        window.after_idle(reflow_rows)

        footer = ttk.Frame(window, style="Panel.TFrame", padding=14)
        footer.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        footer.grid_columnconfigure(0, weight=1)
        self._mac_button(
            footer, "新增密钥", lambda: add_row({"id": "", "name": "", "value": ""}) or reflow_rows(), surface="#ffffff"
        ).grid(row=0, column=0, sticky="w")

        def save_keys():
            new_keys = []
            seen = set()
            for row in rows:
                key_id = row["id"]
                if key_id in seen:
                    continue
                seen.add(key_id)
                new_keys.append(
                    {
                        "id": key_id,
                        "name": row["name"].get().strip(),
                        "value": row["value"].get(),
                    }
                )
            if not new_keys:
                new_keys = [{"id": uuid.uuid4().hex, "name": "默认", "value": ""}]
            project["api_keys"] = new_keys
            _sync_project_keys(project)
            valid_ids = {key["id"] for key in new_keys}
            default_key_id = new_keys[0]["id"]
            for model in project.get("models", []):
                if model.get("api_key_id") not in valid_ids:
                    model["api_key_id"] = default_key_id
            for entry in project.get("discovered_models", []):
                if entry.get("api_key_id") not in valid_ids:
                    entry["api_key_id"] = default_key_id
            self._save_store()
            self.api_key.set(project["api_key"])
            self._refresh_models()
            self.status.set(f"已保存 {len(new_keys)} 个 API 密钥")
            self._log(f"保存 API 密钥：{len(new_keys)} 个")
            window.destroy()

        self._mac_button(footer, "保存", save_keys, kind="primary", surface="#ffffff").grid(
            row=0, column=1, padx=(8, 0)
        )
        self._mac_button(footer, "取消", window.destroy, surface="#ffffff").grid(row=0, column=2, padx=(8, 0))

    def _choose_api_key(self, title: str, prompt: str, initial_id: str | None = None) -> str | None:
        project = self._project()
        keys = _project_keys(project) if project else []
        if not keys:
            messagebox.showinfo(title, "请先添加 API 密钥")
            return None
        result = {"id": None}
        window = Toplevel(self.root)
        window.title(title)
        window.transient(self.root)
        window.resizable(False, False)
        body = ttk.Frame(window, padding=16)
        body.grid(row=0, column=0, sticky="nsew")
        ttk.Label(body, text=prompt, style="Section.TLabel").grid(row=0, column=0, sticky="w")
        listbox = Listbox(
            body,
            width=46,
            height=min(10, max(4, len(keys))),
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#d6d6dc",
            selectbackground="#007aff",
            activestyle="none",
            font=("Microsoft YaHei UI", 9),
        )
        listbox.grid(row=1, column=0, sticky="w", pady=(10, 12))
        for index, key in enumerate(keys):
            listbox.insert(END, api_key_label(key))
            if key["id"] == initial_id:
                listbox.selection_set(index)
        if not listbox.curselection() and keys:
            listbox.selection_set(0)

        def confirm():
            selection = listbox.curselection()
            if selection:
                result["id"] = keys[selection[0]]["id"]
            window.destroy()

        actions = ttk.Frame(body, style="Panel.TFrame")
        actions.grid(row=2, column=0, sticky="e")
        self._mac_button(actions, "确定", confirm, kind="primary", surface="#ffffff").pack(side="left", padx=(0, 8))
        self._mac_button(actions, "取消", window.destroy, surface="#ffffff").pack(side="left")
        window.grab_set()
        window.focus_force()
        self.root.wait_window(window)
        return result["id"]

    def _assign_selected_model_key(self):
        selected = self._selected_model_ids()
        if not selected:
            messagebox.showinfo("设置密钥", "请先选择模型")
            return
        project = self._project()
        if not project:
            return
        first_model = next((model for model in project["models"] if model["id"] == selected[0]), None)
        initial_id = project_key_for_model(project, first_model or {})["id"] if first_model else None
        key_id = self._choose_api_key("设置密钥", "请选择该模型使用的 API 密钥：", initial_id)
        if key_id is None:
            return
        for model in project["models"]:
            if model["id"] in set(selected):
                model["api_key_id"] = key_id
        self._save_store()
        self._refresh_models()
        self.status.set(f"已为 {len(selected)} 个模型设置密钥")

    def _toggle_advanced_settings(self):
        self.advanced_settings_visible = not self.advanced_settings_visible
        if self.advanced_settings_visible:
            self.advanced_settings.grid()
            self.advanced_toggle_button.set_text("收起设置")
        else:
            self.advanced_settings.grid_remove()
            self.advanced_toggle_button.set_text("高级设置")
