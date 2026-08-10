import unittest

from ai_probe.utils import (
    compact_number,
    extract_usage_tokens,
    format_cache_summary,
    normalize_base_url,
    normalize_proxy_url,
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
        self.assertEqual(
            extract_usage_tokens(
                {"input_tokens": 7, "output_tokens": 1, "cache_read_input_tokens": 6},
                "anthropic",
            ),
            (7, 1, 6),
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


if __name__ == "__main__":
    unittest.main()
