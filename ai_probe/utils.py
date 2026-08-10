"""无状态的输入规范化、响应解析与展示辅助函数。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _int_field(mapping, *keys) -> int:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
    return 0


def compact_number(value: int | float) -> str:
    number = max(0, int(value or 0))
    units = ("", "k", "m", "b")
    scaled = float(number)
    unit_index = 0
    while scaled >= 1000 and unit_index < len(units) - 1:
        scaled /= 1000
        unit_index += 1
    if unit_index == 0:
        return str(number)
    scaled = round(scaled, 1)
    if scaled >= 1000 and unit_index < len(units) - 1:
        scaled /= 1000
        unit_index += 1
    return f"{scaled:.1f}".rstrip("0").rstrip(".") + units[unit_index]


def format_cache_summary(input_tokens: int | float, cached_tokens: int | float) -> str:
    input_tokens = max(0, int(input_tokens or 0))
    cached_tokens = max(0, int(cached_tokens or 0))
    if not input_tokens:
        return "-"
    return f"{cached_tokens / input_tokens * 100:.1f}%/{compact_number(cached_tokens)}"


def extract_usage_tokens(usage, api_mode: str) -> tuple[int, int, int]:
    """Return (input_tokens, output_tokens, cached_tokens) from an upstream usage dict."""
    if not isinstance(usage, dict):
        return 0, 0, 0
    if api_mode == "chat":
        input_tokens = _int_field(usage, "prompt_tokens")
        output_tokens = _int_field(usage, "completion_tokens")
        details = usage.get("prompt_tokens_details")
    else:
        input_tokens = _int_field(usage, "input_tokens")
        output_tokens = _int_field(usage, "output_tokens")
        details = usage.get("input_tokens_details")
    cached_tokens = _int_field(details, "cached_tokens") if isinstance(details, dict) else 0
    cached_tokens = max(
        cached_tokens,
        _int_field(usage, "cache_read_input_tokens", "prompt_cache_hit_tokens", "cache_hit_tokens"),
    )
    return input_tokens, output_tokens, cached_tokens


def normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("API 地址必须是完整的 http:// 或 https:// 地址")

    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/messages", "/models"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    if not path:
        path = "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def normalize_proxy_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if any(char in value for char in "\r\n"):
        raise ValueError("HTTP 代理地址不能包含换行")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname:
        raise ValueError("代理地址必须是 http(s)://、socks5:// 或 socks5h:// 地址")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("代理地址不应包含路径、查询参数或片段")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("代理端口无效") from exc
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, "", "", ""))


def parse_custom_headers(value: str) -> dict[str, str]:
    value = value.strip()
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"自定义请求头不是有效 JSON：{exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("自定义请求头必须是 JSON 对象")

    headers = {}
    for name, header_value in payload.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("自定义请求头名称不能为空")
        if header_value is None or isinstance(header_value, (dict, list)):
            raise ValueError(f"请求头 {name} 的值必须是字符串、数字或布尔值")
        name = name.strip()
        header_value = str(header_value)
        if "\r" in name or "\n" in name or "\r" in header_value or "\n" in header_value:
            raise ValueError(f"请求头 {name} 包含非法换行")
        headers[name] = header_value
    return headers


def parse_manual_headers(rows: list[dict]) -> dict[str, str]:
    headers = {}
    names = set()
    for row in rows:
        name = str(row.get("name", "")).strip()
        value = str(row.get("value", "")).strip()
        if not name and not value:
            continue
        if not name:
            raise ValueError("手动请求头的名称不能为空")
        if "\r" in name or "\n" in name or "\r" in value or "\n" in value:
            raise ValueError(f"请求头 {name} 包含非法换行")
        normalized_name = name.lower()
        if normalized_name in names:
            raise ValueError(f"请求头 {name} 重复")
        names.add(normalized_name)
        headers[name] = value
    return headers


def extract_stream_text(payload: dict, api_mode: str) -> str:
    if api_mode == "chat":
        choices = payload.get("choices") or []
        if choices:
            choice = choices[0] or {}
            content = (choice.get("delta") or {}).get("content")
            if content is None:
                content = (choice.get("message") or {}).get("content")
            if content is None:
                content = choice.get("text")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and isinstance(item.get("text"), str)
                )
        return ""

    if api_mode == "anthropic":
        delta = payload.get("delta") or {}
        if isinstance(delta, dict) and isinstance(delta.get("text"), str):
            return delta["text"]
        content_block = payload.get("content_block") or {}
        if isinstance(content_block, dict) and isinstance(content_block.get("text"), str):
            return content_block["text"]
        content = payload.get("content") or []
        if isinstance(content, list):
            return "".join(
                item.get("text", "") for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)
            )
        return ""

    event_type = str(payload.get("type", ""))
    delta = payload.get("delta")
    if isinstance(delta, str) and ("output_text" in event_type or event_type.endswith(".delta")):
        return delta
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    return ""


def error_message_from_response(response) -> str:
    try:
        payload = response.json()
        error = payload.get("error", payload) if isinstance(payload, dict) else payload
        if isinstance(error, dict):
            message = error.get("message") or error.get("detail") or error.get("type")
            if message:
                return str(message)
        if isinstance(error, str):
            return error
    except (ValueError, TypeError):
        pass
    text = (response.text or "").strip().replace("\n", " ")
    return text[:500] or response.reason or "请求失败"


def _is_function_only_tools_error(message: str) -> bool:
    text = str(message).lower()
    return (
        "tools[" in text
        and "custom" in text
        and "function" in text
        and ("expected" in text or "unknown variant" in text)
    )
