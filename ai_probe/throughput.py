"""本地中转实时输出速度统计。"""

from __future__ import annotations

import threading
import time
from collections import deque

# 速度至少基于这么长的生成时长才有意义，过短的请求会给出夸张的数值。
MIN_SPEED_DURATION = 0.2
# 上游实际输出远多于可见文本时（隐藏推理），改从首个上游分片计时。
HIDDEN_REASONING_RATIO = 3.0


def estimate_tokens(text) -> float:
    """粗略估算文本 Token 数：ASCII 约 4 字符 1 Token，其他字符约 0.7 Token。"""
    if not text:
        return 0.0
    text = str(text)
    ascii_count = sum(1 for char in text if ord(char) < 128)
    return ascii_count / 4 + (len(text) - ascii_count) * 0.7


class StreamProbe:
    """跟踪一个流式请求的输出节奏。"""

    def __init__(self, meter: ThroughputMeter, key: tuple[str, str]):
        self.meter = meter
        self.key = key
        self.started = meter.clock()
        self.first_chunk: float | None = None
        self.first_text: float | None = None
        self.last_text: float | None = None
        self.estimated = 0.0
        self.finished = False

    def mark_chunk(self):
        if self.first_chunk is None:
            self.first_chunk = self.meter.clock()

    def add_text(self, text):
        tokens = estimate_tokens(text)
        if tokens <= 0:
            return
        now = self.meter.clock()
        if self.first_chunk is None:
            self.first_chunk = now
        if self.first_text is None:
            self.first_text = now
        self.last_text = now
        self.estimated += tokens
        self.meter._push(self.key, now, tokens)

    def finish(self, actual_output_tokens=0) -> int:
        """结束计时并返回用于速度统计的毫秒数；0 表示该请求不可测速。"""
        if self.finished:
            return 0
        self.finished = True
        end = self.meter.clock()
        actual = max(0, int(actual_output_tokens or 0))
        tokens = actual if actual > 0 else self.estimated
        start = self.first_text
        if start is None or (self.estimated > 0 and actual > self.estimated * HIDDEN_REASONING_RATIO):
            start = self.first_chunk if self.first_chunk is not None else start
        if start is None and actual > 0:
            start = self.started
        duration = end - start if start is not None else 0.0
        speed_ms = int(duration * 1000) if tokens > 0 and duration >= MIN_SPEED_DURATION else 0
        self.meter._finish(self, tokens, speed_ms)
        return speed_ms


class ThroughputMeter:
    """统计最近窗口内的估算输出速度，线程安全。"""

    def __init__(self, window: float = 2.0, clock=time.monotonic):
        self.window = float(window)
        self.clock = clock
        self._lock = threading.Lock()
        self._events: deque[tuple[float, tuple[str, str], float]] = deque()
        self._active: dict[int, tuple[str, str]] = {}
        self.last: dict | None = None

    def start(self, key) -> StreamProbe:
        key = (str(key[0] or ""), str(key[1] or ""))
        probe = StreamProbe(self, key)
        with self._lock:
            self._active[id(probe)] = key
        return probe

    def _push(self, key, now: float, tokens: float):
        with self._lock:
            self._events.append((now, key, tokens))
            self._trim(now)

    def _finish(self, probe: StreamProbe, tokens: float, speed_ms: int):
        with self._lock:
            self._active.pop(id(probe), None)
            if speed_ms > 0:
                self.last = {
                    "key": probe.key,
                    "tokens_per_second": tokens / (speed_ms / 1000),
                }

    def _trim(self, now: float):
        limit = now - self.window
        while self._events and self._events[0][0] < limit:
            self._events.popleft()

    def clear_last(self):
        with self._lock:
            self.last = None

    def snapshot(self) -> dict:
        now = self.clock()
        with self._lock:
            self._trim(now)
            per_key: dict[tuple[str, str], float] = {}
            total = 0.0
            for _, key, tokens in self._events:
                per_key[key] = per_key.get(key, 0.0) + tokens
                total += tokens
            active_keys = set(self._active.values())
            return {
                "active": len(self._active),
                "tokens_per_second": total / self.window,
                "per_key": {key: value / self.window for key, value in per_key.items() if key in active_keys},
                "last": dict(self.last) if self.last else None,
            }
