"""本地转发用量统计。"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

from .config import USAGE_SAVE_INTERVAL


class UsageStats:
    """Daily relay token ledger persisted as a small plain JSON file."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.data = self._load()
        self._last_save = 0.0

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _empty_day() -> dict:
        return {
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "models": {},
        }

    def _load(self) -> dict:
        default = {"version": 1, "days": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default
        if not isinstance(data, dict) or not isinstance(data.get("days"), dict):
            return default
        return data

    def record(
        self,
        project: dict,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
    ):
        input_tokens = max(0, int(input_tokens or 0))
        output_tokens = max(0, int(output_tokens or 0))
        cached_tokens = max(0, int(cached_tokens or 0))
        day_key = self._today()
        project_id = str(project.get("id", "") or "unknown")
        project_name = str(project.get("name", "") or "未命名项目")
        model = str(model or "")
        with self._lock:
            days = self.data.setdefault("days", {})
            self.data["days"] = {key: value for key, value in days.items() if key == day_key}
            day = self.data["days"].setdefault(day_key, self._empty_day())
            day["requests"] += 1
            day["input_tokens"] += input_tokens
            day["output_tokens"] += output_tokens
            day["cached_tokens"] += cached_tokens
            entry_key = f"{project_id}\u0001{model}"
            entry = day["models"].setdefault(
                entry_key,
                {
                    "project_id": project_id,
                    "project_name": project_name,
                    "model": model,
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_tokens": 0,
                },
            )
            entry["requests"] += 1
            entry["input_tokens"] += input_tokens
            entry["output_tokens"] += output_tokens
            entry["cached_tokens"] += cached_tokens
            now = time.time()
            if now - self._last_save >= USAGE_SAVE_INTERVAL:
                self._last_save = now
                self._write_locked()

    def snapshot(self) -> dict:
        with self._lock:
            day = self.data["days"].get(self._today(), self._empty_day())
            return json.loads(json.dumps(day))

    def clear_today(self):
        day_key = self._today()
        with self._lock:
            self.data.setdefault("days", {}).pop(day_key, None)
            # Force the first request after a reset to be persisted immediately.
            self._last_save = 0.0
            self._write_locked()

    def save(self):
        with self._lock:
            self._write_locked()

    def _write_locked(self):
        try:
            temp = self.path.with_name(f".{self.path.name}.tmp")
            temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.path)
        except OSError:
            pass
