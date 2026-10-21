import unittest
from unittest.mock import MagicMock, Mock, patch

from ai_probe.client import VERIFY_SSL, OpenAIClient


class SSLVerificationTests(unittest.TestCase):
    def test_ssl_verification_is_disabled_globally(self):
        self.assertIs(VERIFY_SSL, False)

    @patch("ai_probe.client.requests.get")
    def test_model_discovery_skips_ssl_verification(self, request_get):
        response = Mock(ok=True)
        response.json.return_value = {"data": []}
        request_get.return_value = response

        OpenAIClient("https://example.com", "key", "chat").list_models()

        self.assertIs(request_get.call_args.kwargs["verify"], False)

    @patch("ai_probe.client.requests.post")
    def test_probe_skips_ssl_verification(self, request_post):
        response = MagicMock(ok=True)
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.iter_lines.return_value = iter(["data: [DONE]"])
        request_post.return_value = response

        OpenAIClient("https://example.com", "key", "responses").probe("model")

        self.assertIs(request_post.call_args.kwargs["verify"], False)

    @patch("ai_probe.client.requests.post")
    def test_probe_uses_small_output_limit_and_read_timeout(self, request_post):
        response = MagicMock(ok=True)
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.iter_lines.return_value = iter(["data: [DONE]"])
        request_post.return_value = response

        for api_mode, output_key in (
            ("chat", "max_tokens"),
            ("responses", "max_output_tokens"),
            ("anthropic", "max_tokens"),
        ):
            with self.subTest(api_mode=api_mode):
                client = OpenAIClient("https://example.com", "key", api_mode)
                self.assertIsNotNone(client.session)
                result = client.probe("model")

                self.assertTrue(result["ok"])
                body = request_post.call_args.kwargs["json"]
                self.assertEqual(body[output_key], 32)
                self.assertEqual(request_post.call_args.kwargs["timeout"], (8.0, 5.0))

    def test_probe_has_an_absolute_timeout(self):
        response = MagicMock(ok=True)
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.iter_lines.return_value = iter(['data: {"type":"response.output_text.delta","delta":"ok"}'])
        with (
            patch("ai_probe.client.PROBE_TIMEOUT", 1.0),
            patch("ai_probe.client.time.perf_counter", side_effect=[0.0, 1.1, 1.1]),
            patch("ai_probe.client.requests.post", return_value=response),
        ):
            result = OpenAIClient("https://example.com", "key", "responses").probe("model")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "不可用")
        self.assertEqual(result["error"], "测活超时（1s 未完成）")


if __name__ == "__main__":
    unittest.main()
