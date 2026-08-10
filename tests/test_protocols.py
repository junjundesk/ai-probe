import unittest

from ai_probe.protocols import _chat_to_anthropic, _include_stream_usage, _request_mode, _responses_to_chat


class RequestModeTests(unittest.TestCase):
    def test_request_mode(self):
        self.assertEqual(_request_mode("/v1/chat/completions"), "chat")
        self.assertEqual(_request_mode("/v1/responses"), "responses")
        self.assertEqual(_request_mode("/v1/messages"), "anthropic")


class ConversionTests(unittest.TestCase):
    def test_chat_to_anthropic(self):
        converted = _chat_to_anthropic({"model": "claude", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(converted["messages"][0]["content"], [{"type": "text", "text": "hi"}])

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


class StreamUsageTests(unittest.TestCase):
    def test_chat_stream_requests_usage(self):
        body = {"model": "gpt-test", "stream": True}
        updated = _include_stream_usage(body, "chat")
        self.assertTrue(updated["stream_options"]["include_usage"])

    def test_non_chat_stream_keeps_body(self):
        body = {"model": "claude", "stream": True}
        self.assertEqual(_include_stream_usage(body, "anthropic"), body)


if __name__ == "__main__":
    unittest.main()
