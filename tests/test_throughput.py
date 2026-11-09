import threading
import unittest
from io import BytesIO

from ai_probe.protocols.rendering import _sse_delta_text, _SSEUsageCollector
from ai_probe.relay import RelayServer
from ai_probe.throughput import ThroughputMeter, estimate_tokens
from ai_probe.usage import UsageStats


class FakeClock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class EstimateTests(unittest.TestCase):
    def test_estimate_mixes_ascii_and_cjk(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("abcd" * 3), 3)
        self.assertAlmostEqual(estimate_tokens("你好"), 1.4)


class ThroughputMeterTests(unittest.TestCase):
    def test_window_rate_active_count_and_expiry(self):
        clock = FakeClock()
        meter = ThroughputMeter(window=2.0, clock=clock)
        probe = meter.start(("p", "m"))
        self.assertEqual(meter.snapshot()["active"], 1)
        probe.add_text("a" * 40)  # 10 tokens
        clock.now += 1
        probe.add_text("a" * 40)
        snapshot = meter.snapshot()
        self.assertAlmostEqual(snapshot["tokens_per_second"], 10.0)
        self.assertAlmostEqual(snapshot["per_key"][("p", "m")], 10.0)
        clock.now += 1.5
        self.assertAlmostEqual(meter.snapshot()["tokens_per_second"], 5.0)
        clock.now += 1.0
        speed_ms = probe.finish(50)
        # 首个内容在 t=100，结束于 t=103.5。
        self.assertEqual(speed_ms, 3500)
        snapshot = meter.snapshot()
        self.assertEqual(snapshot["active"], 0)
        self.assertEqual(snapshot["per_key"], {})
        self.assertAlmostEqual(snapshot["last"]["tokens_per_second"], 50 / 3.5)
        self.assertEqual(probe.finish(50), 0)
        meter.clear_last()
        self.assertIsNone(meter.snapshot()["last"])

    def test_short_or_empty_streams_are_not_measured(self):
        clock = FakeClock()
        meter = ThroughputMeter(clock=clock)
        probe = meter.start(("p", "m"))
        probe.add_text("hello")
        clock.now += 0.1
        self.assertEqual(probe.finish(10), 0)
        empty = meter.start(("p", "m"))
        clock.now += 5
        self.assertEqual(empty.finish(0), 0)
        self.assertEqual(meter.snapshot()["active"], 0)
        self.assertIsNone(meter.snapshot()["last"])

    def test_hidden_reasoning_falls_back_to_first_chunk(self):
        clock = FakeClock()
        meter = ThroughputMeter(clock=clock)
        probe = meter.start(("p", "m"))
        probe.mark_chunk()
        clock.now += 4
        probe.add_text("abcd")
        clock.now += 1
        self.assertEqual(probe.finish(500), 5000)
        visible = meter.start(("p", "m"))
        visible.mark_chunk()
        clock.now += 4
        visible.add_text("abcd" * 10)
        clock.now += 1
        self.assertEqual(visible.finish(12), 1000)


class UsageSpeedTests(unittest.TestCase):
    def test_speed_is_accumulated_only_for_measured_requests(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            stats = UsageStats(Path(directory) / "usage.json")
            project = {"id": "p", "name": "P"}
            stats.record(project, "m", 10, 100, 0, 2000)
            stats.record(project, "m", 10, 50, 0)
            stats.record(project, "m", 10, 100, 0, 500)
            snapshot = stats.snapshot()
            row = snapshot["models"]["p\u0001m"]
            self.assertEqual(row["speed_output_tokens"], 200)
            self.assertEqual(row["speed_ms"], 2500)
            self.assertAlmostEqual(UsageStats.average_speed(row), 80.0)
            self.assertAlmostEqual(UsageStats.average_speed(snapshot), 80.0)
            self.assertIsNone(UsageStats.average_speed({"speed_ms": 0}))
            self.assertIsNone(UsageStats.average_speed({}))


class DeltaTextTests(unittest.TestCase):
    def test_extracts_text_reasoning_and_tool_args_per_protocol(self):
        self.assertEqual(
            _sse_delta_text(
                {
                    "choices": [
                        {
                            "delta": {
                                "reasoning_content": "r",
                                "content": "c",
                                "tool_calls": [{"function": {"arguments": "{}"}}],
                            }
                        }
                    ]
                },
                "chat",
            ),
            "cr{}",
        )
        self.assertEqual(_sse_delta_text({"type": "response.output_text.delta", "delta": "hi"}, "responses"), "hi")
        self.assertEqual(_sse_delta_text({"type": "response.completed", "response": {}}, "responses"), "")
        self.assertEqual(
            _sse_delta_text(
                {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "t"}}, "anthropic"
            ),
            "t",
        )
        self.assertEqual(_sse_delta_text({"type": "message_delta", "delta": {}}, "anthropic"), "")

    def test_collector_reports_text_to_callback(self):
        seen = []
        collector = _SSEUsageCollector("chat", on_text=seen.append)
        chunk = b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
        collector.feed(chunk[:10])
        collector.feed(chunk[10:])
        self.assertEqual(seen, ["hello"])


class _Response:
    status_code = 200

    def __init__(self, chunks, clock, step):
        self.chunks = chunks
        self.clock = clock
        self.step = step
        self.headers = {"Content-Type": "text/event-stream"}

    def iter_content(self, chunk_size=8192):
        for chunk in self.chunks:
            self.clock.now += self.step
            yield chunk

    def iter_lines(self, chunk_size=1):
        for chunk in self.chunks:
            self.clock.now += self.step
            yield from chunk.split(b"\n")

    def close(self):
        pass


class _Handler:
    def __init__(self):
        self.wfile = BytesIO()
        self.close_connection = False
        self._relay_request_context = {}

    def send_response(self, status):
        pass

    def send_header(self, name, value):
        pass

    def end_headers(self):
        pass


class _App:
    def __init__(self):
        self.records = []

    def record_relay_usage(self, *args):
        self.records.append(args)

    def _post(self, callback, *args):
        callback(*args)

    def _log(self, message):
        pass


CHAT_CHUNKS = [
    b'data: {"id":"chatcmpl-1","model":"m","choices":[{"delta":{"content":"hello world"},"finish_reason":null}]}\n\n',
    b'data: {"id":"chatcmpl-1","model":"m","choices":[{"delta":{"content":" again"},"finish_reason":null}]}\n\n',
    b'data: {"id":"chatcmpl-1","model":"m","choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
    b'data: {"id":"chatcmpl-1","model":"m","choices":[],"usage":{"prompt_tokens":5,"completion_tokens":40}}\n\n',
    b"data: [DONE]\n\n",
]


class RelaySpeedTests(unittest.TestCase):
    def _server(self):
        app = _App()
        server = RelayServer(app, "127.0.0.1", 0)
        clock = FakeClock()
        server.throughput = ThroughputMeter(clock=clock)
        return app, server, clock

    def test_passthrough_stream_records_speed(self):
        app, server, clock = self._server()
        handler = _Handler()
        RelayServer.passthrough_stream(
            app, handler, _Response(CHAT_CHUNKS, clock, 0.5), "chat", {"id": "p"}, "m", server=server
        )
        self.assertEqual(len(app.records), 1)
        record = app.records[0]
        self.assertEqual(record[2:5], (5, 40, 0))
        self.assertEqual(record[5], 2000)
        self.assertEqual(handler._relay_request_context["output_tokens_per_second"], 20.0)
        self.assertEqual(server.throughput.snapshot()["active"], 0)

    def test_converted_stream_records_speed(self):
        app, server, clock = self._server()
        handler = _Handler()
        server.write_upstream(
            handler, _Response(CHAT_CHUNKS, clock, 0.5), "chat", "responses", "m", {"id": "p"}, True, True
        )
        self.assertEqual(len(app.records), 1)
        self.assertGreater(app.records[0][5], 0)
        self.assertEqual(server.throughput.snapshot()["active"], 0)

    def test_non_stream_passthrough_has_no_speed(self):
        app, server, clock = self._server()

        class JsonResponse:
            status_code = 200
            headers = {"Content-Type": "application/json"}

            def iter_content(self, chunk_size=65536):
                yield b'{"usage":{"prompt_tokens":3,"completion_tokens":4}}'

            def close(self):
                pass

        RelayServer.passthrough_json(app, _Handler(), JsonResponse(), "chat", {"id": "p"}, "m", server=server)
        self.assertEqual(app.records, [({"id": "p"}, "m", 3, 4, 0)])

    def test_meter_is_thread_safe(self):
        meter = ThroughputMeter()

        def work():
            for _ in range(200):
                probe = meter.start(("p", "m"))
                probe.add_text("abcd")
                probe.finish(1)

        threads = [threading.Thread(target=work) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(meter.snapshot()["active"], 0)


if __name__ == "__main__":
    unittest.main()
