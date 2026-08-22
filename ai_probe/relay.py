"""本地兼容转发服务器。"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

from .client import OpenAIClient
from .config import RELAY_ERROR_LOG
from .projects import client_from_project
from .protocols import (
    _canonical_stream_events,
    _collect_stream_result,
    _convert_request,
    _custom_tool_names,
    _include_stream_usage,
    _request_mode,
    _response_for_mode,
    _responses_custom_to_function,
    _SSEUsageCollector,
    _StreamRenderer,
)
from .utils import _is_function_only_tools_error, error_message_from_response, extract_usage_tokens

_CALL_OUTPUT_TYPES = {
    "function_call": "function_call_output",
    "custom_tool_call": "custom_tool_call_output",
    "local_shell_call": "local_shell_call_output",
}


class _ResponsesStreamTracker:
    """Track enough Responses SSE state to terminate a truncated passthrough stream."""

    def __init__(self, model: str):
        self.buffer = bytearray()
        self.id = "relay"
        self.model = model
        self.created_at = int(time.time())
        self.sequence_number = -1
        self.started = False
        self.has_output = False
        self.completed = False

    def feed(self, chunk: bytes):
        self.buffer.extend(chunk)
        while (newline := self.buffer.find(b"\n")) >= 0:
            line = bytes(self.buffer[:newline]).rstrip(b"\r")
            del self.buffer[: newline + 1]
            self._handle_line(line)

    def completion(self) -> bytes | None:
        if not self.started or not self.has_output or self.completed:
            return None
        response = {
            "id": self.id,
            "object": "response",
            "created_at": self.created_at,
            "status": "completed",
            "model": self.model,
            "output": [],
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }
        payload = {
            "type": "response.completed",
            "sequence_number": self.sequence_number + 1,
            "response": response,
        }
        self.completed = True
        return f"event: response.completed\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()

    def _handle_line(self, line: bytes):
        if not line.startswith(b"data:"):
            return
        try:
            payload = json.loads(line[5:].strip().decode("utf-8", "replace"))
        except ValueError:
            return
        if not isinstance(payload, dict):
            return
        event_type = payload.get("type", "")
        response = payload.get("response")
        if isinstance(response, dict):
            self.id = response.get("id") or self.id
            self.model = response.get("model") or self.model
            self.created_at = response.get("created_at") or self.created_at
        if isinstance(payload.get("sequence_number"), int):
            self.sequence_number = payload["sequence_number"]
        self.started = self.started or event_type in {"response.created", "response.in_progress"}
        self.has_output = self.has_output or event_type.startswith(
            ("response.output_", "response.reasoning_", "response.function_", "response.custom_")
        )
        self.completed = self.completed or event_type == "response.completed"


def _sanitize_responses_input(body: dict) -> dict:
    """Drop content-less assistant items that break strict tool-call pairing."""
    source = body.get("input")
    if not isinstance(source, list):
        return body
    filtered = [
        item
        for item in source
        if not (
            isinstance(item, dict)
            and item.get("type") == "message"
            and item.get("role") == "assistant"
            and isinstance(item.get("content"), list)
            and all(
                isinstance(part, dict) and part.get("type") == "output_text" and part.get("text") == ""
                for part in item["content"]
            )
        )
    ]
    return body if len(filtered) == len(source) else {**body, "input": filtered}

def _reasoning_text_from_parts(parts) -> str:
    if not isinstance(parts, list):
        return ""
    for part in parts:
        if (
            isinstance(part, dict)
            and part.get("type") in {"reasoning_text", "summary_text"}
            and isinstance(part.get("text"), str)
            and part["text"].strip()
        ):
            return part["text"]
    return ""

def _summary_reasoning_item(item: dict) -> dict | None:
    summary = item.get("summary")
    content = item.get("content")
    if _reasoning_text_from_parts(summary) and not content:
        return item
    text = _reasoning_text_from_parts(summary) or _reasoning_text_from_parts(content)
    if not text:
        return None
    return {"type": "reasoning", "summary": [{"type": "summary_text", "text": text}]}

def _normalize_web_search_call_item(item: dict) -> dict:
    action = item.get("action")
    if not isinstance(action, dict) or action.get("queries"):
        return item
    query = str(action.get("query", "") or "").strip()
    if not query:
        return item
    return {
        **item,
        "action": {"type": action.get("type", "search"), "queries": [{"query": query}]},
    }

def _normalize_web_search_call_items(items: list) -> list:
    return [
        _normalize_web_search_call_item(item)
        if isinstance(item, dict) and item.get("type") == "web_search_call"
        else item
        for item in items
    ]

def _deepseek_reasoning_item(item: dict) -> dict | None:
    text = _reasoning_text_from_parts(item.get("content")) or _reasoning_text_from_parts(item.get("summary"))
    if not text:
        return None
    normalized = dict(item)
    if not isinstance(normalized.get("summary"), list):
        normalized["summary"] = [{"type": "summary_text", "text": text}]
    if normalized.get("content") and not normalized.get("encrypted_content"):
        return {"type": "reasoning", "summary": [{"type": "summary_text", "text": text}]}
    return normalized

def _is_deepseek_responses_model(model: str) -> bool:
    return "deepseek" in model.lower()

def _deepseek_extra_call_reasoning(items: list) -> dict:
    clones = {}
    turn_reasoning = None
    calls_in_turn = 0
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            turn_reasoning = None
            calls_in_turn = 0
            continue
        item_type = item.get("type")
        if item_type == "reasoning":
            turn_reasoning = item
            calls_in_turn = 0
        elif item_type == "message" and item.get("role") == "assistant":
            continue
        elif item_type in _CALL_OUTPUT_TYPES:
            if turn_reasoning and calls_in_turn > 0:
                clones[index] = turn_reasoning
            calls_in_turn += 1
        else:
            turn_reasoning = None
            calls_in_turn = 0
    return clones

def _normalize_deepseek_tool_output_order(items: list) -> list:
    clones = _deepseek_extra_call_reasoning(items)
    consumed = set()
    result = []
    for index, item in enumerate(items):
        if index in consumed:
            continue
        reasoning = clones.get(index)
        if reasoning:
            result.append(dict(reasoning))
        result.append(item)
        if not isinstance(item, dict):
            continue
        call_id = item.get("call_id") or item.get("id")
        output_type = _CALL_OUTPUT_TYPES.get(item.get("type"))
        if not call_id or not output_type:
            continue
        for next_index in range(index + 1, len(items)):
            if next_index in consumed:
                continue
            candidate = items[next_index]
            if (
                isinstance(candidate, dict)
                and candidate.get("type") == output_type
                and (candidate.get("call_id") or candidate.get("id")) == call_id
            ):
                result.append(candidate)
                consumed.add(next_index)
                break
    return result

def _normalize_deepseek_responses_input(body: dict) -> dict:
    source = body.get("input")
    if not isinstance(source, list):
        return body
    items = []
    for item in source:
        if isinstance(item, dict) and item.get("type") == "reasoning":
            reasoning = _deepseek_reasoning_item(item)
            if reasoning is not None:
                items.append(reasoning)
        else:
            items.append(item)
    items = _normalize_web_search_call_items(items)
    ordered = _normalize_deepseek_tool_output_order(items)
    stripped = [_without_server_item_id(item) for item in ordered]
    return {**body, "input": stripped}

def _without_server_item_id(item):
    if (
        isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item["id"].startswith(("rs_", "fc_", "fco_", "resp_", "msg_", "ctc_", "ctco_", "ws_"))
    ):
        return {key: value for key, value in item.items() if key != "id"}
    return item

def _normalize_summary_responses_input(body: dict) -> dict:
    source = body.get("input")
    if not isinstance(source, list):
        return body
    items = []
    changed = False
    for item in source:
        normalized_item = _without_server_item_id(item)
        if isinstance(item, dict) and item.get("type") == "reasoning":
            reasoning = _summary_reasoning_item(normalized_item)
            if reasoning is None:
                changed = True
                continue
            changed = changed or reasoning is not item or normalized_item is not item
            items.append(reasoning)
        else:
            changed = changed or normalized_item is not item
            items.append(normalized_item)
    items = _normalize_web_search_call_items(items)
    if not changed and all(new is old for new, old in zip(items, source, strict=True)):
        return body
    return {**body, "input": items}

def _prepare_responses_upstream_body(body: dict, model: str) -> dict:
    prepared = _sanitize_responses_input(body)
    if _is_deepseek_responses_model(model):
        return _normalize_deepseek_responses_input(prepared)
    return _normalize_summary_responses_input(prepared)


class RelayServer:
    """Small local OpenAI-compatible router backed by enabled projects."""

    _SENSITIVE_LOG_KEYS = {
        "api_key",
        "authorization",
        "cookie",
        "password",
        "proxy_authorization",
        "secret",
        "set_cookie",
        "x_api_key",
    }
    _LOG_OPTION_KEYS = (
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
        "parallel_tool_calls",
        "reasoning",
        "reasoning_effort",
        "service_tier",
        "store",
        "temperature",
        "tool_choice",
        "top_p",
    )
    _TRACE_HEADER_NAMES = {"content-type", "cf-ray", "request-id", "x-request-id", "x-oneapi-request-id"}

    def __init__(self, app, host: str, port: int, auth_key: str = "", error_logging_enabled: bool = True):
        self.app = app
        self.host = host
        self.port = port
        self.auth_key = auth_key.strip()
        self.error_logging_enabled = bool(error_logging_enabled)
        self.httpd = None
        self.thread = None
        self._lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._rr = {}
        self._model_routes_cache = None
        self._responses_function_only = set()

    def start(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                owner.app._post(owner.app._log, "中转 " + (fmt % args))

            def _json(self, status: int, payload: dict):
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(raw)

            def _error(self, status: int, message: str, error_type: str = "invalid_request_error"):
                if self.command == "POST" and status != 200:
                    owner.log_error(
                        "http_response",
                        status=status,
                        error_type=error_type,
                        message=message,
                        **getattr(self, "_relay_error_context", {}),
                    )
                if _request_mode(self.path.split("?", 1)[0]) == "anthropic":
                    return self._json(status, {"type": "error", "error": {"type": error_type, "message": message}})
                return self._json(status, {"error": {"message": message, "type": error_type}})

            def _authorized(self):
                if not owner.auth_key:
                    return True
                authorization = self.headers.get("Authorization", "")
                bearer = authorization[7:] if authorization.startswith("Bearer ") else ""
                return hmac.compare_digest(bearer, owner.auth_key) or hmac.compare_digest(
                    self.headers.get("x-api-key", ""), owner.auth_key
                )

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                if not self._authorized():
                    return self._error(401, "本地中转密钥无效")
                if self.path.rstrip("/") not in {"/v1/models", "/models"}:
                    return self._error(404, "Not Found")
                models = owner.model_routes()
                self._json(
                    200,
                    {
                        "object": "list",
                        "data": [{"id": model, "object": "model", "owned_by": "relay"} for model in sorted(models)],
                    },
                )

            def do_POST(self):
                request_id = uuid.uuid4().hex[:12]
                incoming_path = self.path.split("?", 1)[0]
                self._relay_error_context = {
                    "request_id": request_id,
                    "path": self.path,
                    "content_length": self.headers.get("Content-Length", ""),
                }
                if not self._authorized():
                    return self._error(401, "本地中转密钥无效")
                length = self.headers.get("Content-Length")
                try:
                    raw_body = self.rfile.read(int(length or 0))
                    body = json.loads(raw_body)
                except (ValueError, TypeError, json.JSONDecodeError):
                    return self._error(400, "请求体必须是 JSON")
                if owner.error_logging_enabled:
                    self._relay_error_context["request"] = owner._request_summary(body, raw_body)
                if not isinstance(body, dict) or not body.get("model"):
                    return self._error(400, "请求体缺少 model")
                model = str(body["model"])
                routes = owner.model_routes().get(model, [])
                if not routes:
                    self._relay_error_context.update(
                        model=model,
                        available_model_count=len(owner.model_routes()),
                    )
                    return self._error(404, f"未启用模型：{model}", "model_not_found")
                incoming_mode = _request_mode(incoming_path)
                requested_stream = bool(body.get("stream"))
                custom_tool_names = _custom_tool_names(body) if incoming_mode == "responses" else set()
                attempts = []
                self._relay_error_context.update(
                    model=model,
                    incoming_mode=incoming_mode,
                    requested_stream=requested_stream,
                    route_count=len(routes),
                    attempts=attempts,
                )
                last_error = ""
                for project in owner.ordered_routes(model, routes):
                    attempt = {
                        "project_id": project.get("id"),
                        "project": project.get("name") or project.get("id") or "unknown",
                        "base_url": project.get("base_url"),
                        "proxy_enabled": bool(project.get("proxy_url")),
                        "skip_ssl_verify": bool(project.get("skip_ssl_verify", False)),
                    }
                    attempts.append(attempt)
                    try:
                        model_item = next(
                            (item for item in project.get("models", []) if str(item.get("id", "")).strip() == model),
                            None,
                        )
                        client = client_from_project(
                            project,
                            api_key_id=model_item.get("api_key_id") if isinstance(model_item, dict) else None,
                        )
                        mode_converted = incoming_mode != client.api_mode
                        with owner._lock:
                            custom_compatible = (
                                incoming_mode == client.api_mode == "responses"
                                and bool(custom_tool_names)
                                and project.get("id") in owner._responses_function_only
                            )
                        converted = mode_converted or custom_compatible
                        if custom_compatible:
                            upstream_body = _responses_custom_to_function(body)
                        elif mode_converted:
                            upstream_body = _convert_request(body, incoming_mode, client.api_mode)
                        else:
                            upstream_body = body
                        if client.api_mode == "responses":
                            upstream_body = _prepare_responses_upstream_body(upstream_body, model)
                        if converted:
                            upstream_body["stream"] = True
                        if upstream_body.get("stream"):
                            _include_stream_usage(upstream_body, client.api_mode)
                        path = owner.upstream_path(client.api_mode)
                        if not mode_converted and "?" in self.path:
                            path += "?" + self.path.split("?", 1)[1]
                        upstream_headers = owner.upstream_headers(client, self.headers, passthrough=not mode_converted)
                        attempt.update(
                            upstream_mode=client.api_mode,
                            upstream_url=f"{client.base_url}{path}",
                            converted=converted,
                        )
                        response = requests.post(
                            f"{client.base_url}{path}",
                            headers=upstream_headers,
                            json=upstream_body,
                            timeout=(15, 300),
                            proxies=client.proxies,
                            verify=client.verify_ssl,
                            stream=True,
                        )
                        attempt.update(
                            upstream_status=response.status_code,
                            upstream_trace=owner._trace_headers(response.headers),
                        )
                        if response.ok:
                            return owner.write_upstream(
                                self,
                                response,
                                client.api_mode,
                                incoming_mode,
                                model,
                                project,
                                converted,
                                requested_stream,
                                custom_tool_names,
                                request_id,
                            )
                        error_text = error_message_from_response(response)
                        attempt["upstream_error"] = error_text
                        response.close()
                        if (
                            incoming_mode == client.api_mode == "responses"
                            and custom_tool_names
                            and _is_function_only_tools_error(error_text)
                        ):
                            with owner._lock:
                                owner._responses_function_only.add(project.get("id"))
                            compatible_body = _prepare_responses_upstream_body(_responses_custom_to_function(body), model)
                            compatible_body["stream"] = True
                            compatibility_retry = {
                                "converted": True,
                            }
                            attempt["compatibility_retry"] = compatibility_retry
                            response = requests.post(
                                f"{client.base_url}{path}",
                                headers=upstream_headers,
                                json=compatible_body,
                                timeout=(15, 300),
                                proxies=client.proxies,
                                verify=client.verify_ssl,
                                stream=True,
                            )
                            compatibility_retry.update(
                                upstream_status=response.status_code,
                                upstream_trace=owner._trace_headers(response.headers),
                            )
                            if response.ok:
                                return owner.write_upstream(
                                    self,
                                    response,
                                    client.api_mode,
                                    incoming_mode,
                                    model,
                                    project,
                                    True,
                                    requested_stream,
                                    custom_tool_names,
                                    request_id,
                                )
                            error_text = error_message_from_response(response)
                            compatibility_retry["upstream_error"] = error_text
                            response.close()
                        last_error = f"HTTP {response.status_code}: {error_text}"
                    except Exception as exc:
                        last_error = str(exc)
                        attempt.update(
                            exception_type=type(exc).__name__,
                            exception=str(exc),
                        )
                self._error(502, last_error or "所有上游接口均不可用", "upstream_error")

        try:
            RELAY_ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
            with self._log_lock:
                self._prune_old_logs(self._current_log_path())
        except OSError:
            pass

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="ai-relay", daemon=True)
        self.thread.start()

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        self.httpd = None
        self.thread = None

    @staticmethod
    def _recoverable_stream_disconnect(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "response ended prematurely",
                "incomplete read",
                "stream closed",
                "connection broken",
                "connection reset",
                "remote end closed connection",
            )
        )

    @classmethod
    def _sanitize_log_value(cls, value, key: str = "", depth: int = 0):
        normalized_key = key.lower().replace("-", "_")
        key_parts = set(normalized_key.split("_"))
        if (
            normalized_key in cls._SENSITIVE_LOG_KEYS
            or normalized_key.startswith("api_key")
            or normalized_key.endswith("_key")
            or key_parts.intersection({"authorization", "cookie", "password", "secret", "token"})
        ):
            return "[REDACTED]"
        if depth >= 6:
            return "[MAX_DEPTH]"
        if isinstance(value, dict):
            items = list(value.items())
            sanitized = {}
            for item_key, item_value in items[:30]:
                raw_key = str(item_key)
                sanitized[raw_key[:100]] = cls._sanitize_log_value(item_value, raw_key, depth + 1)
            if len(items) > 30:
                sanitized["_truncated_fields"] = len(items) - 30
            return sanitized
        if isinstance(value, (list, tuple, set)):
            items = list(value)
            sanitized = [cls._sanitize_log_value(item, depth=depth + 1) for item in items[:20]]
            if len(items) > 20:
                sanitized.append({"_truncated_items": len(items) - 20})
            return sanitized
        if isinstance(value, str) and len(value) > 1000:
            return f"{value[:1000]}...[truncated {len(value) - 1000} chars]"
        return value

    @classmethod
    def _request_summary(cls, body, raw_body: bytes) -> dict:
        summary = {
            "body_bytes": len(raw_body),
            "body_sha256": hashlib.sha256(raw_body).hexdigest(),
        }
        if not isinstance(body, dict):
            summary["json_type"] = type(body).__name__
            return summary
        for key in ("model", "stream", *cls._LOG_OPTION_KEYS):
            if key in body:
                summary[key] = cls._sanitize_log_value(body[key], key)
        if isinstance(body.get("instructions"), str):
            summary["instructions_chars"] = len(body["instructions"])
        for key in ("input", "messages"):
            if key in body:
                summary[key] = cls._sequence_summary(body[key])
        if "tools" in body:
            summary["tools"] = cls._tools_summary(body["tools"])
        return summary

    @staticmethod
    def _sequence_summary(value) -> dict:
        if isinstance(value, str):
            return {"kind": "text", "chars": len(value)}
        if not isinstance(value, list):
            return {"kind": type(value).__name__}
        types = {}
        roles = {}
        content_types = {}
        for item in value:
            if not isinstance(item, dict):
                item_type = type(item).__name__
            else:
                item_type = str(item.get("type") or "message")[:80]
                role = item.get("role")
                if role:
                    role = str(role)[:80]
                    roles[role] = roles.get(role, 0) + 1
                content = item.get("content")
                if isinstance(content, list):
                    for part in content:
                        part_type = (
                            str(part.get("type") or "unknown")[:80]
                            if isinstance(part, dict)
                            else type(part).__name__
                        )
                        content_types[part_type] = content_types.get(part_type, 0) + 1
            types[item_type] = types.get(item_type, 0) + 1
        result = {"kind": "items", "count": len(value), "types": types}
        if roles:
            result["roles"] = roles
        if content_types:
            result["content_types"] = content_types
        return result

    @staticmethod
    def _tools_summary(value) -> dict:
        if not isinstance(value, list):
            return {"kind": type(value).__name__}
        types = {}
        names = []
        for tool in value:
            if not isinstance(tool, dict):
                tool_type = type(tool).__name__
                name = ""
            else:
                tool_type = str(tool.get("type") or "unknown")[:80]
                function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
                name = str(tool.get("name") or function.get("name") or "")[:100]
            types[tool_type] = types.get(tool_type, 0) + 1
            if name and len(names) < 20:
                names.append(name)
        return {"count": len(value), "types": types, "names": names}

    @classmethod
    def _trace_headers(cls, headers) -> dict:
        return {
            str(name).lower(): cls._sanitize_log_value(value, str(name))
            for name, value in headers.items()
            if str(name).lower() in cls._TRACE_HEADER_NAMES or str(name).lower().endswith("request-id")
        }

    @staticmethod
    def _current_log_path() -> Path:
        day = datetime.now().astimezone().strftime("%Y-%m-%d")
        return RELAY_ERROR_LOG.with_name(f"relay-errors-{day}.jsonl")

    @classmethod
    def _prune_old_logs(cls, keep: Path) -> None:
        for path in (*keep.parent.glob("relay-errors-*.jsonl"), RELAY_ERROR_LOG):
            if path == keep:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue

    def log_error(self, stage: str, **details):
        if not self.error_logging_enabled:
            return
        entry = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "stage": stage,
            **self._sanitize_log_value(details),
        }
        try:
            log_dir = RELAY_ERROR_LOG.parent
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = self._current_log_path()
            with self._log_lock:
                self._prune_old_logs(log_path)
                with log_path.open("a", encoding="utf-8") as log_file:
                    json.dump(entry, log_file, ensure_ascii=False, default=str)
                    log_file.write("\n")
        except OSError as log_exc:
            self.app._post(self.app._log, f"中转异常日志写入失败：{log_exc}")
            return
        self.app._post(self.app._log, f"中转异常[{stage}]已记录：{log_path}")

    def log_exception(self, stage: str, exc: Exception, **context):
        self.log_error(
            stage,
            **context,
            exception_type=type(exc).__name__,
            exception=str(exc),
        )

    def model_routes(self) -> dict[str, list[dict]]:
        with self._lock:
            if self._model_routes_cache is None:
                relay = self.app.store.get("relay", {})
                enabled = set(relay.get("project_ids", []))
                result = {}
                for project in self.app.store.get("projects", []):
                    if project.get("id") not in enabled:
                        continue
                    for item in project.get("models", []):
                        model = str(item.get("id", "")).strip() if isinstance(item, dict) else str(item).strip()
                        if model:
                            result.setdefault(model, []).append(project)
                self._model_routes_cache = result
            return self._model_routes_cache

    def invalidate_routes(self):
        with self._lock:
            self._model_routes_cache = None

    def ordered_routes(self, model: str, routes: list[dict]) -> list[dict]:
        with self._lock:
            offset = self._rr.get(model, 0) % len(routes)
            self._rr[model] = offset + 1
        return routes[offset:] + routes[:offset]

    @staticmethod
    def upstream_path(api_mode: str) -> str:
        if api_mode == "anthropic":
            return "/messages"
        if api_mode == "responses":
            return "/responses"
        return "/chat/completions"

    @staticmethod
    def upstream_headers(client: OpenAIClient, incoming_headers, passthrough: bool) -> dict[str, str]:
        headers = dict(client.headers)
        if not passthrough:
            return headers
        blocked = {
            "authorization",
            "x-api-key",
            "host",
            "content-length",
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailer",
            "transfer-encoding",
            "upgrade",
            "cookie",
        }
        configured = {
            name.lower()
            for name, value in client.custom_headers.items()
            if name.lower() != "user-agent" or str(value).strip()
        }
        for name, value in incoming_headers.items():
            if name.lower() not in blocked and name.lower() not in configured:
                headers[name] = value
        return headers

    @staticmethod
    def _response_headers(handler, response, extra_blocked=None, extra_headers=None):
        blocked = {
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailer",
            "transfer-encoding",
            "upgrade",
            "server",
            "date",
        }
        blocked.update(extra_blocked or ())
        handler.send_response(response.status_code)
        has_cors = False
        for name, value in response.headers.items():
            if name.lower() in blocked:
                continue
            handler.send_header(name, value)
            has_cors = has_cors or name.lower() == "access-control-allow-origin"
        if not has_cors:
            handler.send_header("Access-Control-Allow-Origin", "*")
        for name, value in (extra_headers or {}).items():
            handler.send_header(name, value)
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.close_connection = True

    @staticmethod
    def passthrough_json(app, handler, response, upstream_mode, project, model):
        try:
            body = b"".join(response.iter_content(chunk_size=65536))
        finally:
            response.close()
        input_tokens = output_tokens = cached_tokens = 0
        try:
            payload = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            input_tokens, output_tokens, cached_tokens = extract_usage_tokens(payload.get("usage"), upstream_mode)
        RelayServer._response_headers(
            handler,
            response,
            extra_blocked={"content-encoding", "content-length"},
            extra_headers={"Content-Length": str(len(body))},
        )
        try:
            handler.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            app.record_relay_usage(project, model, input_tokens, output_tokens, cached_tokens)

    @staticmethod
    def passthrough_stream(app, handler, response, upstream_mode, project, model):
        RelayServer._response_headers(handler, response, {"content-encoding", "transfer-encoding", "content-length"})
        collector = _SSEUsageCollector(upstream_mode)
        responses_tracker = _ResponsesStreamTracker(model) if upstream_mode == "responses" else None
        try:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    if responses_tracker:
                        responses_tracker.feed(chunk)
                    handler.wfile.write(chunk)
                    handler.wfile.flush()
                    collector.feed(chunk)
            if responses_tracker and (completion := responses_tracker.completion()):
                handler.wfile.write(completion)
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        except Exception as exc:
            if not responses_tracker or not RelayServer._recoverable_stream_disconnect(exc):
                raise
            if completion := responses_tracker.completion():
                handler.wfile.write(completion)
                handler.wfile.flush()
        finally:
            collector.finish()
            response.close()
        input_tokens, output_tokens, cached_tokens = collector.summary()
        app.record_relay_usage(project, model, input_tokens, output_tokens, cached_tokens)

    def write_upstream(
        self,
        handler,
        response,
        upstream_mode: str,
        incoming_mode: str,
        model: str,
        project: dict,
        converted: bool,
        requested_stream: bool,
        custom_tool_names: set[str] | None = None,
        request_id: str = "",
    ):
        if not converted:
            if requested_stream:
                RelayServer.passthrough_stream(self.app, handler, response, upstream_mode, project, model)
            else:
                RelayServer.passthrough_json(self.app, handler, response, upstream_mode, project, model)
            return

        if not requested_stream:
            try:
                result = _collect_stream_result(response, upstream_mode, model, custom_tool_names)
                raw = json.dumps(
                    _response_for_mode(result, incoming_mode, custom_tool_names), ensure_ascii=False
                ).encode("utf-8")
            finally:
                response.close()
            handler.send_response(response.status_code)
            handler.send_header("Content-Type", "application/json; charset=utf-8")
            handler.send_header("Content-Length", str(len(raw)))
            handler.send_header("Access-Control-Allow-Origin", "*")
            handler.end_headers()
            handler.wfile.write(raw)
            usage = result["usage"]
            self.app.record_relay_usage(
                project,
                model,
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
                usage.get("cached_tokens", 0),
            )
            return

        handler.send_response(response.status_code)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.close_connection = True

        def write_stream(chunk: bytes):
            if not chunk:
                return
            handler.wfile.write(chunk)
            handler.wfile.flush()

        renderer = _StreamRenderer(incoming_mode, model, custom_tool_names)
        try:
            for event in _canonical_stream_events(response, upstream_mode, model, custom_tool_names):
                for chunk in renderer.feed(event):
                    write_stream(chunk)
            for chunk in renderer.finish():
                write_stream(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        except Exception as exc:
            self.log_exception(
                "converted_stream",
                exc,
                incoming_mode=incoming_mode,
                upstream_mode=upstream_mode,
                project=project.get("name") or project.get("id") or "unknown",
                model=model,
                has_output=renderer.has_output,
                request_id=request_id,
            )
            try:
                if incoming_mode == "responses" and renderer.has_output and self._recoverable_stream_disconnect(exc):
                    for chunk in renderer.finish():
                        write_stream(chunk)
                else:
                    error = {"type": "error", "error": {"type": "api_error", "message": str(exc)}}
                    write_stream(f"event: error\ndata: {json.dumps(error, ensure_ascii=False)}\n\n".encode())
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        finally:
            response.close()
            self.app.record_relay_usage(
                project,
                model,
                renderer.input_tokens,
                renderer.output_tokens,
                renderer.cached_tokens,
            )

