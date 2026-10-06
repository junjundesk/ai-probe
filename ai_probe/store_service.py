"""GUI-independent configuration storage and normalization services."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from .config import (
    DATA_FILE,
    _derive_config_key,
    decrypt_config,
    encrypt_config,
    save_config_key,
)
from .projects import _project_keys, _sync_project_keys, new_project, project_key_by_id


def default_store() -> dict:
    return {
        "version": 2,
        "selected_project_id": None,
        "projects": [new_project("默认项目")],
        "relay": {
            "host": "127.0.0.1",
            "port": 8040,
            "api_key": "",
            "user_agent": "",
            "project_ids": [],
            "error_logging_enabled": True,
            "request_logging_enabled": True,
            "request_debug_capture": False,
            "system_prompt": "",
            "append_user_prompt": True,
        },
    }


def normalize_store(data: dict) -> dict:
    relay_default = default_store()["relay"]
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
            item["route_name"] = str(item.get("route_name") or "").strip()
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
    relay["host"] = str(relay.get("host") or "127.0.0.1")
    try:
        relay["port"] = int(relay.get("port", 8040))
    except (TypeError, ValueError):
        relay["port"] = 8040
    relay["api_key"] = str(relay.get("api_key") or "")
    relay["user_agent"] = str(relay.get("user_agent") or "")
    relay["project_ids"] = [item for item in relay.get("project_ids", []) if isinstance(item, str)]
    relay["error_logging_enabled"] = bool(relay.get("error_logging_enabled", True))
    relay["request_logging_enabled"] = bool(relay.get("request_logging_enabled", True))
    relay["request_debug_capture"] = bool(relay.get("request_debug_capture", False))
    relay["system_prompt"] = str(relay.get("system_prompt") or "")
    relay["append_user_prompt"] = bool(relay.get("append_user_prompt", True))
    data["version"] = 2
    return data


class StoreService:
    """Atomic encrypted store access shared by Qt and lightweight relay modes."""

    def __init__(self, config_key: bytes, data_file: Path = DATA_FILE):
        self.config_key = config_key
        self.data_file = Path(data_file)
        self.loaded_plaintext = False

    def load(self) -> dict:
        if not self.data_file.exists():
            return default_store()
        try:
            raw = self.data_file.read_text(encoding="utf-8")
            data, encrypted = decrypt_config(raw, self.config_key, allow_legacy=False)
            self.loaded_plaintext = not encrypted
            return normalize_store(data)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            raise RuntimeError(f"无法载入配置：{exc}") from exc

    @staticmethod
    def write_encrypted_file(path: Path, data: dict, secret: str | bytes):
        path = Path(path)
        temp = path.with_name(f".{path.name}.tmp")
        temp.write_text(encrypt_config(data, secret), encoding="utf-8")
        temp.replace(path)

    @staticmethod
    def write_plain_file(path: Path, data: dict):
        path = Path(path)
        temp = path.with_name(f".{path.name}.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def save(self, data: dict):
        self.write_encrypted_file(self.data_file, data, self.config_key)

    def import_payload(self, path: Path, secret: str | None = None) -> tuple[dict, bytes | None]:
        raw = Path(path).read_text(encoding="utf-8")
        payload = json.loads(raw)
        encrypted = isinstance(payload, dict) and payload.get("format")
        if encrypted:
            if secret is None:
                raise ValueError("导入加密配置需要密码")
            imported, _ = decrypt_config(raw, secret, allow_legacy=False)
            return normalize_store(imported), _derive_config_key(secret)
        return normalize_store(payload), None

    def activate_import(self, data: dict, imported_key: bytes | None = None):
        if imported_key is not None:
            self.config_key = imported_key
        self.save(data)
        save_config_key(self.config_key)
