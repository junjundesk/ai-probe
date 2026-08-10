"""无 GUI 的核心回归自检。"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from . import client as client_module
from .client import OpenAIClient
from .config import HARD_CODED_AES_KEY, _derive_config_key, decrypt_config, encrypt_config
from .projects import (
    _sync_project_keys,
    api_key_label,
    client_from_project,
    new_project,
    project_key_for_model,
)
from .protocols import (
    _chat_to_anthropic,
    _chat_to_responses,
    _convert_request,
    _include_stream_usage,
    _normalize_response,
    _request_mode,
    _responses_to_chat,
    _SSEUsageCollector,
)
from .relay import RelayServer
from .usage import UsageStats
from .utils import (
    compact_number,
    extract_stream_text,
    extract_usage_tokens,
    format_cache_summary,
    normalize_base_url,
    normalize_proxy_url,
    parse_custom_headers,
)


def self_test():
    assert compact_number(999) == "999"
    assert compact_number(1_000) == "1k"
    assert compact_number(12_345) == "12.3k"
    assert compact_number(999_999) == "1m"
    assert compact_number(1_234_567) == "1.2m"
    assert compact_number(2_500_000_000) == "2.5b"
    assert format_cache_summary(10_000, 8_750) == "87.5%/8.8k"
    assert format_cache_summary(0, 0) == "-"
    assert new_project()["base_url"] == ""
    assert new_project()["proxy_url"] == ""
    assert new_project()["test_prompt"] == ""
    multi_key_project = new_project("多密钥")
    multi_key_project["api_keys"] = [
        {"id": "key-a", "name": "A", "value": "sk-aaa"},
        {"id": "key-b", "name": "B", "value": "sk-bbb"},
    ]
    multi_key_project["base_url"] = "https://example.com"
    _sync_project_keys(multi_key_project)
    assert multi_key_project["api_key"] == "sk-aaa"
    assert client_from_project(multi_key_project, api_key_id="key-b").api_key == "sk-bbb"
    assert project_key_for_model(multi_key_project, {"id": "m", "api_key_id": "key-b"})["name"] == "B"
    assert api_key_label({"name": "B", "value": "sk-bbb"}) == "B · ...-bbb"
    assert parse_custom_headers("") == {}
    assert parse_custom_headers('{"X-Test": "ok", "X-Number": 1}') == {
        "X-Test": "ok",
        "X-Number": "1",
    }
    try:
        parse_custom_headers("[]")
    except ValueError:
        pass
    else:
        raise AssertionError("非对象请求头应被拒绝")
    assert normalize_base_url("https://example.com") == "https://example.com/v1"
    assert normalize_base_url("https://api.openai.com/v1/") == "https://api.openai.com/v1"
    assert normalize_base_url("https://host/v1/chat/completions") == "https://host/v1"
    assert normalize_base_url("https://host/v1/messages") == "https://host/v1"
    assert normalize_proxy_url("") == ""
    assert normalize_proxy_url("HTTP://127.0.0.1:8080/") == "http://127.0.0.1:8080"
    assert normalize_proxy_url("socks5h://user:pass@127.0.0.1:1080") == "socks5h://user:pass@127.0.0.1:1080"
    empty_ua_client = OpenAIClient("https://example.com", "", "chat", custom_headers={"User-Agent": ""})
    assert "User-Agent" not in empty_ua_client.headers

    class FakeProbeResponse:
        def __init__(self, on_done):
            self.ok = True
            self.encoding = "utf-8"
            self.on_done = on_done

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_lines(self, decode_unicode=True):
            yield "data: " + json.dumps({"choices": [{"delta": {"content": "现"}}]})
            self.on_done()
            yield "data: [DONE]"

    class FakeProbeErrorResponse:
        ok = False
        status_code = 500
        encoding = "utf-8"
        reason = "error"
        text = "upstream error"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def json(self):
            raise ValueError("not json")

        def iter_lines(self, decode_unicode=True):
            return iter(())

    class FakeProbeRequests:
        def __init__(self):
            self.reached_done = False

        def post(self, *args, **kwargs):
            model = (kwargs.get("json") or {}).get("model", "")
            if model == "fake-error":
                return FakeProbeErrorResponse()
            return FakeProbeResponse(lambda: setattr(self, "reached_done", True))

    probe_client = OpenAIClient("https://example.com", "", "chat")
    real_requests = client_module.requests
    client_module.requests = FakeProbeRequests()
    try:
        probe_result = probe_client.probe("fake-model")
        assert probe_result["ok"] and probe_result["reply"] == "可用（首 token 已返回）"
        assert not client_module.requests.reached_done
        error_result = probe_client.probe("fake-error")
        assert not error_result["ok"] and "upstream error" in error_result["error"]
    finally:
        client_module.requests = real_requests

    assert (
        RelayServer.upstream_headers(empty_ua_client, {"User-Agent": "caller-agent"}, True)["User-Agent"]
        == "caller-agent"
    )
    custom_ua_client = OpenAIClient("https://example.com", "", "chat", custom_headers={"User-Agent": "custom-agent"})
    assert custom_ua_client.headers["User-Agent"] == "custom-agent"
    assert extract_stream_text({"choices": [{"delta": {"content": "ok"}}]}, "chat") == "ok"
    assert extract_stream_text({"type": "response.output_text.delta", "delta": "ok"}, "responses") == "ok"
    assert (
        extract_stream_text(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}},
            "anthropic",
        )
        == "ok"
    )
    converted = _chat_to_anthropic({"model": "claude", "messages": [{"role": "user", "content": "hi"}]})
    assert converted["messages"][0]["content"] == [{"type": "text", "text": "hi"}]
    normalized = _normalize_response({"content": [{"type": "text", "text": "ok"}], "usage": {}}, "anthropic", "claude")
    assert normalized["text"] == "ok"
    responses_body = _chat_to_responses(
        {
            "model": "gpt-test",
            "messages": [{"role": "user", "content": "weather"}],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "get_weather", "description": "weather", "parameters": {"type": "object"}},
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
        }
    )
    assert responses_body["tools"][0]["name"] == "get_weather"
    assert "function" not in responses_body["tools"][0]
    assert responses_body["tool_choice"] == {"type": "function", "name": "get_weather"}
    chat_body = _normalize_response(
        {
            "id": "resp_1",
            "output": [{"type": "function_call", "call_id": "call_1", "name": "get_weather", "arguments": "{}"}],
        },
        "responses",
        "gpt-test",
    )
    assert chat_body["tool_calls"][0]["name"] == "get_weather"
    assert _request_mode("/v1/chat/completions") == "chat"
    assert _request_mode("/v1/responses") == "responses"
    assert _request_mode("/v1/messages") == "anthropic"
    responses_to_chat = _convert_request(
        {
            "model": "gpt-test",
            "instructions": "Be concise.",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "look"},
                        {"type": "input_image", "image_url": "data:image/png;base64,AA=="},
                    ],
                }
            ],
            "max_output_tokens": 64,
            "reasoning": {"effort": "low"},
            "tools": [{"type": "custom", "name": "shell", "description": "run", "parameters": {"type": "object"}}],
        },
        "responses",
        "chat",
    )
    assert responses_to_chat["messages"][0] == {"role": "system", "content": "Be concise."}
    assert responses_to_chat["messages"][1]["content"][1]["type"] == "image_url"
    assert responses_to_chat["max_tokens"] == 64
    assert responses_to_chat["reasoning_effort"] == "low"
    assert responses_to_chat["tools"][0]["type"] == "function"
    assert responses_to_chat["tools"][0]["function"]["name"] == "shell"
    reasoning_to_chat = _responses_to_chat(
        {
            "model": "gpt-test",
            "input": [
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "think"}]},
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "answer"}]},
            ],
        }
    )
    assert reasoning_to_chat["messages"][-1]["reasoning_content"] == "think"
    reasoning_to_responses = _chat_to_responses(
        {
            "model": "gpt-test",
            "messages": [{"role": "assistant", "content": "answer", "reasoning_content": "think"}],
        }
    )
    assert reasoning_to_responses["input"][0] == {
        "type": "reasoning",
        "summary": [{"type": "summary_text", "text": "think"}],
    }
    tools_to_chat = _responses_to_chat(
        {
            "model": "gpt-test",
            "input": [
                {"type": "function_call", "call_id": "call_1", "name": "one", "arguments": "{}"},
                {"type": "function_call", "call_id": "call_2", "name": "two", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call_1", "output": "one result"},
                {"type": "function_call_output", "call_id": "call_2", "output": "two result"},
            ],
        }
    )
    assert len(tools_to_chat["messages"]) == 3
    assert [call["id"] for call in tools_to_chat["messages"][0]["tool_calls"]] == ["call_1", "call_2"]
    assert [message["tool_call_id"] for message in tools_to_chat["messages"][1:]] == ["call_1", "call_2"]
    tools_to_anthropic = _chat_to_anthropic(
        {
            "model": "claude-test",
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "one", "arguments": "{}"}},
                        {"id": "call_2", "type": "function", "function": {"name": "two", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "one result"},
                {"role": "tool", "tool_call_id": "call_2", "content": "two result"},
            ],
        }
    )
    assert len(tools_to_anthropic["messages"]) == 2
    assert [part["tool_use_id"] for part in tools_to_anthropic["messages"][1]["content"]] == ["call_1", "call_2"]
    anthropic_to_chat = _convert_request(
        {
            "model": "claude-test",
            "max_tokens": 128,
            "system": [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}}
                    ],
                }
            ],
        },
        "anthropic",
        "chat",
    )
    assert anthropic_to_chat["messages"][0]["content"] == "A\nB"
    assert anthropic_to_chat["messages"][1]["content"][0]["image_url"]["url"] == "data:image/png;base64,AA=="
    same_mode = {"model": "gpt-test", "input": "hello", "tools": [{"type": "custom", "name": "shell"}]}
    assert _convert_request(same_mode, "responses", "responses") == same_mode
    derived_key = _derive_config_key("correct-pass")
    assert len(derived_key) == 32
    assert derived_key == hashlib.md5(("correct-pass" + HARD_CODED_AES_KEY).encode("utf-8")).hexdigest().encode("ascii")
    encrypted = encrypt_config({"version": 2, "projects": [new_project("加密测试")], "relay": {}}, "correct-pass")
    decrypted, encrypted_flag = decrypt_config(encrypted, "correct-pass", allow_legacy=False)
    assert encrypted_flag and decrypted["projects"][0]["name"] == "加密测试"
    try:
        decrypt_config(encrypted, "wrong-pass", allow_legacy=False)
    except ValueError:
        pass
    else:
        raise AssertionError("错误密码不应解密成功")
    shared = encrypt_config({"version": 2, "projects": [], "relay": {}}, "xiaoyao")
    shared_data, shared_flag = decrypt_config(shared, _derive_config_key("xiaoyao"), allow_legacy=False)
    assert shared_flag and shared_data["version"] == 2
    assert extract_usage_tokens(
        {"prompt_tokens": 10, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 4}},
        "chat",
    ) == (10, 5, 4)
    assert extract_usage_tokens(
        {"input_tokens": 8, "output_tokens": 2, "input_tokens_details": {"cached_tokens": 3}},
        "responses",
    ) == (8, 2, 3)
    assert extract_usage_tokens(
        {"input_tokens": 7, "output_tokens": 1, "cache_read_input_tokens": 6},
        "anthropic",
    ) == (7, 1, 6)
    collector = _SSEUsageCollector("chat")
    chunk = b'data: {"usage": {"prompt_tokens": 12, "completion_tokens": 3, "prompt_tokens_details": {"cached_tokens": 5}}}\n\n'
    collector.feed(chunk[:30])
    collector.feed(chunk[30:])
    assert collector.summary() == (12, 3, 5)
    responses_collector = _SSEUsageCollector("responses")
    responses_collector.feed(
        b'data: {"type":"response.completed","response":{"usage":{"input_tokens":20,"output_tokens":4,"input_tokens_details":{"cached_tokens":8}}}}\n\n'
    )
    assert responses_collector.summary() == (20, 4, 8)
    with tempfile.TemporaryDirectory() as temp_dir:
        stats = UsageStats(Path(temp_dir) / "usage.json")
        stats.record({"id": "p1", "name": "AI项目"}, "gpt-test", 100, 40, 30)
        snapshot = stats.snapshot()
        assert snapshot["input_tokens"] == 100
        assert snapshot["output_tokens"] == 40
        assert snapshot["cached_tokens"] == 30
        assert next(iter(snapshot["models"].values()))["model"] == "gpt-test"
        stats.save()
        loaded = UsageStats(Path(temp_dir) / "usage.json")
        assert loaded.snapshot()["input_tokens"] == 100
        stats.clear_today()
        cleared = stats.snapshot()
        assert cleared["input_tokens"] == 0
        assert cleared["output_tokens"] == 0
        assert cleared["cached_tokens"] == 0
        assert cleared["models"] == {}
        stats.record({"id": "p1", "name": "AI项目"}, "gpt-test", 20, 7, 3)
        after_clear_record = stats.snapshot()
        assert after_clear_record["requests"] == 1
        assert after_clear_record["input_tokens"] == 20
        assert after_clear_record["output_tokens"] == 7
        assert after_clear_record["cached_tokens"] == 3
        assert next(iter(after_clear_record["models"].values()))["model"] == "gpt-test"
        reloaded = UsageStats(Path(temp_dir) / "usage.json")
        assert reloaded.snapshot()["input_tokens"] == 20
    body = {"model": "gpt-test", "stream": True}
    assert _include_stream_usage(body, "chat")["stream_options"]["include_usage"] is True
    assert "stream_options" not in _include_stream_usage({"model": "claude", "stream": True}, "anthropic")
    print("self-test passed")
