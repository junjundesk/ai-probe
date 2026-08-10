"""上游 OpenAI 兼容 API 客户端。"""

from __future__ import annotations

import json
import time

try:
    import requests
except ImportError:
    requests = None

from .config import TEST_PROMPT
from .utils import (
    error_message_from_response,
    extract_stream_text,
    normalize_base_url,
    normalize_proxy_url,
    utc_timestamp,
)


class OpenAIClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_mode: str,
        test_prompt: str = "",
        custom_headers: dict[str, str] | None = None,
        proxy_url: str = "",
    ):
        if requests is None:
            raise RuntimeError("缺少 requests，请先运行：pip install -r requirements.txt")
        self.base_url = normalize_base_url(base_url)
        self.api_key = api_key.strip()
        self.api_mode = api_mode
        self.test_prompt = test_prompt.strip() or TEST_PROMPT
        self.custom_headers = custom_headers or {}
        self.proxy_url = normalize_proxy_url(proxy_url)
        self.proxies = {"http": self.proxy_url, "https": self.proxy_url} if self.proxy_url else None

    @property
    def headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.api_mode == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
            if self.api_key:
                headers["x-api-key"] = self.api_key
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        for name, value in self.custom_headers.items():
            if name.lower() == "user-agent" and not str(value).strip():
                continue
            headers[name] = value
        return headers

    def list_models(self) -> list[str]:
        response = requests.get(
            f"{self.base_url}/models",
            headers=self.headers,
            timeout=(10, 30),
            proxies=self.proxies,
        )
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status_code}: {error_message_from_response(response)}")
        try:
            payload = response.json()
        except ValueError as exc:
            content_type = response.headers.get("content-type", "未知类型")
            preview = (response.text or "").strip().replace("\n", " ")[:160]
            raise RuntimeError(f"模型列表不是 JSON（{content_type}）：{preview or '空响应'}") from exc
        if isinstance(payload, dict):
            items = payload.get("data", payload.get("models", []))
        elif isinstance(payload, list):
            items = payload
        else:
            items = []

        model_ids = []
        for item in items:
            if isinstance(item, str):
                model_ids.append(item)
            elif isinstance(item, dict):
                model_id = item.get("id") or item.get("name")
                if model_id:
                    model_ids.append(str(model_id))
        return sorted(set(model_ids), key=str.lower)

    def probe(self, model: str) -> dict:
        if self.api_mode == "responses":
            url = f"{self.base_url}/responses"
            body = {"model": model, "input": self.test_prompt, "stream": True}
        elif self.api_mode == "anthropic":
            url = f"{self.base_url}/messages"
            body = {
                "model": model,
                "max_tokens": 128,
                "messages": [{"role": "user", "content": self.test_prompt}],
                "stream": True,
            }
        else:
            url = f"{self.base_url}/chat/completions"
            body = {
                "model": model,
                "messages": [{"role": "user", "content": self.test_prompt}],
                "stream": True,
            }

        started = time.perf_counter()
        first_event_ms = None
        first_token_ms = None
        probe_reply = ""
        event_name = ""

        try:
            with requests.post(
                url,
                headers={**self.headers, "Accept": "text/event-stream"},
                json=body,
                stream=True,
                timeout=(10, 30),
                proxies=self.proxies,
            ) as response:
                if not response.ok:
                    raise RuntimeError(f"HTTP {response.status_code}: {error_message_from_response(response)}")
                response.encoding = "utf-8"
                for raw_line in response.iter_lines(decode_unicode=True):
                    if raw_line is None:
                        continue
                    line = raw_line.strip()
                    if not line:
                        event_name = ""
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                        continue
                    data = line[5:].strip() if line.startswith("data:") else line
                    if data == "[DONE]":
                        break
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, dict):
                        continue

                    elapsed_ms = round((time.perf_counter() - started) * 1000)
                    if first_event_ms is None:
                        first_event_ms = elapsed_ms

                    payload_type = str(payload.get("type", event_name))
                    if payload_type == "error" or payload.get("error"):
                        error = payload.get("error", payload)
                        if isinstance(error, dict):
                            error = error.get("message") or json.dumps(error, ensure_ascii=False)
                        raise RuntimeError(str(error))

                    text = extract_stream_text(payload, self.api_mode)
                    if text:
                        if first_token_ms is None:
                            first_token_ms = elapsed_ms
                        probe_reply = "可用（首 token 已返回）"
                        # 测活只验证可用性，收到首个有效 token 即结束流式读取。
                        break

            total_ms = round((time.perf_counter() - started) * 1000)
            return {
                "ok": True,
                "status": "可用",
                "first_ms": first_token_ms or first_event_ms or total_ms,
                "total_ms": total_ms,
                "reply": probe_reply or "已完成（未返回文本）",
                "error": "",
                "tested_at": utc_timestamp(),
            }
        except Exception as exc:
            return {
                "ok": False,
                "status": "不可用",
                "first_ms": None,
                "total_ms": round((time.perf_counter() - started) * 1000),
                "reply": "",
                "error": str(exc),
                "tested_at": utc_timestamp(),
            }
