import copy
import json
import unittest
from http.client import HTTPConnection
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai_probe.protocols import _convert_request
from ai_probe.relay import RelayServer, _inject_system_prompt
from ai_probe.ui.store_mixin import StoreMixin


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
            verify_ssl=True,
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
                        self.assertEqual(result.status, 502)
                    finally:
                        connection.close()
                    self.assertEqual(post.call_args.kwargs["json"]["instructions"], expected)
                    self.assertEqual(post.call_args.kwargs["json"]["input"], "question")
                self.assertEqual(post.call_count, 3)
            finally:
                server.stop()

    def test_legacy_defaults_and_saved_config(self):
        relay = StoreMixin._normalize_store({"projects": []})["relay"]
        self.assertEqual(relay["system_prompt"], "")
        self.assertTrue(relay["append_user_prompt"])
        relay = StoreMixin._normalize_store(
            {
                "projects": [],
                "relay": {
                    "system_prompt": "global\nline 2",
                    "append_user_prompt": False,
                },
            }
        )["relay"]
        self.assertEqual(relay["system_prompt"], "global\nline 2")
        self.assertFalse(relay["append_user_prompt"])
        server = RelayServer(
            SimpleNamespace(store={}), "127.0.0.1", 0, system_prompt=relay["system_prompt"], append_user_prompt=False
        )
        self.assertEqual(server.prompt_settings, ("global\nline 2", False))
