import json
import time
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
from ai_probe.relay import RelayServer, _prepare_responses_upstream_body


class RequestModeTests(unittest.TestCase):
    def test_request_mode(self):
        self.assertEqual(_request_mode("/v1/chat/completions"), "chat")
        self.assertEqual(_request_mode("/v1/responses"), "responses")
        self.assertEqual(_request_mode("/v1/messages"), "anthropic")


class ResponsesInputNormalizationTests(unittest.TestCase):
    def test_deepseek_summary_reasoning_keeps_summary_and_content(self):
        body = {
            "model": "deepseek-v4-pro",
            "input": [
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "think hard"}]},
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "continue"}]},
            ],
        }
        result = _prepare_responses_upstream_body(body, "deepseek-v4-pro")
        self.assertEqual(
            result["input"][0],
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "think hard"}]},
        )

    def test_deepseek_drops_empty_reasoning_items(self):
        body = {
            "model": "deepseek-v4-pro",
            "input": [
                {"type": "reasoning", "summary": []},
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "continue"}]},
            ],
        }
        result = _prepare_responses_upstream_body(body, "deepseek-v4-pro")
        self.assertEqual([item["type"] for item in result["input"]], ["message"])

    def test_deepseek_parallel_calls_replay_reasoning_and_repair_order(self):
        body = {
            "model": "deepseek-v4-flash",
            "input": [
                {
                    "type": "reasoning",
                    "id": "rs_think",
                    "summary": [{"type": "summary_text", "text": "think"}],
                    "content": [{"type": "reasoning_text", "text": "think"}],
                    "encrypted_content": "enc-1",
                },
                {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "one", "arguments": "{}"},
                {"type": "function_call", "id": "fc_2", "call_id": "call_2", "name": "two", "arguments": "{}"},
                {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "post tool"}]},
                {"type": "function_call_output", "call_id": "call_1", "output": "one"},
                {"type": "function_call_output", "call_id": "call_2", "output": "two"},
            ],
        }
        result = _prepare_responses_upstream_body(body, "deepseek-v4-flash")
        output = result["input"]
        self.assertEqual(
            [item["type"] for item in output],
            [
                "reasoning",
                "function_call",
                "function_call_output",
                "reasoning",
                "function_call",
                "function_call_output",
                "message",
            ],
        )
        self.assertEqual(output[3]["content"][0]["text"], "think")
        self.assertEqual(output[4]["call_id"], "call_2")
        self.assertNotIn("id", output[0])
        self.assertNotIn("id", output[1])
        self.assertNotIn("id", output[3])
        self.assertNotIn("id", output[5])
        self.assertEqual(body["input"][0]["id"], "rs_think")
        self.assertEqual(body["input"][1]["id"], "fc_1")
        self.assertEqual(body["input"][-1]["call_id"], "call_2")

    def test_non_deepseek_responses_input_stays_untouched(self):
        body = {
            "model": "gpt-5",
            "input": [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "keep"}]}],
        }
        result = _prepare_responses_upstream_body(body, "gpt-5")
        self.assertIs(result, body)

    def test_non_deepseek_reasoning_content_becomes_summary(self):
        body = {
            "model": "gpt-5.6-sol",
            "input": [
                {
                    "type": "reasoning",
                    "summary": [],
                    "content": [{"type": "reasoning_text", "text": "think"}],
                },
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "continue"}]},
            ],
        }
        result = _prepare_responses_upstream_body(body, "gpt-5.6-sol")
        self.assertEqual(
            result["input"][0],
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "think"}]},
        )

    def test_non_deepseek_responses_strips_server_item_ids(self):
        body = {
            "model": "gpt-5.6-sol",
            "input": [
                {"type": "message", "id": "msg_previous", "role": "user", "content": []},
                {"type": "reasoning", "id": "rs_previous", "summary": []},
                {
                    "type": "function_call",
                    "id": "fc_previous",
                    "call_id": "call_keep",
                    "name": "tool",
                    "arguments": "{}",
                },
                {"type": "custom_tool_call_output", "id": "ctco_previous", "call_id": "call_keep", "output": "ok"},
            ],
        }
        result = _prepare_responses_upstream_body(body, "gpt-5.6-sol")
        self.assertEqual(
            [item["type"] for item in result["input"]], ["message", "function_call", "custom_tool_call_output"]
        )
        self.assertTrue(all("id" not in item for item in result["input"]))
        self.assertEqual(result["input"][1]["call_id"], "call_keep")

    def test_non_deepseek_web_search_call_gets_action_queries(self):
        body = {
            "model": "gpt-5.6-sol",
            "input": [
                {
                    "type": "web_search_call",
                    "id": "msg_ws_1",
                    "status": "completed",
                    "action": {"type": "search", "query": "test query"},
                }
            ],
        }
        result = _prepare_responses_upstream_body(body, "gpt-5.6-sol")
        self.assertEqual(result["input"][0]["action"], {"type": "search", "queries": [{"query": "test query"}]})

    def test_deepseek_web_search_call_gets_action_queries(self):
        body = {
            "model": "deepseek-v4-flash",
            "input": [
                {
                    "type": "web_search_call",
                    "id": "msg_ws_1",
                    "status": "completed",
                    "action": {"type": "search", "query": "test query"},
                }
            ],
        }
        result = _prepare_responses_upstream_body(body, "deepseek-v4-flash")
        self.assertEqual(result["input"][0]["action"]["queries"], [{"query": "test query"}])


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

    def test_responses_to_chat_normalizes_one_of_tool_schema(self):
        converted = _responses_to_chat(
            {
                "model": "deepseek-test",
                "input": "hi",
                "tools": [
                    {
                        "type": "function",
                        "name": "automation_update",
                        "parameters": {
                            "$defs": {"__schema0": {"type": "string"}},
                            "oneOf": [
                                {
                                    "type": "object",
                                    "properties": {"id": {"$ref": "#/$defs/__schema0"}},
                                    "required": ["id"],
                                    "additionalProperties": False,
                                }
                            ],
                        },
                    }
                ],
            }
        )
        parameters = converted["tools"][0]["function"]["parameters"]
        self.assertEqual(parameters["type"], "object")
        self.assertEqual(parameters["oneOf"][0]["properties"]["id"]["$ref"], "#/$defs/__schema0")

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
        self.assertIn(b'"type": "error"', stream)
        self.assertIn(b'"status": "incomplete"', stream)
        log_exception.assert_called_once()

    def test_responses_passthrough_completes_after_disconnect_with_output(self):
        class BrokenResponse:
            status_code = 200
            headers = {}

            def iter_content(self, chunk_size=8192):
                yield (
                    b"event: response.created\n"
                    b'data: {"type":"response.created","sequence_number":0,"response":{"id":"resp-test","model":"gpt-test","created_at":1}}\n\n'
                    b"event: response.output_text.delta\n"
                    b'data: {"type":"response.output_text.delta","sequence_number":1,"delta":"ok"}\n\n'
                )
                raise RuntimeError("stream closed")

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

        handler = Handler()
        RelayServer.passthrough_stream(App(), handler, BrokenResponse(), "responses", {}, "gpt-test")

        stream = handler.wfile.getvalue()
        self.assertIn(b'"type":"response.output_text.delta"', stream)
        self.assertIn(b'"type": "response.completed"', stream)
        self.assertIn(b'"id": "resp-test"', stream)

    def test_responses_passthrough_read_timeout_still_completes_stream(self):
        class TimeoutResponse:
            status_code = 200
            headers = {}

            def iter_content(self, chunk_size=8192):
                raise RuntimeError("Read timed out. (read timeout=15)")

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

        handler = Handler()
        RelayServer.passthrough_stream(App(), handler, TimeoutResponse(), "responses", {}, "gpt-test")

        stream = handler.wfile.getvalue()
        self.assertIn(b'"type": "error"', stream)
        self.assertIn(b'"type": "response.completed"', stream)
        self.assertIn(b"Read timed out", stream)

    def test_responses_error_event_is_followed_by_completed(self):
        renderer = _StreamRenderer("responses", "gpt-test")
        chunks = renderer.feed({"type": "error", "message": "upstream failed"})

        stream = b"".join(chunks)
        self.assertIn(b'"type": "error"', stream)
        self.assertIn(b'"type": "response.completed"', stream)


class RelayErrorLoggingTests(unittest.TestCase):
    class App:
        def __init__(self, store):
            self.store = store
            self.messages = []
            self.usage_records = []

        def _post(self, callback, *args):
            callback(*args)

        def _log(self, message):
            self.messages.append(message)

        def record_relay_usage(self, project, model, input_tokens, output_tokens, cached_tokens):
            self.usage_records.append((model, input_tokens, output_tokens, cached_tokens))

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
            patch("ai_probe.relay.RELAY_ERROR_LOG", Path(temp_dir) / "relay-errors.jsonl"),
        ):
            log_path = server._current_log_path()
            server.start()
            try:
                self.assertEqual(
                    self.post(
                        server,
                        {"model": "missing-model", "input": "hello", "instructions": "x" * 100_000},
                        {"Authorization": "Bearer secret"},
                    ),
                    404,
                )
                entry = json.loads(log_path.read_text(encoding="utf-8").strip())
                self.assertEqual(entry["status"], 404)
                self.assertEqual(entry["path"], "/v1/responses")
                self.assertEqual(entry["request"]["model"], "missing-model")
                self.assertEqual(entry["request"]["input"], {"kind": "text", "chars": 5})
                self.assertEqual(entry["request"]["instructions_chars"], 100_000)
                self.assertNotIn("request_headers", entry)
                self.assertNotIn("request_body", entry)
                self.assertLess(log_path.stat().st_size, 4000)

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
            patch("ai_probe.relay.RELAY_ERROR_LOG", Path(temp_dir) / "relay-errors.jsonl"),
        ):
            log_path = server._current_log_path()
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
            patch("ai_probe.relay.RELAY_ERROR_LOG", Path(temp_dir) / "relay-errors.jsonl"),
            patch("ai_probe.relay.requests.post", return_value=UpstreamResponse()) as upstream_post,
        ):
            log_path = server._current_log_path()
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
            self.assertEqual(entry["attempts"][0]["upstream_trace"], {"content-type": "application/json"})
            self.assertNotIn("upstream_headers", entry["attempts"][0])
            self.assertNotIn("upstream_body", entry["attempts"][0])
            self.assertNotIn("upstream_response_headers", entry["attempts"][0])

    def test_relay_stream_502_ends_with_response_completed(self):
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
        with (
            TemporaryDirectory() as temp_dir,
            patch("ai_probe.relay.RELAY_ERROR_LOG", Path(temp_dir) / "relay-errors.jsonl"),
            patch(
                "ai_probe.relay.requests.post",
                side_effect=RuntimeError("Read timed out. (read timeout=15)"),
            ),
        ):
            server.start()
            try:
                connection = HTTPConnection("127.0.0.1", server.port, timeout=3)
                connection.request(
                    "POST",
                    "/v1/responses",
                    json.dumps({"model": "gpt-test", "input": "hello", "stream": True}),
                    {"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                body = response.read().decode("utf-8")
                status = response.status
                connection.close()
            finally:
                server.stop()

            self.assertEqual(status, 200)
            self.assertIn("event: error", body)
            self.assertIn("event: response.completed", body)
            self.assertIn("Read timed out", body)

    def test_relay_log_keeps_only_current_day_file(self):
        app = self.App({"projects": [], "relay": {"project_ids": []}})
        server = RelayServer(app, "127.0.0.1", 0, error_logging_enabled=True)
        with (
            TemporaryDirectory() as temp_dir,
            patch("ai_probe.relay.RELAY_ERROR_LOG", Path(temp_dir) / "relay-errors.jsonl"),
        ):
            stale_path = Path(temp_dir) / "relay-errors-2000-01-01.jsonl"
            stale_path.write_text('{"stale": true}\n', encoding="utf-8")
            legacy_path = Path(temp_dir) / "relay-errors.jsonl"
            legacy_path.write_text('{"legacy": true}\n', encoding="utf-8")
            log_path = server._current_log_path()
            log_path.write_text('{"kept": true}\n', encoding="utf-8")

            server.log_error("test", status=503, message="boom")

            self.assertFalse(stale_path.exists())
            self.assertFalse(legacy_path.exists())
            self.assertTrue(log_path.exists())
            lines = log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[1])["status"], 503)

    def test_relay_request_log_records_success_and_usage(self):
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
        server = RelayServer(app, "127.0.0.1", 0, error_logging_enabled=True, request_logging_enabled=True)

        class UpstreamResponse:
            ok = True
            status_code = 200
            headers = {"Content-Type": "text/event-stream"}

            def iter_content(self, chunk_size=8192):
                yield b'data: {"type":"response.created","response":{"id":"resp-log","model":"gpt-test"}}\n\n'
                yield (
                    b'data: {"type":"response.completed","response":{"id":"resp-log","model":"gpt-test",'
                    b'"usage":{"input_tokens":11,"output_tokens":7,"total_tokens":18}}}\n\n'
                )

            def close(self):
                pass

        with (
            TemporaryDirectory() as temp_dir,
            patch("ai_probe.relay.RELAY_ERROR_LOG", Path(temp_dir) / "relay-errors.jsonl"),
            patch("ai_probe.relay.RELAY_REQUEST_LOG", Path(temp_dir) / "relay-requests.jsonl"),
            patch("ai_probe.relay.requests.post", return_value=UpstreamResponse()),
        ):
            error_log_path = server._current_log_path()
            request_log_path = server._current_request_log_path()
            self.assertEqual(error_log_path.parent, request_log_path.parent)
            server.start()
            try:
                status = self.post(server, {"model": "gpt-test", "input": "hello", "stream": True})
            finally:
                server.stop()

            self.assertFalse(error_log_path.exists())
            deadline = time.time() + 2
            while not request_log_path.exists() and time.time() < deadline:
                time.sleep(0.02)
            entry = json.loads(request_log_path.read_text(encoding="utf-8").strip())

        self.assertEqual(status, 200)
        self.assertFalse(error_log_path.exists())
        self.assertEqual(entry["status"], 200)
        self.assertEqual(entry["path"], "/v1/responses")
        self.assertEqual(entry["model"], "gpt-test")
        self.assertEqual(entry["project"], "test")
        self.assertEqual(entry["upstream_mode"], "responses")
        self.assertFalse(entry["converted"])
        self.assertTrue(entry["requested_stream"])
        self.assertGreaterEqual(entry["duration_ms"], 0)
        self.assertEqual(entry["usage"], {"input_tokens": 11, "output_tokens": 7, "cached_tokens": 0})
        self.assertIn("request", entry)

    def test_relay_request_log_records_rejections_in_same_directory(self):
        app = self.App({"projects": [], "relay": {"project_ids": []}})
        server = RelayServer(app, "127.0.0.1", 0, "secret-key", error_logging_enabled=True)
        with (
            TemporaryDirectory() as temp_dir,
            patch("ai_probe.relay.RELAY_ERROR_LOG", Path(temp_dir) / "relay-errors.jsonl"),
            patch("ai_probe.relay.RELAY_REQUEST_LOG", Path(temp_dir) / "relay-requests.jsonl"),
        ):
            error_log_path = server._current_log_path()
            request_log_path = server._current_request_log_path()
            server.start()
            try:
                self.assertEqual(self.post(server, {"model": "missing-model", "input": "hi"}), 401)
                self.assertEqual(
                    self.post(
                        server,
                        {"model": "missing-model", "input": "hi"},
                        {"Authorization": "Bearer secret-key"},
                    ),
                    404,
                )
            finally:
                server.stop()

            entries = [json.loads(line) for line in request_log_path.read_text(encoding="utf-8").splitlines()]
            error_entries = [json.loads(line) for line in error_log_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(request_log_path.parent, error_log_path.parent)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["status"], 401)
        self.assertTrue(entries[0]["auth_failed"])
        self.assertNotIn("request", entries[0])
        self.assertEqual(entries[1]["status"], 404)
        self.assertEqual(entries[1]["model"], "missing-model")
        self.assertEqual(entries[1]["error"], "未启用模型：missing-model")
        self.assertTrue(any(item["status"] == 404 for item in error_entries))

    def test_relay_request_log_disabled_writes_nothing(self):
        app = self.App({"projects": [], "relay": {"project_ids": []}})
        server = RelayServer(app, "127.0.0.1", 0, request_logging_enabled=False)
        with (
            TemporaryDirectory() as temp_dir,
            patch("ai_probe.relay.RELAY_ERROR_LOG", Path(temp_dir) / "relay-errors.jsonl"),
            patch("ai_probe.relay.RELAY_REQUEST_LOG", Path(temp_dir) / "relay-requests.jsonl"),
        ):
            request_log_path = server._current_request_log_path()
            server.start()
            try:
                self.assertEqual(self.post(server, {"model": "missing-model", "input": "hi"}), 404)
            finally:
                server.stop()

            self.assertFalse(request_log_path.exists())

    def test_relay_request_log_keeps_only_current_day_file(self):
        app = self.App({"projects": [], "relay": {"project_ids": []}})
        server = RelayServer(app, "127.0.0.1", 0)
        with (
            TemporaryDirectory() as temp_dir,
            patch("ai_probe.relay.RELAY_ERROR_LOG", Path(temp_dir) / "relay-errors.jsonl"),
            patch("ai_probe.relay.RELAY_REQUEST_LOG", Path(temp_dir) / "relay-requests.jsonl"),
        ):
            stale_path = Path(temp_dir) / "relay-requests-2000-01-01.jsonl"
            stale_path.write_text('{"stale": true}\n', encoding="utf-8")
            legacy_path = Path(temp_dir) / "relay-requests.jsonl"
            legacy_path.write_text('{"legacy": true}\n', encoding="utf-8")

            class Handler:
                _relay_request_context = {}

            server.log_request(Handler(), 200)

            self.assertFalse(stale_path.exists())
            self.assertFalse(legacy_path.exists())

    def test_relay_debug_capture_records_full_payloads_and_redacts_secrets(self):
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
        server = RelayServer(
            app, "127.0.0.1", 0, error_logging_enabled=True, request_logging_enabled=True, request_debug_capture=True
        )

        upstream_raw = json.dumps(
            {
                "id": "resp-x",
                "object": "response",
                "status": "completed",
                "output": [{"type": "message", "role": "assistant", "content": []}],
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }
        )

        class UpstreamResponse:
            ok = True
            status_code = 200
            headers = {
                "Content-Type": "application/json",
                "x-request-id": "trace-debug",
                "Set-Cookie": "session=abc",
            }

            def iter_content(self, chunk_size=8192):
                yield upstream_raw.encode("utf-8")

            def close(self):
                pass

        captured_kwargs = {}

        def fake_post(url, **kwargs):
            captured_kwargs.update(kwargs)
            return UpstreamResponse()

        with (
            TemporaryDirectory() as temp_dir,
            patch("ai_probe.relay.RELAY_ERROR_LOG", Path(temp_dir) / "relay-errors.jsonl"),
            patch("ai_probe.relay.RELAY_REQUEST_LOG", Path(temp_dir) / "relay-requests.jsonl"),
            patch("ai_probe.relay.requests.post", side_effect=fake_post),
        ):
            request_log_path = server._current_request_log_path()
            server.start()
            try:
                status = self.post(
                    server,
                    {"model": "gpt-test", "input": "帮我写首诗"},
                    {"Authorization": "Bearer relay-client-secret"},
                )
            finally:
                server.stop()

            entry = json.loads(request_log_path.read_text(encoding="utf-8").strip())

        self.assertEqual(status, 200)
        # 入站请求体完整保留（含中文正文）
        incoming = entry["debug_incoming_body"]
        self.assertEqual(incoming["json"]["input"], "帮我写首诗")
        # 上游请求体完整保留
        upstream_request = entry["attempts"][0]["upstream_request"]
        self.assertEqual(upstream_request["json"]["input"], "帮我写首诗")
        # 返回体完整保留
        response_entry = entry["debug_response_body"]
        self.assertEqual(response_entry["json"]["output"], [{"type": "message", "role": "assistant", "content": []}])
        self.assertEqual(response_entry["json"]["usage"]["input_tokens"], 5)
        # 响应头保留 trace 并脱敏 Cookie
        self.assertEqual(entry["debug_response_headers"].get("x-request-id"), "trace-debug")
        self.assertEqual(entry["debug_response_headers"].get("Set-Cookie"), "[REDACTED]")
        # 客户端发来的 Authorization 头被脱敏
        self.assertEqual(entry["debug_incoming_headers"].get("Authorization"), "[REDACTED]")
        # 转发给上游的头里密钥同样脱敏
        upstream_headers_entry = entry["attempts"][0]["upstream_request_headers"]
        sent_auth = [value for key, value in upstream_headers_entry.items() if key.lower() == "authorization"]
        self.assertTrue(sent_auth and all(value == "[REDACTED]" for value in sent_auth))
        # usage 摘要照常记录
        self.assertEqual(entry["usage"], {"input_tokens": 5, "output_tokens": 3, "cached_tokens": 0})

    def test_relay_debug_capture_records_sse_stream(self):
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
        server = RelayServer(
            app, "127.0.0.1", 0, error_logging_enabled=False, request_logging_enabled=True, request_debug_capture=True
        )

        class UpstreamResponse:
            ok = True
            status_code = 200
            headers = {"Content-Type": "text/event-stream"}

            def iter_content(self, chunk_size=8192):
                yield b'data: {"type":"response.created","response":{"id":"r-dbg","model":"gpt-test"}}\n\n'
                yield b'data: {"type":"response.completed","response":{"id":"r-dbg","model":"gpt-test","usage":{"input_tokens":9,"output_tokens":4}}}\n\n'

            def close(self):
                pass

        with (
            TemporaryDirectory() as temp_dir,
            patch("ai_probe.relay.RELAY_ERROR_LOG", Path(temp_dir) / "relay-errors.jsonl"),
            patch("ai_probe.relay.RELAY_REQUEST_LOG", Path(temp_dir) / "relay-requests.jsonl"),
            patch("ai_probe.relay.requests.post", return_value=UpstreamResponse()),
        ):
            request_log_path = server._current_request_log_path()
            server.start()
            try:
                status = self.post(server, {"model": "gpt-test", "input": "你好，请介绍一下你自己", "stream": True})
            finally:
                server.stop()

            entry = json.loads(request_log_path.read_text(encoding="utf-8").strip())

        self.assertEqual(status, 200)
        body = entry["debug_response_body"]
        self.assertIn('"type":"response.created"', body["text"])
        self.assertIn('"type":"response.completed"', body["text"])
        self.assertIn("你好", entry["debug_incoming_body"]["json"]["input"])

    def test_relay_debug_capture_off_keeps_summary_only(self):
        app = self.App({"projects": [], "relay": {"project_ids": []}})
        server = RelayServer(app, "127.0.0.1", 0, error_logging_enabled=False, request_logging_enabled=True)
        with (
            TemporaryDirectory() as temp_dir,
            patch("ai_probe.relay.RELAY_ERROR_LOG", Path(temp_dir) / "relay-errors.jsonl"),
            patch("ai_probe.relay.RELAY_REQUEST_LOG", Path(temp_dir) / "relay-requests.jsonl"),
        ):
            server.start()
            try:
                self.post(server, {"model": "missing-model", "input": "hi"})
            finally:
                server.stop()
            log_text = ""
            request_log_path = server._current_request_log_path()
            if request_log_path.exists():
                log_text = request_log_path.read_text(encoding="utf-8")

        self.assertNotIn("debug_incoming_body", log_text)
        self.assertNotIn("debug_response_body", log_text)


class RelayRouteSnapshotTests(unittest.TestCase):
    """UI 与请求线程共享路由表时的免锁快照语义。"""

    class App:
        def __init__(self, store):
            self.store = store

    def test_invalidate_rebuilds_snapshot_and_filters_disabled(self):
        project = {"id": "p1", "name": "P1", "models": [{"id": "m1"}, {"id": "m2"}]}
        app = self.App({"projects": [project], "relay": {"project_ids": ["p1"]}})
        server = RelayServer(app, "127.0.0.1", 0)
        first = server.model_routes()
        self.assertEqual(set(first), {"m1", "m2"})

        # invalidate 后懒重建得到新快照；旧引用仍可被并发中的调用方安全使用
        server.invalidate_routes()
        second = server.model_routes()
        self.assertIsNot(second, first)
        self.assertEqual(set(second), {"m1", "m2"})

        # 关闭启用项目后，路由表即时反映（无需重启服务器）
        app.store["relay"]["project_ids"] = []
        server.invalidate_routes()
        self.assertEqual(server.model_routes(), {})


if __name__ == "__main__":
    unittest.main()
