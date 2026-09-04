import unittest
from unittest.mock import MagicMock, Mock, patch

from ai_probe.client import OpenAIClient
from ai_probe.projects import client_from_project, new_project


class SSLVerificationTests(unittest.TestCase):
    @patch("ai_probe.client.requests.get")
    def test_model_discovery_verifies_ssl_by_default(self, request_get):
        response = Mock(ok=True)
        response.json.return_value = {"data": []}
        request_get.return_value = response

        OpenAIClient("https://example.com", "key", "chat").list_models()

        self.assertIs(request_get.call_args.kwargs["verify"], True)

    @patch("ai_probe.client.requests.get")
    def test_model_discovery_can_skip_ssl_verification(self, request_get):
        response = Mock(ok=True)
        response.json.return_value = {"data": []}
        request_get.return_value = response

        OpenAIClient("https://example.com", "key", "chat", verify_ssl=False).list_models()

        self.assertIs(request_get.call_args.kwargs["verify"], False)

    @patch("ai_probe.client.requests.post", side_effect=RuntimeError("connection stopped"))
    def test_probe_uses_configured_ssl_verification(self, request_post):
        result = OpenAIClient("https://example.com", "key", "responses", verify_ssl=False).probe("model")

        self.assertFalse(result["ok"])
        self.assertIs(request_post.call_args.kwargs["verify"], False)

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

    def test_project_defaults_to_ssl_verification(self):
        project = new_project()

        self.assertIs(project["skip_ssl_verify"], False)

    @patch("ai_probe.projects.OpenAIClient")
    def test_project_setting_is_mapped_to_client(self, client_class):
        project = new_project()
        project["base_url"] = "https://example.com"
        project["skip_ssl_verify"] = True

        client_from_project(project)

        self.assertIs(client_class.call_args.kwargs["verify_ssl"], False)


if __name__ == "__main__":
    unittest.main()
