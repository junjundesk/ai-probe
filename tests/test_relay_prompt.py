import copy
import json
import tempfile
import unittest
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai_probe.protocols import _convert_request
from ai_probe.relay import RelayServer, _inject_system_prompt
from ai_probe.store_service import normalize_store

_LOG_DIR = tempfile.TemporaryDirectory()
_LOG_PATCHERS = (
    patch("ai_probe.relay.RELAY_ERROR_LOG", Path(_LOG_DIR.name) / "relay-errors.jsonl"),
    patch("ai_probe.relay.RELAY_REQUEST_LOG", Path(_LOG_DIR.name) / "relay-requests.jsonl"),
)


def setUpModule():
    for patcher in _LOG_PATCHERS:
        patcher.start()


def tearDownModule():
    for patcher in _LOG_PATCHERS:
        patcher.stop()
    _LOG_DIR.cleanup()


class RelayPromptTests(unittest.TestCase):
    def test_blank_is_exact_noop(self):
        body = {"instructions": "original", "messages": [{"role": "system", "content": "original"}]}
        for mode in ("chat", "responses", "anthropic"):
            for append in (True, False):
                self.assertIs(_inject_system_prompt(body, mode, " \n\t", append), body)

    def test_chat_preserves_multimodal_and_tools_without_mutation(self):
        messages = [
            {"role": "system", "content": "client"},
            {"role": "developer", "content": "developer"},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]},
            {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function"}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        ]
        body = {"messages": messages, "model": "test", "stream": True}
        before = copy.deepcopy(body)
        for append in (True, False):
            result = _inject_system_prompt(body, "chat", "global", append)
            self.assertEqual(result["messages"][0], {"role": "system", "content": "global"})
            self.assertEqual(result["messages"][1:], messages if append else messages[2:])
            self.assertEqual(body, before)

    def test_responses_instructions_and_input_roles(self):
        body = {
            "instructions": "client",
            "input": [
                {"role": "developer", "content": "dev"},
                {"role": "system", "content": "sys"},
                {"type": "function_call_output", "call_id": "c1", "output": "ok"},
                {"role": "user", "content": "question"},
            ],
        }
        result = _inject_system_prompt(body, "responses", "global", True)
        self.assertEqual(result["instructions"], "global\n\nclient")
        self.assertEqual(result["input"], body["input"])
        result = _inject_system_prompt(body, "responses", "global", False)
        self.assertEqual(result["instructions"], "global")
        self.assertEqual(result["input"], body["input"][2:])
        self.assertEqual(_inject_system_prompt({"input": "hello"}, "responses", "global", False)["input"], "hello")

    def test_anthropic_text_and_cached_blocks(self):
        for system in ("client", [{"type": "text", "text": "client", "cache_control": {"type": "ephemeral"}}]):
            body = {"system": system, "messages": [{"role": "user", "content": "hello"}]}
            before = copy.deepcopy(body)
            result = _inject_system_prompt(body, "anthropic", "global", True)
            expected = "global\n\nclient" if isinstance(system, str) else [{"type": "text", "text": "global"}, *system]
            self.assertEqual(result["system"], expected)
            self.assertEqual(result["messages"], body["messages"])
            self.assertEqual(_inject_system_prompt(body, "anthropic", "global", False)["system"], "global")
            self.assertEqual(body, before)

    def test_injection_survives_all_protocol_conversions(self):
        bodies = {
            "chat": {"messages": [{"role": "system", "content": "client"}, {"role": "user", "content": "question"}]},
            "responses": {"instructions": "client", "input": "question"},
            "anthropic": {"system": "client", "messages": [{"role": "user", "content": "question"}]},
        }
        for source, body in bodies.items():
            for target in bodies:
                for append in (True, False):
                    with self.subTest(source=source, target=target, append=append):
                        result = _convert_request(_inject_system_prompt(body, source, "global", append), source, target)
                        self.assertIn("global", str(result))
                        self.assertIn("question", str(result))
                        self.assertEqual("client" in str(result), append)

    def test_http_forwarding_uses_live_settings_once_per_request(self):
        project = {"id": "test", "name": "test", "models": [{"id": "model"}]}
        app = SimpleNamespace(
            store={"projects": [project], "relay": {"project_ids": ["test"]}},
            _post=lambda *args: None,
            _log=lambda *args: None,
        )
        server = RelayServer(
            app, "127.0.0.1", 0, error_logging_enabled=False, request_logging_enabled=False, system_prompt="global"
        )
        client = SimpleNamespace(
            api_mode="responses",
            headers={},
            custom_headers={},
            base_url="https://example.test/v1",
            proxies={},
        )
        response = Mock(ok=False, status_code=400, text='{"error":{"message":"test response"}}', headers={})
        response.json.return_value = {"error": {"message": "test response"}}
        payload = {"model": "model", "instructions": "client", "input": "question"}
        with (
            patch("ai_probe.relay.client_from_project", return_value=client),
            patch("ai_probe.relay.requests.post", return_value=response) as post,
        ):
            server.start()
            try:
                for settings, expected in [
                    (("global", True), "global\n\nclient"),
                    (("changed", False), "changed"),
                    (("", False), "client"),
                ]:
                    server.prompt_settings = settings
                    connection = HTTPConnection("127.0.0.1", server.port, timeout=3)
                    try:
                        connection.request(
                            "POST", "/v1/responses", json.dumps(payload), {"Content-Type": "application/json"}
                        )
                        result = connection.getresponse()
                        result.read()
                        # 上游 400 原样透传，客户端才知道这是请求本身的问题、不必重试。
                        self.assertEqual(result.status, 400)
                    finally:
                        connection.close()
                    self.assertEqual(post.call_args.kwargs["json"]["instructions"], expected)
                    self.assertEqual(post.call_args.kwargs["json"]["input"], "question")
                self.assertEqual(post.call_count, 3)
            finally:
                server.stop()

    def test_http_forwarding_keeps_caller_user_agent_when_unset(self):
        """端到端：本机与项目都没配 UA 时，转换后的上游请求必须沿用调用方 UA。"""
        project = {"id": "test", "name": "test", "models": [{"id": "model"}]}
        app = SimpleNamespace(
            store={"projects": [project], "relay": {"project_ids": ["test"]}},
            _post=lambda *args: None,
            _log=lambda *args: None,
        )
        server = RelayServer(app, "127.0.0.1", 0, error_logging_enabled=False, request_logging_enabled=False)
        # 项目是 anthropic 上游，调用方发 chat 请求 => 走协议转换路径。
        client = SimpleNamespace(
            api_mode="anthropic",
            headers={"x-api-key": "sk-upstream"},
            custom_headers={"x-api-key": "sk-upstream"},
            base_url="https://example.test/v1",
            proxies={},
        )
        response = Mock(ok=False, status_code=400, text='{"error":{"message":"test response"}}', headers={})
        response.json.return_value = {"error": {"message": "test response"}}
        payload = {"model": "model", "messages": [{"role": "user", "content": "hi"}]}
        caller_headers = {"Content-Type": "application/json", "User-Agent": "claude-cli/2.1.0 (external, cli)"}
        with (
            patch("ai_probe.relay.client_from_project", return_value=client),
            patch("ai_probe.relay.requests.post", return_value=response) as post,
        ):
            server.start()
            try:
                connection = HTTPConnection("127.0.0.1", server.port, timeout=3)
                try:
                    connection.request("POST", "/v1/chat/completions", json.dumps(payload), caller_headers)
                    result = connection.getresponse()
                    result.read()
                    self.assertEqual(result.status, 400)
                finally:
                    connection.close()
                sent = post.call_args.kwargs["headers"]
                self.assertEqual(sent["User-Agent"], caller_headers["User-Agent"])
                self.assertEqual(sum(name.lower() == "user-agent" for name in sent), 1)
                # 协议转换仍然只转发 UA，不把入站 Content-Type 之类的头带过去覆盖上游。
                self.assertEqual(sent["x-api-key"], "sk-upstream")
            finally:
                server.stop()

    def test_legacy_defaults_and_saved_config(self):
        relay = normalize_store({"projects": []})["relay"]
        self.assertEqual(relay["system_prompt"], "")
        self.assertTrue(relay["append_user_prompt"])
        self.assertEqual(relay["user_agent"], "")
        relay = normalize_store(
            {
                "projects": [],
                "relay": {
                    "system_prompt": "global\nline 2",
                    "append_user_prompt": False,
                    "user_agent": 123,
                },
            }
        )["relay"]
        self.assertEqual(relay["system_prompt"], "global\nline 2")
        self.assertFalse(relay["append_user_prompt"])
        self.assertEqual(relay["user_agent"], "123")
        server = RelayServer(
            SimpleNamespace(store={}),
            "127.0.0.1",
            0,
            system_prompt=relay["system_prompt"],
            append_user_prompt=False,
            user_agent=relay["user_agent"],
        )
        self.assertEqual(server.prompt_settings, ("global\nline 2", False))
        self.assertEqual(server.user_agent, "123")

    def test_relay_user_agent_overrides_incoming_and_project_headers(self):
        client = SimpleNamespace(
            headers={"User-Agent": "project-agent", "X-Project": "yes"},
            custom_headers={"User-Agent": "project-agent", "X-Project": "yes"},
        )
        headers = RelayServer.upstream_headers(
            client,
            {"User-Agent": "caller-agent", "X-Incoming": "yes"},
            passthrough=True,
            user_agent="relay-agent",
        )
        self.assertEqual(headers["User-Agent"], "relay-agent")
        self.assertEqual(headers["X-Incoming"], "yes")
        self.assertEqual(sum(name.lower() == "user-agent" for name in headers), 1)
        converted_headers = RelayServer.upstream_headers(
            client,
            {"User-Agent": "caller-agent", "X-Incoming": "yes"},
            passthrough=False,
            user_agent="relay-agent",
        )
        self.assertEqual(converted_headers["User-Agent"], "relay-agent")
        self.assertNotIn("X-Incoming", converted_headers)

    def test_blank_relay_user_agent_preserves_existing_behavior(self):
        client = SimpleNamespace(
            headers={"User-Agent": "project-agent"},
            custom_headers={"User-Agent": "project-agent"},
        )
        self.assertEqual(
            RelayServer.upstream_headers(client, {"User-Agent": "caller-agent"}, True)["User-Agent"],
            "project-agent",
        )
        self.assertEqual(
            RelayServer.upstream_headers(
                SimpleNamespace(headers={}, custom_headers={}), {"User-Agent": "caller-agent"}, True
            )["User-Agent"],
            "caller-agent",
        )

    def test_converted_requests_forward_incoming_user_agent(self):
        """协议转换不复制入站请求头，但 UA 必须原样转发，不能换成 requests 的默认值。"""
        blank_client = SimpleNamespace(headers={}, custom_headers={})
        for passthrough in (True, False):
            with self.subTest(passthrough=passthrough):
                headers = RelayServer.upstream_headers(
                    blank_client, {"User-Agent": "claude-cli/2.1.0", "X-Incoming": "yes"}, passthrough
                )
                self.assertEqual(headers["User-Agent"], "claude-cli/2.1.0")
                self.assertEqual(sum(name.lower() == "user-agent" for name in headers), 1)
                self.assertEqual("X-Incoming" in headers, passthrough)

    def test_converted_requests_keep_project_and_relay_user_agent_precedence(self):
        project_client = SimpleNamespace(headers={}, custom_headers={"User-Agent": "project-agent"})
        project_client.headers = {"User-Agent": "project-agent"}
        self.assertEqual(
            RelayServer.upstream_headers(project_client, {"User-Agent": "caller-agent"}, False)["User-Agent"],
            "project-agent",
        )
        self.assertEqual(
            RelayServer.upstream_headers(
                SimpleNamespace(headers={}, custom_headers={}), {"User-Agent": "caller-agent"}, False, "relay-agent"
            )["User-Agent"],
            "relay-agent",
        )

    def test_blank_project_user_agent_placeholder_does_not_block_incoming(self):
        # UI 的「添加 User-Agent」会写入空值占位，它不构成配置，应继续透传入站 UA。
        client = SimpleNamespace(headers={}, custom_headers={"User-Agent": ""})
        self.assertEqual(
            RelayServer.upstream_headers(client, {"User-Agent": "caller-agent"}, False)["User-Agent"],
            "caller-agent",
        )

    def test_missing_incoming_user_agent_leaves_header_absent(self):
        headers = RelayServer.upstream_headers(SimpleNamespace(headers={}, custom_headers={}), {}, False)
        self.assertFalse(RelayServer.has_user_agent(headers))
        self.assertEqual(RelayServer.incoming_user_agent({"User-Agent": "  caller  "}), "caller")
        self.assertEqual(RelayServer.incoming_user_agent({"user-agent": "lower"}), "lower")
        self.assertEqual(RelayServer.incoming_user_agent({}), "")
