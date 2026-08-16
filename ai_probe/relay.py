"""本地兼容转发服务器。"""

from __future__ import annotations

import hmac
import json
import threading
import traceback
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
                    "method": "POST",
                    "path": self.path,
                    "client": self.client_address[0],
                    "request_headers": dict(self.headers.items()),
                }
                if not self._authorized():
                    return self._error(401, "本地中转密钥无效")
                length = self.headers.get("Content-Length")
                try:
                    body = json.loads(self.rfile.read(int(length or 0)))
                except (ValueError, TypeError, json.JSONDecodeError):
                    return self._error(400, "请求体必须是 JSON")
                self._relay_error_context["request_body"] = body
                if not isinstance(body, dict) or not body.get("model"):
                    return self._error(400, "请求体缺少 model")
                model = str(body["model"])
                routes = owner.model_routes().get(model, [])
                if not routes:
                    self._relay_error_context.update(
                        model=model,
                        available_models=sorted(owner.model_routes()),
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
                        "proxy_url": project.get("proxy_url"),
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
                            upstream_body = _sanitize_responses_input(upstream_body)
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
                            upstream_headers=upstream_headers,
                            upstream_body=upstream_body,
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
                            upstream_response_headers=dict(response.headers),
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
                            compatible_body = _sanitize_responses_input(_responses_custom_to_function(body))
                            compatible_body["stream"] = True
                            compatibility_retry = {
                                "upstream_body": compatible_body,
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
                                upstream_response_headers=dict(response.headers),
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
                            traceback="".join(traceback.format_exception(exc)),
                        )
                        owner.log_exception(
                            "upstream_request",
                            exc,
                            request_id=request_id,
                            path=incoming_path,
                            incoming_mode=incoming_mode,
                            upstream_mode=getattr(locals().get("client"), "api_mode", "unknown"),
                            project=project.get("name") or project.get("id") or "unknown",
                            model=model,
                            requested_stream=requested_stream,
                        )
                self._relay_error_context["last_error"] = last_error
                self._error(502, last_error or "所有上游接口均不可用", "upstream_error")

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
    def _sanitize_log_value(cls, value, key: str = ""):
        normalized_key = key.lower().replace("-", "_")
        key_parts = set(normalized_key.split("_"))
        if (
            normalized_key in cls._SENSITIVE_LOG_KEYS
            or normalized_key.startswith("api_key")
            or normalized_key.endswith("_key")
            or key_parts.intersection({"authorization", "cookie", "password", "secret", "token"})
        ):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {
                str(item_key): cls._sanitize_log_value(item_value, str(item_key))
                for item_key, item_value in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [cls._sanitize_log_value(item) for item in value]
        if isinstance(value, str) and len(value) > 4000:
            return f"{value[:4000]}...[truncated {len(value) - 4000} chars]"
        return value

    def log_error(self, stage: str, **details):
        if not self.error_logging_enabled:
            return
        entry = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "stage": stage,
            **self._sanitize_log_value(details),
        }
        try:
            RELAY_ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
            with self._log_lock, RELAY_ERROR_LOG.open("a", encoding="utf-8") as log_file:
                json.dump(entry, log_file, ensure_ascii=False, default=str)
                log_file.write("\n")
        except OSError as log_exc:
            self.app._post(self.app._log, f"中转异常日志写入失败：{log_exc}")
            return
        self.app._post(self.app._log, f"中转异常[{stage}]已记录：{RELAY_ERROR_LOG}")

    def log_exception(self, stage: str, exc: Exception, **context):
        self.log_error(
            stage,
            **context,
            exception_type=type(exc).__name__,
            exception=str(exc),
            traceback="".join(traceback.format_exception(exc)),
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
        try:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    handler.wfile.write(chunk)
                    handler.wfile.flush()
                    collector.feed(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
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
