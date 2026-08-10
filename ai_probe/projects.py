"""项目与多 API Key 的持久化数据模型辅助函数。"""

from __future__ import annotations

import uuid

from .client import OpenAIClient
from .utils import parse_custom_headers, parse_manual_headers


def new_project(name: str = "新项目") -> dict:
    return {
        "id": uuid.uuid4().hex,
        "name": name,
        "base_url": "",
        "api_key": "",
        "api_keys": [],
        "proxy_url": "",
        "api_mode": "chat",
        "test_prompt": "",
        "headers_mode": "json",
        "custom_headers": "",
        "manual_headers": [],
        "discovered_models": [],
        "models": [],
    }


def _project_keys(project: dict) -> list[dict]:
    """Return normalized API keys for a project, preserving their ids."""
    raw = project.get("api_keys")
    if not isinstance(raw, list):
        raw = []
    keys = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            item = {"id": "", "name": "", "value": str(item or "")}
        key_id = str(item.get("id") or "").strip() or uuid.uuid4().hex
        if key_id in seen:
            continue
        seen.add(key_id)
        keys.append(
            {
                "id": key_id,
                "name": str(item.get("name") or "").strip(),
                "value": str(item.get("value") or ""),
            }
        )
    if not keys:
        keys.append({"id": "default", "name": "默认", "value": str(project.get("api_key") or "")})
    return keys


def _sync_project_keys(project: dict) -> list[dict]:
    keys = _project_keys(project)
    project["api_keys"] = keys
    project["api_key"] = keys[0]["value"] if keys else str(project.get("api_key") or "")
    return keys


def project_key_by_id(project: dict, key_id) -> dict | None:
    if not key_id:
        return None
    for key in _project_keys(project):
        if key["id"] == key_id:
            return key
    return None


def project_key_for_model(project: dict, model) -> dict:
    key_id = model.get("api_key_id") if isinstance(model, dict) else None
    key = project_key_by_id(project, key_id)
    if key:
        return key
    keys = _project_keys(project)
    return keys[0] if keys else {"id": "", "name": "默认", "value": str(project.get("api_key") or "")}


def api_key_label(key: dict) -> str:
    name = str(key.get("name") or "").strip()
    value = str(key.get("value") or "")
    tail = value[-4:] if value else ""
    if name and value:
        return f"{name} · ...{tail}"
    if value:
        return f"...{tail}"
    return name or "默认"


def client_from_project(project: dict, api_key_id: str | None = None) -> OpenAIClient:
    """Build an upstream client from persisted project data."""
    if project.get("headers_mode") == "manual":
        headers = parse_manual_headers(project.get("manual_headers", []))
    else:
        headers = parse_custom_headers(project.get("custom_headers", ""))
    key = project_key_by_id(project, api_key_id)
    if key is None:
        keys = _project_keys(project)
        key = keys[0] if keys else {"id": "", "name": "默认", "value": project.get("api_key", "")}
    return OpenAIClient(
        project.get("base_url", ""),
        key["value"],
        project.get("api_mode", "chat"),
        project.get("test_prompt", ""),
        headers,
        project.get("proxy_url", ""),
    )
