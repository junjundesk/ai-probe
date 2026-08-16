"""配置存储、导入导出与加密迁移。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from tkinter import StringVar, Toplevel, filedialog, messagebox, simpledialog, ttk

from ..config import (
    CONFIG_FORMAT,
    DATA_FILE,
    MIN_CONFIG_SECRET_LENGTH,
    _derive_config_key,
    decrypt_config,
    encrypt_config,
    save_config_key,
)
from ..projects import _project_keys, _sync_project_keys, new_project, project_key_by_id


class StoreMixin:
    def _default_store(self) -> dict:
        relay_default = {
            "host": "127.0.0.1",
            "port": 8040,
            "api_key": "",
            "project_ids": [],
            "error_logging_enabled": True,
        }
        return {
            "version": 2,
            "selected_project_id": None,
            "projects": [new_project("默认项目")],
            "relay": relay_default,
        }

    @staticmethod
    def _normalize_store(data: dict) -> dict:
        relay_default = {
            "host": "127.0.0.1",
            "port": 8040,
            "api_key": "",
            "project_ids": [],
            "error_logging_enabled": True,
        }
        if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
            raise ValueError("配置文件缺少有效的 projects 列表")
        for project in data["projects"]:
            if not isinstance(project, dict):
                raise ValueError("项目配置格式无效")
            project.setdefault("id", uuid.uuid4().hex)
            project.setdefault("name", "未命名项目")
            project.setdefault("base_url", "")
            project.setdefault("api_key", "")
            project.setdefault("api_keys", [])
            project.setdefault("proxy_url", "")
            project.setdefault("skip_ssl_verify", False)
            project.setdefault("api_mode", "chat")
            project.setdefault("test_prompt", "")
            project.setdefault("headers_mode", "json")
            project.setdefault("custom_headers", "")
            project.setdefault("manual_headers", [])
            project.setdefault("discovered_models", [])
            project.setdefault("models", [])
            _sync_project_keys(project)
            default_key_id = _project_keys(project)[0]["id"]
            discovered = []
            seen_discovered = set()
            for item in project.get("discovered_models", []):
                if isinstance(item, dict):
                    model_id = str(item.get("id") or "").strip()
                    key_id = str(item.get("api_key_id") or "").strip() or default_key_id
                else:
                    model_id = str(item).strip()
                    key_id = default_key_id
                if project_key_by_id(project, key_id) is None:
                    key_id = default_key_id
                if not model_id or model_id in seen_discovered:
                    continue
                seen_discovered.add(model_id)
                discovered.append({"id": model_id, "api_key_id": key_id})
            project["discovered_models"] = discovered
            models = []
            seen_models = set()
            for item in project.get("models", []):
                if not isinstance(item, dict):
                    item = {"id": str(item or "")}
                model_id = str(item.get("id") or "").strip()
                if not model_id or model_id in seen_models:
                    continue
                seen_models.add(model_id)
                key_id = str(item.get("api_key_id") or "").strip() or default_key_id
                if project_key_by_id(project, key_id) is None:
                    key_id = default_key_id
                item["api_key_id"] = key_id
                models.append(item)
            project["models"] = models
        if not data["projects"]:
            data["projects"].append(new_project("默认项目"))
        relay = data.setdefault("relay", {})
        if not isinstance(relay, dict):
            relay = {}
            data["relay"] = relay
        for key, value in relay_default.items():
            relay.setdefault(key, value)
        relay["project_ids"] = [
            project_id for project_id in relay.get("project_ids", []) if isinstance(project_id, str)
        ]
        relay["error_logging_enabled"] = bool(relay.get("error_logging_enabled", True))
        data["version"] = 2
        return data

    def _load_store(self) -> dict:
        default = self._default_store()
        self._loaded_plaintext = False
        if not DATA_FILE.exists():
            return default
        try:
            raw = DATA_FILE.read_text(encoding="utf-8")
            data, encrypted = decrypt_config(raw, self.config_key, allow_legacy=False)
            self._loaded_plaintext = not encrypted
            return self._normalize_store(data)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            raise RuntimeError(f"无法载入配置：{exc}") from exc

    @staticmethod
    def _write_encrypted_file(path: Path, data: dict, secret: str | bytes):
        temp = path.with_name(f".{path.name}.tmp")
        temp.write_text(encrypt_config(data, secret), encoding="utf-8")
        temp.replace(path)

    @staticmethod
    def _write_plain_file(path: Path, data: dict):
        temp = path.with_name(f".{path.name}.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def _save_store(self):
        if self.relay_server:
            self.relay_server.invalidate_routes()
        try:
            self._write_encrypted_file(DATA_FILE, self.store, self.config_key)
        except (OSError, RuntimeError) as exc:
            self.status.set(f"保存失败：{exc}")

    def _backup_config(self):
        self._commit_form()
        encrypted = messagebox.askyesnocancel(
            "备份格式",
            "是否使用 AES 加密？\n\n选择“是”导出加密文件，选择“否”导出明文 JSON。",
            parent=self.root,
        )
        if encrypted is None:
            return
        path = filedialog.asksaveasfilename(
            title="备份配置",
            initialfile="ai_probe_projects_backup.json",
            defaultextension=".json",
            filetypes=[("AI Probe 配置", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        if not encrypted:
            try:
                self._write_plain_file(Path(path), self.store)
                self.status.set(f"明文备份完成：{Path(path).name}")
                self._log(f"已备份明文配置：{path}")
            except OSError as exc:
                messagebox.showerror("备份失败", str(exc))
            return
        secret = self._ask_new_secret("设置备份密钥", "请设置用于分享和导入此备份的加密密码：")
        if not secret:
            return
        try:
            self._write_encrypted_file(Path(path), self.store, secret)
            self.status.set(f"备份完成：{Path(path).name}")
            self._log(f"已备份加密配置：{path}")
            self._show_secret_dialog("备份解密密码", secret, "请将此密码与备份文件分开分享或保存。")
        except (OSError, RuntimeError) as exc:
            messagebox.showerror("备份失败", str(exc))

    def _encrypt_config_file(self):
        source = filedialog.askopenfilename(
            title="选择要加密的明文配置",
            filetypes=[("JSON 配置", "*.json"), ("所有文件", "*.*")],
        )
        if not source:
            return
        try:
            payload = json.loads(Path(source).read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("format") == CONFIG_FORMAT:
                raise ValueError("该文件已经是 AES 加密配置")
            payload = self._normalize_store(payload)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("加密失败", str(exc))
            return
        secret = self._ask_new_secret("设置加密密钥", "请设置该配置文件的 AES 加密密码：")
        if not secret:
            return
        source_path = Path(source)
        target = filedialog.asksaveasfilename(
            title="保存加密配置",
            initialfile=f"{source_path.stem}_encrypted.json",
            defaultextension=".json",
            filetypes=[("AI Probe 配置", "*.json"), ("所有文件", "*.*")],
        )
        if not target:
            return
        try:
            self._write_encrypted_file(Path(target), payload, secret)
            self.status.set(f"加密完成：{Path(target).name}")
            self._log(f"已手动加密配置：{target}")
            self._show_secret_dialog("文件解密密码", secret, "解密或导入该文件时需要此密码。")
        except (OSError, RuntimeError) as exc:
            messagebox.showerror("加密失败", str(exc))

    def _import_config(self):
        path = filedialog.askopenfilename(
            title="导入加密配置",
            filetypes=[("AI Probe 配置", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        if not messagebox.askyesno("导入配置", "导入后将替换当前全部项目，是否继续？"):
            return
        try:
            raw = Path(path).read_text(encoding="utf-8")
            payload = json.loads(raw)
            encrypted = isinstance(payload, dict) and payload.get("format") == CONFIG_FORMAT
            imported = payload
            imported_key = None
            if encrypted:
                secret = simpledialog.askstring(
                    "解密密钥", "请输入该备份配置的 AES 解密密码：", show="*", parent=self.root
                )
                if secret is None:
                    return
                secret = secret.strip()
                if len(secret) < MIN_CONFIG_SECRET_LENGTH:
                    raise ValueError("解密密码不能为空")
                imported, _encrypted = decrypt_config(raw, secret, allow_legacy=False)
                imported_key = _derive_config_key(secret)
            imported = self._normalize_store(imported)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            messagebox.showerror("导入失败", str(exc))
            return
        self._commit_form()
        target_key = imported_key or self.config_key
        try:
            self._write_encrypted_file(DATA_FILE, imported, target_key)
            save_config_key(target_key)
        except (OSError, RuntimeError) as exc:
            messagebox.showerror("导入失败", f"无法保存导入配置：{exc}")
            return
        self.config_key = target_key
        self.store = imported
        self.current_id = self.store.get("selected_project_id")
        self._ensure_selection()
        self._refresh_project_list()
        if self.relay_window and self.relay_window.winfo_exists():
            self._refresh_relay_projects()
        self.status.set(f"导入完成：{len(self.store['projects'])} 个项目")
        self._log(f"已导入配置：{path}")

    def _ask_new_secret(self, title: str, prompt: str) -> str | None:
        secret = simpledialog.askstring(title, prompt, show="*", parent=self.root)
        if secret is None:
            return None
        secret = secret.strip()
        if len(secret) < MIN_CONFIG_SECRET_LENGTH:
            messagebox.showerror(title, "加密密码不能为空")
            return None
        return secret

    def _show_secret_dialog(self, title: str, secret: str, message: str):
        window = Toplevel(self.root)
        window.title(title)
        window.transient(self.root)
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", window.destroy)

        body = ttk.Frame(window, padding=18)
        body.grid(row=0, column=0, sticky="nsew")
        ttk.Label(body, text=title, style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            body,
            text=message,
            style="Muted.TLabel",
            wraplength=390,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(7, 12))
        key_var = StringVar(value=secret)
        key_entry = ttk.Entry(body, textvariable=key_var, width=48)
        key_entry.grid(row=2, column=0, sticky="ew", padx=(0, 8))
        key_entry.configure(state="readonly")

        def copy_key():
            self.root.clipboard_clear()
            self.root.clipboard_append(secret)
            self.root.update_idletasks()
            self.status.set("解密密码已复制")

        self._mac_button(body, "复制密钥", copy_key, surface="#ffffff").grid(row=2, column=1)
        self._mac_button(body, "关闭", window.destroy, kind="primary", surface="#ffffff").grid(
            row=3, column=0, columnspan=2, sticky="e", pady=(15, 0)
        )
        window.grab_set()
        window.focus_force()
