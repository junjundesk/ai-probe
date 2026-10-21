import json
import unittest

from ai_probe.utils import (
    compact_number,
    error_message_from_response,
    extract_usage_tokens,
    format_cache_summary,
    normalize_base_url,
    normalize_proxy_url,
    parse_channel_import,
    parse_custom_headers,
)


class CompactNumberTests(unittest.TestCase):
    def test_compact_number_units(self):
        self.assertEqual(compact_number(0), "0")
        self.assertEqual(compact_number(999), "999")
        self.assertEqual(compact_number(1_000), "1k")
        self.assertEqual(compact_number(12_345), "12.3k")
        self.assertEqual(compact_number(999_999), "1m")
        self.assertEqual(compact_number(1_234_567), "1.2m")
        self.assertEqual(compact_number(2_500_000_000), "2.5b")

    def test_format_cache_summary(self):
        self.assertEqual(format_cache_summary(10_000, 8_750), "87.5%/8.8k")
        self.assertEqual(format_cache_summary(0, 0), "-")


class FakeErrorResponse:
    """模拟 requests.Response：content 是原始字节，text 按 ISO-8859-1 解码。"""

    def __init__(self, content: bytes, text: str = "", reason: str = "Bad Request"):
        self.content = content
        self.text = text
        self.reason = reason

    def json(self):
        raise ValueError("not json")


class ErrorMessageTests(unittest.TestCase):
    def test_decodes_utf8_json_body_without_charset(self):
        body = json.dumps({"error": {"message": "分组下模型无可用渠道"}}, ensure_ascii=False).encode("utf-8")
        response = FakeErrorResponse(body, text=body.decode("iso-8859-1"))

        self.assertEqual(error_message_from_response(response), "分组下模型无可用渠道")

    def test_extracts_message_from_sse_error_frame(self):
        payload = {"type": "error", "error": {"type": "api_error", "message": "固定商家当前不可用。request_id: 123"}}
        body = f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
        response = FakeErrorResponse(body, text=body.decode("iso-8859-1"))

        self.assertEqual(error_message_from_response(response), "固定商家当前不可用。request_id: 123")

    def test_falls_back_to_reason_when_body_is_empty(self):
        response = FakeErrorResponse(b"", reason="Bad Gateway")

        self.assertEqual(error_message_from_response(response), "Bad Gateway")


class NormalizationTests(unittest.TestCase):
    def test_normalize_base_url(self):
        self.assertEqual(normalize_base_url("https://example.com"), "https://example.com/v1")
        self.assertEqual(normalize_base_url("https://api.openai.com/v1/"), "https://api.openai.com/v1")
        self.assertEqual(normalize_base_url("https://host/v1/chat/completions"), "https://host/v1")

    def test_normalize_base_url_rejects_invalid_value(self):
        with self.assertRaises(ValueError):
            normalize_base_url("not-a-url")

    def test_normalize_proxy_url(self):
        self.assertEqual(normalize_proxy_url(""), "")
        self.assertEqual(normalize_proxy_url("proxy.example.com"), "http://proxy.example.com")
        self.assertEqual(normalize_proxy_url("proxy.example.com:8080"), "http://proxy.example.com:8080")
        self.assertEqual(normalize_proxy_url("HTTP://127.0.0.1:8080/"), "http://127.0.0.1:8080")
        with self.assertRaises(ValueError):
            normalize_proxy_url("http://host/path")


class UsageExtractionTests(unittest.TestCase):
    def test_extract_usage_tokens_for_chat(self):
        self.assertEqual(
            extract_usage_tokens(
                {"prompt_tokens": 10, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 4}},
                "chat",
            ),
            (10, 5, 4),
        )

    def test_extract_usage_tokens_for_responses(self):
        self.assertEqual(
            extract_usage_tokens(
                {"input_tokens": 8, "output_tokens": 2, "input_tokens_details": {"cached_tokens": 3}},
                "responses",
            ),
            (8, 2, 3),
        )

    def test_extract_usage_tokens_for_anthropic(self):
        # cache_read must be folded into input so ratio ≤ 100%
        self.assertEqual(
            extract_usage_tokens(
                {"input_tokens": 7, "output_tokens": 1, "cache_read_input_tokens": 6},
                "anthropic",
            ),
            (13, 1, 6),
        )

    def test_extract_usage_tokens_for_anthropic_with_cache_creation(self):
        # cache_creation_input_tokens must also count toward the prompt total
        self.assertEqual(
            extract_usage_tokens(
                {
                    "input_tokens": 4,
                    "output_tokens": 3,
                    "cache_creation_input_tokens": 20183,
                    "cache_read_input_tokens": 5360,
                },
                "anthropic",
            ),
            (25547, 3, 5360),
        )

    def test_extract_usage_tokens_cached_clamp(self):
        # if cached > prompt_tokens (broken upstream), clamp input up to cached
        self.assertEqual(
            extract_usage_tokens(
                {"prompt_tokens": 10, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 40}},
                "chat",
            ),
            (40, 5, 40),
        )


class CustomHeaderTests(unittest.TestCase):
    def test_parse_custom_headers(self):
        self.assertEqual(parse_custom_headers(""), {})
        self.assertEqual(
            parse_custom_headers('{"X-Test": "ok", "X-Number": 1}'),
            {"X-Test": "ok", "X-Number": "1"},
        )

    def test_parse_custom_headers_rejects_non_object(self):
        with self.assertRaises(ValueError):
            parse_custom_headers("[]")


class ChannelImportTests(unittest.TestCase):
    def test_parse_newapi_json(self):
        channels = parse_channel_import('{"_type":"newapi_channel_conn","key":"sk-test","url":"https://example.com"}')
        self.assertEqual(
            channels, [{"url": "https://example.com/v1", "key": "sk-test", "_type": "newapi_channel_conn"}]
        )

    def test_parse_escaped_newapi_json_with_markdown_url(self):
        channels = parse_channel_import(
            r'{"\_type":"newapi\_channel\_conn","key":"sk-test","url":"[https://example.com](https://example.com)"}'
        )
        self.assertEqual(
            channels, [{"url": "https://example.com/v1", "key": "sk-test", "_type": "newapi_channel_conn"}]
        )

    def test_parse_markdown_and_key_lines(self):
        channels = parse_channel_import("[https://example.com](https://example.com)\nsk-test")
        self.assertEqual(channels, [{"url": "https://example.com/v1", "key": "sk-test"}])

    def test_parse_multiple_url_key_lines(self):
        channels = parse_channel_import("https://one.example\nsk-one\nhttps://two.example/v1\nsk-two")
        self.assertEqual(
            channels,
            [
                {"url": "https://one.example/v1", "key": "sk-one"},
                {"url": "https://two.example/v1", "key": "sk-two"},
            ],
        )

    def test_parse_repeated_url_with_different_keys(self):
        channels = parse_channel_import("https://example.com\nsk-one\nhttps://example.com\nsk-two")
        self.assertEqual(
            channels,
            [
                {"url": "https://example.com/v1", "key": "sk-one"},
                {"url": "https://example.com/v1", "key": "sk-two"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
