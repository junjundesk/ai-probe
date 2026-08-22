"""模型发现、测活任务和异步 UI 更新。"""

from __future__ import annotations

import queue
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime
from tkinter import END, messagebox

from ..client import OpenAIClient
from ..config import MAX_WORKERS, PROBE_TIMEOUT, PROBE_UI_BATCH_SIZE
from ..projects import _project_keys, api_key_label, project_key_for_model
from ..utils import parse_custom_headers, parse_manual_headers, utc_timestamp

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


class ModelsMixin:
    @staticmethod
    def _format_ms(value) -> str:
        if value is None:
            return "-"
        return f"{value / 1000:.1f}s"

    def _refresh_models(self):
        project = self._project()
        if not project:
            return
        self.remote_list.delete(0, END)
        self.remote_model_entries = []
        keys = _project_keys(project)
        show_key_label = len(keys) > 1
        for item in project.get("discovered_models", []):
            entry = item if isinstance(item, dict) else {"id": str(item), "api_key_id": keys[0]["id"] if keys else ""}
            self.remote_model_entries.append(entry)
            key_label = api_key_label(project_key_for_model(project, entry))
            display = f"{entry['id']}  [{key_label}]" if show_key_label else entry["id"]
            self.remote_list.insert(END, display)
        self.discovered_count.set(f"远程模型 {len(project['discovered_models'])}")

        visible_model_ids = set()
        for index, model in enumerate(project["models"]):
            model_id = model["id"]
            visible_model_ids.add(model_id)
            item_id = self.model_tree_items.get(model_id)
            if item_id is None or not self.model_tree.exists(item_id):
                item_id = f"model_{self.next_model_tree_item}"
                self.next_model_tree_item += 1
                self.model_tree_items[model_id] = item_id
                self.tree_model_ids[item_id] = model_id
                self.model_tree.insert("", END, iid=item_id)
            self._update_model_row(model)
            self.model_tree.move(item_id, "", index)
        for model_id, item_id in list(self.model_tree_items.items()):
            if model_id in visible_model_ids:
                continue
            if self.model_tree.exists(item_id):
                self.model_tree.delete(item_id)
            self.model_tree_items.pop(model_id, None)
            self.tree_model_ids.pop(item_id, None)
        self.added_count.set(f"已添加 {len(project['models'])}")

    def _update_model_row(self, model: dict):
        item_id = self.model_tree_items.get(model["id"])
        if item_id is None or not self.model_tree.exists(item_id):
            return
        status = model.get("status", "未测试")
        first = self._format_ms(model.get("first_ms"))
        total = self._format_ms(model.get("total_ms"))
        detail = (model.get("error") or "").replace("\n", " ")
        key_label = api_key_label(project_key_for_model(self._project(), model))
        tag = {"可用": "ok", "不可用": "fail", "测试中": "testing"}.get(status, "unknown")
        self.model_tree.item(
            item_id,
            text=model["id"],
            values=(key_label, status, first, total, detail[:180]),
            tags=(tag,),
        )

    def _snapshot_clients(self):
        self._commit_form()
        project = self._project()
        try:
            if project.get("headers_mode") == "manual":
                custom_headers = parse_manual_headers(project.get("manual_headers", []))
            else:
                custom_headers = parse_custom_headers(project.get("custom_headers", ""))
            clients = {}
            for key in _project_keys(project):
                clients[key["id"]] = OpenAIClient(
                    project["base_url"],
                    key["value"],
                    project["api_mode"],
                    project.get("test_prompt", ""),
                    custom_headers,
                    project.get("proxy_url", ""),
                    verify_ssl=not bool(project.get("skip_ssl_verify", False)),
                )
            if not clients:
                clients[""] = OpenAIClient(
                    project["base_url"],
                    project.get("api_key", ""),
                    project["api_mode"],
                    project.get("test_prompt", ""),
                    custom_headers,
                    project.get("proxy_url", ""),
                    verify_ssl=not bool(project.get("skip_ssl_verify", False)),
                )
            return project["id"], clients
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror("配置错误", str(exc))
            return None

    def _collect_remote_models(self, clients: dict[str, OpenAIClient]) -> tuple[list[dict], list[str]]:
        found = {}
        errors = []
        for key_id, client in clients.items():
            try:
                model_ids = client.list_models()
            except Exception as exc:
                errors.append(str(exc))
                continue
            for model_id in model_ids:
                found.setdefault(model_id, key_id)
        entries = [{"id": model_id, "api_key_id": key_id} for model_id, key_id in found.items()]
        entries.sort(key=lambda item: item["id"].lower())
        return entries, errors

    def _fetch_models(self):
        snapshot = self._snapshot_clients()
        if not snapshot:
            return
        project_id, clients = snapshot

        def work():
            models, errors = self._collect_remote_models(clients)
            self._post(self._apply_discovered, project_id, models)
            status = f"获取完成：{len(models)} 个模型"
            if errors:
                status += f"，{len(errors)} 个密钥获取失败"
                self._post(self._log, "获取模型列表失败：" + "；".join(errors))
            self._post(self._set_status, status)

        self._start_job("正在获取模型列表...", work)

    def _apply_discovered(self, project_id: str, models: list):
        project = self._project(project_id)
        if not project:
            return
        keys = _project_keys(project)
        default_key_id = keys[0]["id"] if keys else ""
        entries = []
        seen = set()
        for item in models:
            if isinstance(item, dict):
                model_id = str(item.get("id") or "").strip()
                key_id = str(item.get("api_key_id") or "").strip() or default_key_id
            else:
                model_id = str(item).strip()
                key_id = default_key_id
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            entries.append({"id": model_id, "api_key_id": key_id})
        entries.sort(key=lambda item: item["id"].lower())
        project["discovered_models"] = entries
        self._save_store()
        if project_id == self.current_id:
            self._refresh_models()
        self._log(f"获取模型列表：{len(entries)} 个")

    def _add_models(self, model_entries: list):
        project = self._project()
        if not project:
            return
        keys = _project_keys(project)
        default_key_id = keys[0]["id"] if keys else ""
        existing = {model["id"] for model in project["models"]}
        for item in model_entries:
            if isinstance(item, dict):
                model_id = str(item.get("id") or "").strip()
                api_key_id = str(item.get("api_key_id") or "").strip() or default_key_id
            else:
                model_id = str(item).strip()
                api_key_id = default_key_id
            if model_id not in existing:
                project["models"].append(
                    {
                        "id": model_id,
                        "api_key_id": api_key_id,
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
        self._refresh_models()

    def _read_custom_model_id(self):
        model_id = self.custom_model_id.get().strip()
        if not model_id:
            messagebox.showinfo("添加自定义模型", "请输入模型 ID")
            self.custom_model_entry.focus_set()
            return None
        if "\r" in model_id or "\n" in model_id:
            messagebox.showerror("添加自定义模型", "模型 ID 不能包含换行")
            return None
        return model_id

    def _add_custom_model(self, _event=None):
        model_id = self._read_custom_model_id()
        if not model_id:
            return "break"

        project = self._project()
        if project and any(model["id"] == model_id for model in project["models"]):
            messagebox.showinfo("添加自定义模型", "该模型已经在当前项目中")
            self.custom_model_entry.selection_range(0, END)
            return "break"

        self._add_models([model_id])
        self.custom_model_id.set("")
        self.custom_model_entry.focus_set()
        self._log(f"手动添加模型：{model_id}")
        return "break"

    def _add_selected(self):
        models = [
            self.remote_model_entries[index]
            for index in self.remote_list.curselection()
            if index < len(self.remote_model_entries)
        ]
        if not models:
            messagebox.showinfo("添加模型", "请先选择模型")
            return
        self._add_models(models)

    def _add_all(self):
        project = self._project()
        if not project or not project["discovered_models"]:
            messagebox.showinfo("添加模型", "请先获取模型列表")
            return
        self._add_models(project["discovered_models"])

    def _selected_model_ids(self) -> list[str]:
        return [self.tree_model_ids[item] for item in self.model_tree.selection() if item in self.tree_model_ids]

    @staticmethod
    def _show_model_context_menu(event, menu):
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _show_remote_model_menu(self, event):
        if not self.remote_list.size():
            return
        index = self.remote_list.nearest(event.y)
        bounds = self.remote_list.bbox(index)
        if not bounds or not bounds[1] <= event.y <= bounds[1] + bounds[3]:
            return
        self.remote_list.selection_clear(0, END)
        self.remote_list.selection_set(index)
        self.remote_list.activate(index)
        self.context_model_id = (
            self.remote_model_entries[index]["id"]
            if index < len(self.remote_model_entries)
            else self.remote_list.get(index)
        )
        return self._show_model_context_menu(event, self.remote_model_context_menu)

    def _show_project_model_menu(self, event):
        item = self.model_tree.identify_row(event.y)
        model_id = self.tree_model_ids.get(item)
        self.context_model_id = model_id
        row_state = "normal" if model_id else "disabled"
        self.project_model_context_menu.entryconfigure(0, state=row_state)
        self.project_model_context_menu.entryconfigure(2, state=row_state)
        self.project_model_context_menu.entryconfigure(4, state=row_state)
        if model_id:
            self.model_tree.selection_set(item)
            self.model_tree.focus(item)
        return self._show_model_context_menu(event, self.project_model_context_menu)

    def _copy_model_name(self, model_id: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(model_id)
        self.root.update_idletasks()
        self.status.set(f"已复制模型：{model_id}")

    def _copy_context_model(self):
        if self.context_model_id:
            self._copy_model_name(self.context_model_id)

    def _remove_context_model(self):
        model_id = self.context_model_id
        project = self._project()
        if not model_id or not project:
            return
        original_count = len(project["models"])
        project["models"] = [model for model in project["models"] if model["id"] != model_id]
        if len(project["models"]) == original_count:
            return
        self._save_store()
        self._refresh_models()
        self.status.set(f"已移除模型：{model_id}")
        self._log(f"移除模型：{model_id}")
        self.context_model_id = None

    def _copy_selected_remote_model(self, _event=None):
        selection = self.remote_list.curselection()
        if not selection:
            return
        index = selection[0]
        model_id = (
            self.remote_model_entries[index]["id"]
            if index < len(self.remote_model_entries)
            else self.remote_list.get(index)
        )
        self._copy_model_name(model_id)
        return "break"

    def _copy_selected_project_model(self, _event=None):
        selected = self._selected_model_ids()
        if not selected:
            return
        self._copy_model_name(selected[0])
        return "break"

    def _remove_selected(self):
        selected = set(self._selected_model_ids())
        if not selected:
            return
        project = self._project()
        project["models"] = [model for model in project["models"] if model["id"] not in selected]
        self._save_store()
        self._refresh_models()

    def _remove_all(self):
        project = self._project()
        if not project or not project["models"]:
            return
        if messagebox.askyesno("移除全部", "确定移除当前项目的全部模型吗？"):
            project["models"] = []
            self._save_store()
            self._refresh_models()

    def _detect_all_available(self):
        snapshot = self._snapshot_clients()
        if not snapshot:
            return
        project_id, clients = snapshot

        def work():
            model_entries, errors = self._collect_remote_models(clients)
            if errors:
                self._post(self._log, "检测全部模型时部分密钥失败：" + "；".join(errors))
            if not model_entries:
                self._post(self._set_status, "未获取到可用模型")
                return
            probe_entries = [entry for entry in model_entries if self._is_text_probe_suitable(entry["id"])]
            skipped = len(model_entries) - len(probe_entries)
            if skipped:
                self._post(self._log, f"跳过 {skipped} 个不适合文本测活的模型（图像/视频/音频/Embedding 等）")
            if not probe_entries:
                self._post(self._set_status, "没有适合文本测活的模型")
                return
            self._post(self._apply_discovered, project_id, model_entries)
            self._pending_detected = []

            def on_result(entry, result, completed, total):
                if result["ok"]:
                    self._pending_detected.append((entry, result))
                if len(self._pending_detected) >= PROBE_UI_BATCH_SIZE:
                    pending = self._pending_detected[:]
                    self._pending_detected.clear()
                    self._post(self._add_detected_available_batch, project_id, pending, completed, total)

            results = self._run_probes(
                clients,
                probe_entries,
                project_id,
                progress_label="检测全部模型",
                update_existing=False,
                on_result=on_result,
            )
            if self._pending_detected:
                self._post(
                    self._add_detected_available_batch,
                    project_id,
                    self._pending_detected[:],
                    len(results),
                    len(probe_entries),
                )
                self._pending_detected.clear()
            entry_by_id = {entry["id"]: entry for entry in probe_entries}
            available = [
                self._entry_from_result(entry_by_id[model_id], result) for model_id, result in results if result["ok"]
            ]
            self._post(self._replace_available, project_id, available, len(probe_entries))

        self._start_job("正在获取并检测全部模型...", work)

    @staticmethod
    def _is_text_probe_suitable(model_id: str) -> bool:
        lowered = model_id.lower()
        return not any(keyword in lowered for keyword in _SKIP_PROBE_KEYWORDS)

    def _add_detected_available_batch(self, project_id: str, pending: list, completed: int, total: int):
        project = self._project(project_id)
        if not project:
            return
        models = {model["id"]: model for model in project["models"]}
        for entry, result in pending:
            model = models.get(entry["id"])
            if model is None:
                model = self._entry_from_result(entry, result)
                project["models"].append(model)
                models[entry["id"]] = model
            else:
                model.update({key: value for key, value in result.items() if key != "ok"})
        project["models"].sort(key=lambda item: item["id"].lower())
        self._schedule_probe_save()
        if project_id == self.current_id:
            for entry, _ in pending:
                model = models[entry["id"]]
                item_id = self.model_tree_items.get(entry["id"])
                if item_id is None or not self.model_tree.exists(item_id):
                    item_id = f"model_{self.next_model_tree_item}"
                    self.next_model_tree_item += 1
                    self.model_tree_items[entry["id"]] = item_id
                    self.tree_model_ids[item_id] = entry["id"]
                    self.model_tree.insert("", END, iid=item_id)
                self._update_model_row(model)
            self.added_count.set(f"已添加 {len(project['models'])}")
        self.status.set(f"检测全部模型：{completed}/{total}")

    def _replace_available(self, project_id: str, available: list[dict], total: int):
        project = self._project(project_id)
        if not project:
            return
        project["models"] = sorted(available, key=lambda item: item["id"].lower())
        self._flush_probe_save()
        if project_id == self.current_id:
            self._refresh_models()
        self.status.set(f"检测完成：{len(available)}/{total} 个模型可用")
        self._log(f"仅添加可用模型：{len(available)}/{total}")

    def _test_selected(self):
        model_ids = self._selected_model_ids()
        if not model_ids:
            messagebox.showinfo("测试模型", "请先选择模型")
            return
        self._test_models(model_ids)

    def _test_all(self):
        project = self._project()
        if not project or not project["models"]:
            messagebox.showinfo("测试模型", "当前项目还没有模型")
            return
        model_ids = [model["id"] for model in project["models"] if self._is_text_probe_suitable(model["id"])]
        if not model_ids:
            messagebox.showinfo("测试模型", "当前项目没有适合文本测活的模型")
            return
        skipped = len(project["models"]) - len(model_ids)
        if skipped:
            self._log(f"测活全部：跳过 {skipped} 个不适合文本测活的模型")
        self._test_models(model_ids)

    def _test_models(self, model_ids: list[str]):
        snapshot = self._snapshot_clients()
        if not snapshot:
            return
        project_id, clients = snapshot
        project = self._project(project_id)
        model_by_id = {model["id"]: model for model in project["models"]} if project else {}
        model_entries = [
            {
                "id": model_id,
                "api_key_id": model_by_id.get(model_id, {}).get("api_key_id", ""),
            }
            for model_id in model_ids
        ]
        self._mark_testing(project_id, model_ids)

        def work():
            results = self._run_probes(
                clients,
                model_entries,
                project_id,
                progress_label="测试模型",
                update_existing=True,
            )
            ok_count = sum(1 for _, result in results if result["ok"])
            self._post(self._set_status, f"测试完成：{ok_count}/{len(model_entries)} 个模型可用")

        self._start_job(f"正在测试 {len(model_entries)} 个模型...", work)

    def _run_probes(
        self,
        clients: dict[str, OpenAIClient],
        model_entries: list[dict],
        project_id: str,
        progress_label: str,
        update_existing: bool,
        on_result=None,
    ):
        if not model_entries:
            return []
        default_client = clients.get("") or next(iter(clients.values()), None)

        def probe(entry: dict):
            client = clients.get(entry.get("api_key_id")) or default_client
            return client.probe(entry["id"])

        results = []
        pending = []
        workers = min(MAX_WORKERS, len(model_entries))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {pool.submit(probe, entry): entry for entry in model_entries}
            for completed, future in enumerate(as_completed(future_map), start=1):
                entry = future_map[future]
                model_id = entry["id"]
                try:
                    result = future.result(timeout=PROBE_TIMEOUT + 3)
                except Exception as exc:
                    if isinstance(exc, FutureTimeoutError):
                        error_text = f"超时（{PROBE_TIMEOUT:.0f}s 未响应）"
                        total_ms = round((PROBE_TIMEOUT + 3) * 1000)
                    else:
                        error_text = str(exc)
                        total_ms = None
                    result = {
                        "ok": False,
                        "status": "不可用",
                        "first_ms": None,
                        "total_ms": total_ms,
                        "reply": "",
                        "error": error_text,
                        "tested_at": utc_timestamp(),
                    }
                results.append((model_id, result))
                pending.append((model_id, result))
                if on_result:
                    on_result(entry, result, completed, len(model_entries))
                if len(pending) >= PROBE_UI_BATCH_SIZE:
                    self._post(
                        self._apply_probe_results,
                        project_id,
                        pending,
                        progress_label,
                        completed,
                        len(model_entries),
                        update_existing,
                    )
                    pending = []
        if pending:
            self._post(
                self._apply_probe_results,
                project_id,
                pending,
                progress_label,
                len(model_entries),
                len(model_entries),
                update_existing,
            )
        if update_existing:
            self._post(self._flush_probe_save)
        return results

    @staticmethod
    def _entry_from_result(entry: dict, result: dict) -> dict:
        return {
            "id": entry["id"],
            "api_key_id": entry.get("api_key_id", ""),
            **{key: value for key, value in result.items() if key != "ok"},
        }

    def _mark_testing(self, project_id: str, model_ids: list[str]):
        project = self._project(project_id)
        if not project:
            return
        target = set(model_ids)
        for model in project["models"]:
            if model["id"] in target:
                model["status"] = "测试中"
                if project_id == self.current_id:
                    self._update_model_row(model)

    def _apply_probe_results(
        self,
        project_id: str,
        results: list[tuple[str, dict]],
        progress_label: str,
        completed: int,
        total: int,
        update_existing: bool,
    ):
        project = self._project(project_id) if update_existing else None
        models = {item["id"]: item for item in project["models"]} if project else {}
        changed = False
        for model_id, result in results:
            model = models.get(model_id)
            if model is not None:
                model.update({key: value for key, value in result.items() if key != "ok"})
                changed = True
                if project_id == self.current_id:
                    self._update_model_row(model)
        if changed:
            self._schedule_probe_save()
        ok_in_batch = sum(1 for _, result in results if result["ok"])
        self._log(f"{progress_label}：{completed}/{total}，本批可用 {ok_in_batch}/{len(results)}")
        self.status.set(f"{progress_label}：{completed}/{total}")

    def _schedule_probe_save(self):
        if self.probe_save_after:
            self.root.after_cancel(self.probe_save_after)
        self.probe_save_after = self.root.after(500, self._flush_probe_save)

    def _flush_probe_save(self):
        if self.probe_save_after:
            self.root.after_cancel(self.probe_save_after)
            self.probe_save_after = None
        self._save_store()

    def _show_model_detail(self, _event=None):
        selected = self._selected_model_ids()
        if not selected:
            return
        project = self._project()
        model = next((item for item in project["models"] if item["id"] == selected[0]), None)
        if not model:
            return
        detail = model.get("error")
        if detail:
            messagebox.showinfo(model["id"], detail)

    def _start_job(self, label: str, work):
        if self.busy:
            messagebox.showinfo("任务进行中", "请等待当前网络任务完成")
            return
        self.busy = True
        self.root.configure(cursor="watch")
        self.status.set(label)
        for button in self.network_buttons:
            button.state(["disabled"])

        def runner():
            try:
                work()
            except Exception as exc:
                self._post(self._job_error, str(exc))
            finally:
                self._post(self._finish_job)

        threading.Thread(target=runner, daemon=True).start()

    def _finish_job(self):
        self.busy = False
        self.root.configure(cursor="")
        for button in self.network_buttons:
            button.state(["!disabled"])

    def _job_error(self, error: str):
        self.status.set(f"失败：{error}")
        self._log(f"ERROR {error}")
        messagebox.showerror("请求失败", error)

    def _set_status(self, value: str):
        self.status.set(value)

    def _post(self, callback, *args):
        self.events.put((callback, args))

    def _poll_events(self):
        processed = 0
        try:
            while processed < 40:
                callback, args = self.events.get_nowait()
                callback(*args)
                processed += 1
        except queue.Empty:
            pass
        # Yield to the UI event loop between bursts; back off while idle.
        if processed:
            self.root.after(10, self._poll_events)
        else:
            self.root.after(200, self._poll_events)

    def _log(self, message: str):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert(END, f"[{stamp}] {message}\n")
        self.log_text.see(END)
        self.log_text.configure(state="disabled")

    def _on_close(self):
        if self.save_timer:
            self.root.after_cancel(self.save_timer)
        self._commit_form()
        self.usage_stats.save()
        if self.relay_server:
            self.relay_server.stop()
            self.relay_server = None
        self.root.destroy()
