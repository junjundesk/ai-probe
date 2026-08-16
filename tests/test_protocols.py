import json
import unittest
from http.client import HTTPConnection
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ai_probe.protocols import (
    _anthropic_to_chat,
    _canonical_stream_events,
    _chat_to_anthropic,
    _convert_request,
    _include_stream_usage,
    _request_mode,
    _responses_custom_to_function,
    _responses_to_chat,
    _StreamRenderer,
)
from ai_probe.relay import RelayServer


class RequestModeTests(unittest.TestCase):
    def test_request_mode(self):
        self.assertEqual(_request_mode("/v1/chat/completions"), "chat")
        self.assertEqual(_request_mode("/v1/responses"), "responses")
        self.assertEqual(_request_mode("/v1/messages"), "anthropic")


class ConversionTests(unittest.TestCase):
    def test_chat_to_anthropic(self):
        converted = _chat_to_anthropic({"model": "claude", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(converted["messages"][0]["content"], [{"type": "text", "text": "hi"}])

    def test_chat_to_anthropic_thinking_maps_budget_and_drops_sampling(self):
        converted = _chat_to_anthropic(
            {
                "model": "claude-test",
                "messages": [{"role": "user", "content": "hi"}],
                "reasoning_effort": "high",
                "max_completion_tokens": 4096,
                "temperature": 0.7,
                "top_p": 0.9,
            }
        )
        self.assertEqual(converted["thinking"], {"type": "enabled", "budget_tokens": 4096})
        self.assertEqual(converted["max_tokens"], 5120)
        self.assertNotIn("temperature", converted)
        self.assertNotIn("top_p", converted)

    def test_chat_to_anthropic_sanitizes_tool_history(self):
        converted = _chat_to_anthropic(
            {
                "model": "claude-test",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "lookup", "arguments": '{"key":"a"}'},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_1", "content": "found"},
                    {"role": "assistant", "content": "done"},
                ],
            }
        )
        self.assertEqual(
            [message["role"] for message in converted["messages"]], ["user", "assistant", "user", "assistant", "user"]
        )
        self.assertEqual(converted["messages"][1]["content"][0]["type"], "tool_use")
        self.assertEqual(converted["messages"][1]["content"][0]["input"], {"key": "a"})
        self.assertEqual(converted["messages"][2]["content"][0]["type"], "tool_result")

    def test_chat_to_anthropic_repairs_missing_tool_result(self):
        converted = _chat_to_anthropic(
            {
                "model": "claude-test",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "lookup", "arguments": "{}"},
                            }
                        ],
                    },
                    {"role": "user", "content": "keep going"},
                ],
            }
        )
        result = converted["messages"][2]
        self.assertEqual(result["role"], "user")
        self.assertEqual(result["content"][0]["type"], "tool_result")
        self.assertEqual(result["content"][0]["tool_use_id"], "call_1")
        self.assertEqual(result["content"][0]["is_error"], True)
        self.assertEqual(result["content"][1]["text"], "keep going")

    def test_chat_to_anthropic_system_only_adds_user_message(self):
        converted = _chat_to_anthropic(
            {
                "model": "claude-test",
                "messages": [{"role": "system", "content": "Be brief."}],
            }
        )
        self.assertEqual(converted["system"], "Be brief.")
        self.assertEqual(converted["messages"][0]["role"], "user")

    def test_parallel_tool_calls_mapping(self):
        anthropic = _chat_to_anthropic(
            {
                "model": "claude-test",
                "messages": [{"role": "user", "content": "hi"}],
                "parallel_tool_calls": False,
            }
        )
        self.assertEqual(anthropic["disable_parallel_tool_use"], True)
        chat = _anthropic_to_chat(
            {
                "model": "gpt-test",
                "messages": [{"role": "user", "content": "hi"}],
                "disable_parallel_tool_use": True,
            }
        )
        self.assertEqual(chat["parallel_tool_calls"], False)

    def test_responses_to_chat_with_reasoning(self):
        converted = _responses_to_chat(
            {
                "model": "gpt-test",
                "input": [
                    {"type": "reasoning", "summary": [{"type": "summary_text", "text": "think"}]},
                    {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "answer"}]},
                ],
            }
        )
        self.assertEqual(converted["messages"][-1]["reasoning_content"], "think")

    def test_responses_tool_choice_dictionary_to_chat(self):
        converted = _responses_to_chat(
            {
                "model": "gpt-test",
                "input": "hi",
                "tool_choice": {"type": "none"},
            }
        )
        self.assertEqual(converted["tool_choice"], "none")
        converted = _responses_to_chat(
            {
                "model": "gpt-test",
                "input": "hi",
                "tool_choice": {"type": "function", "name": "lookup"},
            }
        )
        self.assertEqual(converted["tool_choice"], {"type": "function", "function": {"name": "lookup"}})

    def test_responses_custom_to_function_drops_builtin_tools(self):
        converted = _responses_custom_to_function(
            {
                "model": "gpt-test",
                "input": [
                    {"type": "custom_tool_call", "call_id": "call_1", "name": "lookup", "input": "x"},
                ],
                "tools": [
                    {"type": "custom", "name": "lookup", "parameters": {}},
                    {"type": "web_search"},
                ],
            }
        )
        self.assertEqual([tool["type"] for tool in converted["tools"]], ["function"])
        self.assertEqual(converted["tools"][0]["name"], "lookup")
        self.assertEqual(converted["input"][0]["type"], "function_call")

    def test_responses_to_anthropic_preserves_reasoning_and_tool_choice(self):
        converted = _convert_request(
            {
                "model": "claude-test",
                "instructions": "Be brief.",
                "input": [
                    {"type": "reasoning", "summary": [{"type": "summary_text", "text": "think hard"}]},
                    {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{}"},
                    {"type": "function_call_output", "call_id": "call_1", "output": "found"},
                    {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "next"}]},
                ],
                "max_output_tokens": 8192,
                "reasoning": {"effort": "medium"},
                "tools": [
                    {
                        "type": "function",
                        "name": "lookup",
                        "description": "Look up a value",
                        "parameters": {"type": "object", "properties": {}},
                    }
                ],
                "tool_choice": "auto",
                "stream": True,
            },
            "responses",
            "anthropic",
        )
        self.assertEqual(converted["system"], "Be brief.")
        self.assertEqual(converted["thinking"], {"type": "enabled", "budget_tokens": 2048})
        self.assertEqual(converted["tool_choice"], {"type": "auto"})
        assistant = next(item for item in converted["messages"] if item["role"] == "assistant")
        self.assertEqual(assistant["content"][0]["type"], "thinking")
        self.assertEqual(assistant["content"][0]["thinking"], "think hard")
        self.assertEqual(assistant["content"][1]["type"], "tool_use")

    def test_responses_to_anthropic_tool_choice_none_keeps_tools(self):
        converted = _convert_request(
            {
                "model": "claude-test",
                "input": "hi",
                "tools": [
                    {
                        "type": "function",
                        "name": "lookup",
                        "parameters": {"type": "object", "properties": {}},
                    }
                ],
                "tool_choice": "none",
            },
            "responses",
            "anthropic",
        )
        self.assertIn("tools", converted)
        self.assertEqual(converted["tool_choice"], {"type": "none"})

    def test_responses_chat_text_format_round_trip(self):
        chat = _responses_to_chat(
            {
                "model": "gpt-test",
                "input": "return json",
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "result",
                        "strict": True,
                        "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
                    }
                },
            }
        )
        self.assertEqual(
            chat["response_format"],
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "result",
                    "strict": True,
                    "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
                },
            },
        )
        responses = _convert_request(
            {
                "model": "gpt-test",
                "messages": [{"role": "user", "content": "return json"}],
                "response_format": chat["response_format"],
            },
            "chat",
            "responses",
        )
        self.assertEqual(
            responses["text"]["format"],
            {
                "type": "json_schema",
                "name": "result",
                "strict": True,
                "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
            },
        )

    def test_anthropic_to_responses_preserves_thinking_and_multimodal_tool_result(self):
        converted = _convert_request(
            {
                "model": "gpt-test",
                "system": [{"type": "text", "text": "sys"}],
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "call_1",
                                "content": [
                                    {"type": "text", "text": "ok"},
                                    {
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": "image/png",
                                            "data": "AAAA",
                                        },
                                    },
                                ],
                            }
                        ],
                    },
                    {
                        "role": "assistant",
                        "content": [{"type": "thinking", "thinking": "think"}, {"type": "text", "text": "answer"}],
                    },
                ],
            },
            "anthropic",
            "responses",
        )
        self.assertEqual(converted["instructions"], "sys")
        self.assertEqual(converted["input"][0]["type"], "function_call_output")
        self.assertEqual(converted["input"][0]["output"][1]["type"], "input_image")
        self.assertEqual(converted["input"][1]["type"], "reasoning")
        self.assertEqual(converted["input"][1]["summary"][0]["text"], "think")
        self.assertEqual(converted["input"][2]["role"], "assistant")

    def test_anthropic_to_chat_maps_thinking(self):
        chat = _anthropic_to_chat(
            {
                "model": "gpt-test",
                "thinking": {"type": "enabled", "budget_tokens": 2048},
                "messages": [
                    {
                        "role": "assistant",
                        "content": [{"type": "thinking", "thinking": "think"}, {"type": "text", "text": "answer"}],
                    }
                ],
            }
        )
        self.assertEqual(chat["reasoning_effort"], "medium")
        self.assertEqual(chat["messages"][0]["reasoning_content"], "think")


class StreamUsageTests(unittest.TestCase):
    def test_chat_stream_requests_usage(self):
        body = {"model": "gpt-test", "stream": True}
        updated = _include_stream_usage(body, "chat")
        self.assertTrue(updated["stream_options"]["include_usage"])

    def test_non_chat_stream_keeps_body(self):
        body = {"model": "claude", "stream": True}
        self.assertEqual(_include_stream_usage(body, "anthropic"), body)


class StreamConversionTests(unittest.TestCase):
    def test_responses_stream_exposes_reasoning_item_for_next_turn(self):
        class ReasoningChatResponse:
            def iter_lines(self, chunk_size=1):
                yield b'data: {"id":"chatcmpl-test","model":"gpt-test","choices":[{"delta":{"reasoning_content":"think"},"finish_reason":null}]}'
                yield b'data: {"id":"chatcmpl-test","model":"gpt-test","choices":[{"delta":{"content":"answer"},"finish_reason":null}]}'
                yield b'data: {"id":"chatcmpl-test","model":"gpt-test","choices":[{"delta":{},"finish_reason":"stop"}]}'

        renderer = _StreamRenderer("responses", "gpt-test")
        chunks = []
        for event in _canonical_stream_events(ReasoningChatResponse(), "chat", "gpt-test"):
            chunks.extend(renderer.feed(event))
        chunks.extend(renderer.finish())

        payloads = []
        for line in b"".join(chunks).decode("utf-8").splitlines():
            if line.startswith("data:"):
                payloads.append(json.loads(line[5:].strip()))
        completed = next(payload for payload in payloads if payload.get("type") == "response.completed")
        reasoning_item = completed["response"]["output"][0]
        self.assertEqual(reasoning_item["type"], "reasoning")
        self.assertEqual(reasoning_item["summary"][0]["text"], "think")

        converted = _responses_to_chat({"model": "gpt-test", "input": [reasoning_item]})
        self.assertEqual(converted["messages"][0]["reasoning_content"], "think")

    def test_chat_stream_skips_non_dict_payloads(self):
        class NullChunkResponse:
            def iter_lines(self, chunk_size=1):
                yield b'data: {"id":"chatcmpl-test","model":"gpt-test","choices":[{"delta":{"content":"ok"},"finish_reason":null}]}'
                yield b"data: null"
                yield b'data: {"id":"chatcmpl-test","model":"gpt-test","choices":[{"delta":{},"finish_reason":"stop"}]}'

        renderer = _StreamRenderer("responses", "gpt-test")
        chunks = []
        for event in _canonical_stream_events(NullChunkResponse(), "chat", "gpt-test"):
            chunks.extend(renderer.feed(event))
        chunks.extend(renderer.finish())

        stream = b"".join(chunks)
        self.assertIn(b'"type": "response.completed"', stream)
        self.assertNotIn(b'"type": "error"', stream)

    def test_chat_disconnect_after_finish_still_completes_responses_stream(self):
        class BrokenAfterFinish:
            def iter_lines(self, chunk_size=1):
                yield b'data: {"id":"chatcmpl-test","model":"gpt-test","choices":[{"delta":{"content":"ok"},"finish_reason":null}]}'
                yield b'data: {"id":"chatcmpl-test","model":"gpt-test","choices":[{"delta":{},"finish_reason":"stop"}]}'
                raise RuntimeError("Response ended prematurely")

        renderer = _StreamRenderer("responses", "gpt-test")
        chunks = []
        for event in _canonical_stream_events(BrokenAfterFinish(), "chat", "gpt-test"):
            chunks.extend(renderer.feed(event))

        self.assertIn(b'"type": "response.completed"', b"".join(chunks))

    def test_chat_disconnect_before_finish_still_raises(self):
        class BrokenBeforeFinish:
            def iter_lines(self, chunk_size=1):
                yield b'data: {"id":"chatcmpl-test","model":"gpt-test","choices":[{"delta":{"content":"ok"},"finish_reason":null}]}'
                raise RuntimeError("Response ended prematurely")

        with self.assertRaisesRegex(RuntimeError, "Response ended prematurely"):
            list(_canonical_stream_events(BrokenBeforeFinish(), "chat", "gpt-test"))

    def test_relay_completes_responses_after_chat_disconnect_with_output(self):
        class BrokenResponse:
            status_code = 200

            def iter_lines(self, chunk_size=1):
                yield b'data: {"id":"chatcmpl-test","model":"gpt-test","choices":[{"delta":{"content":"ok"},"finish_reason":null}]}'
                raise RuntimeError("Response ended prematurely")

            def close(self):
                pass

        class Handler:
            def __init__(self):
                self.wfile = BytesIO()
                self.close_connection = False

            def send_response(self, status):
                pass

            def send_header(self, name, value):
                pass

            def end_headers(self):
                pass

        class App:
            def record_relay_usage(self, *args):
                pass

        server = RelayServer(App(), "127.0.0.1", 0)
        handler = Handler()
        with patch.object(server, "log_exception") as log_exception:
            server.write_upstream(
                handler,
                BrokenResponse(),
                "chat",
                "responses",
                "gpt-test",
                {"id": "project-test", "name": "test"},
                True,
                True,
            )

        stream = handler.wfile.getvalue()
        self.assertIn(b'"type": "response.completed"', stream)
        self.assertNotIn(b'"type": "error"', stream)
        log_exception.assert_called_once()


class RelayErrorLoggingTests(unittest.TestCase):
    class App:
        def __init__(self, store):
            self.store = store
            self.messages = []

        def _post(self, callback, *args):
            callback(*args)

        def _log(self, message):
            self.messages.append(message)

    @staticmethod
    def post(server, payload, headers=None):
        connection = HTTPConnection("127.0.0.1", server.port, timeout=3)
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        connection.request("POST", "/v1/responses", json.dumps(payload), request_headers)
        response = connection.getresponse()
        response.read()
        status = response.status
        connection.close()
        return status

    def test_relay_logs_404_details_and_respects_switch(self):
        app = self.App({"projects": [], "relay": {"project_ids": []}})
        server = RelayServer(app, "127.0.0.1", 0, error_logging_enabled=True)
        with (
            TemporaryDirectory() as temp_dir,
            patch("ai_probe.relay.RELAY_ERROR_LOG", Path(temp_dir) / "relay-errors.jsonl") as log_path,
        ):
            server.start()
            try:
                self.assertEqual(
                    self.post(server, {"model": "missing-model", "input": "hello"}, {"Authorization": "Bearer secret"}),
                    404,
                )
                entry = json.loads(log_path.read_text(encoding="utf-8").strip())
                self.assertEqual(entry["status"], 404)
                self.assertEqual(entry["path"], "/v1/responses")
                self.assertEqual(entry["request_body"]["model"], "missing-model")
                self.assertEqual(entry["request_headers"]["Authorization"], "[REDACTED]")

                server.error_logging_enabled = False
                self.assertEqual(self.post(server, {"model": "still-missing", "input": "hello"}), 404)
                self.assertEqual(len(log_path.read_text(encoding="utf-8").splitlines()), 1)
            finally:
                server.stop()

    def test_relay_logs_400_response(self):
        app = self.App({"projects": [], "relay": {"project_ids": []}})
        server = RelayServer(app, "127.0.0.1", 0, error_logging_enabled=True)
        with (
            TemporaryDirectory() as temp_dir,
            patch("ai_probe.relay.RELAY_ERROR_LOG", Path(temp_dir) / "relay-errors.jsonl") as log_path,
        ):
            server.start()
            try:
                self.assertEqual(self.post(server, {"input": "missing model"}), 400)
            finally:
                server.stop()

            entry = json.loads(log_path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["status"], 400)

    def test_relay_logs_502_upstream_attempt(self):
        project = {
            "id": "project-test",
            "name": "test",
            "base_url": "https://example.test/v1",
            "api_key": "upstream-secret",
            "api_keys": [{"id": "key-test", "name": "default", "value": "upstream-secret"}],
            "proxy_url": "",
            "skip_ssl_verify": False,
            "api_mode": "responses",
            "headers_mode": "json",
            "custom_headers": "",
            "models": [{"id": "gpt-test", "api_key_id": "key-test"}],
        }
        app = self.App({"projects": [project], "relay": {"project_ids": ["project-test"]}})
        server = RelayServer(app, "127.0.0.1", 0, error_logging_enabled=True)

        class UpstreamResponse:
            ok = False
            status_code = 404
            headers = {"Content-Type": "application/json"}
            reason = "Not Found"
            text = '{"error":{"message":"upstream missing"}}'

            def json(self):
                return {"error": {"message": "upstream missing"}}

            def close(self):
                pass

        payload = {
            "model": "gpt-test",
            "input": [
                {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{}"},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "", "annotations": []}],
                },
                {"type": "function_call_output", "call_id": "call_1", "output": "found"},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "done", "annotations": []}],
                },
            ],
        }
        with (
            TemporaryDirectory() as temp_dir,
            patch("ai_probe.relay.RELAY_ERROR_LOG", Path(temp_dir) / "relay-errors.jsonl") as log_path,
            patch("ai_probe.relay.requests.post", return_value=UpstreamResponse()) as upstream_post,
        ):
            server.start()
            try:
                self.assertEqual(self.post(server, payload), 502)
            finally:
                server.stop()

            upstream_input = upstream_post.call_args.kwargs["json"]["input"]
            self.assertEqual(
                [item["type"] for item in upstream_input],
                ["function_call", "function_call_output", "message"],
            )
            self.assertEqual(upstream_input[-1]["content"][0]["text"], "done")
            entry = json.loads(log_path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["status"], 502)
            self.assertEqual(entry["attempts"][0]["upstream_status"], 404)
            self.assertEqual(entry["attempts"][0]["upstream_error"], "upstream missing")
            self.assertEqual(entry["attempts"][0]["upstream_headers"]["Authorization"], "[REDACTED]")


if __name__ == "__main__":
    unittest.main()
