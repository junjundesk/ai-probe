"""将规范流事件渲染为目标协议 SSE。"""

from __future__ import annotations

import json
import time
import uuid

from ..utils import extract_usage_tokens
from .conversion import _json_object


class _StreamRenderer:
    def __init__(self, target_mode: str, model: str, custom_tool_names: set[str] | None = None):
        self.target_mode = target_mode
        self.custom_tool_names = custom_tool_names or set()
        self.id = "relay"
        self.model = model
        self.created = int(time.time())
        self.started = False
        self.finished = False
        self.has_output = False
        self.stop_reason = "stop"
        self.text = ""
        self.tools = {}
        self.input_tokens = 0
        self.output_tokens = 0
        self.cached_tokens = 0
        self.response_sequence = 0
        self.response_message_id = f"msg_{uuid.uuid4().hex}"
        self.response_text_started = False
        self.response_text_output_index = None
        self.response_next_output_index = 0
        self.anthropic_text_index = None
        self.anthropic_reasoning_index = None
        self.anthropic_tool_indices = {}
        self.anthropic_next_index = 0

    @staticmethod
    def _sse(payload: dict, event_type: str = "") -> bytes:
        prefix = f"event: {event_type}\n" if event_type else ""
        return f"{prefix}data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()

    def _response_event(self, event_type: str, **values) -> bytes:
        payload = {"type": event_type, "sequence_number": self.response_sequence, **values}
        self.response_sequence += 1
        return self._sse(payload, event_type)

    def _ensure_started(self):
        if self.started:
            return []
        self.started = True
        if self.target_mode == "chat":
            chunk = {
                "id": self.id,
                "object": "chat.completion.chunk",
                "created": self.created,
                "model": self.model,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
            }
            return [self._sse(chunk)]
        if self.target_mode == "responses":
            response = {
                "id": self.id,
                "object": "response",
                "created_at": self.created,
                "status": "in_progress",
                "model": self.model,
                "output": [],
            }
            return [
                self._response_event("response.created", response=response),
                self._response_event("response.in_progress", response=response),
            ]
        message = {
            "id": self.id,
            "type": "message",
            "role": "assistant",
            "model": self.model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": self.input_tokens, "output_tokens": 0},
        }
        return [self._sse({"type": "message_start", "message": message}, "message_start")]

    def feed(self, event: dict):
        event_type = event.get("type")
        if event_type == "start":
            source_id = str(event.get("id") or self.id)
            if self.target_mode == "responses":
                if source_id.startswith("chatcmpl-"):
                    source_id = source_id.replace("chatcmpl-", "resp-", 1)
                elif not source_id.startswith("resp"):
                    source_id = f"resp_{source_id}"
            elif self.target_mode == "chat":
                if source_id.startswith("resp-"):
                    source_id = source_id.replace("resp-", "chatcmpl-", 1)
                elif source_id.startswith("resp_"):
                    source_id = source_id.replace("resp_", "chatcmpl-", 1)
            elif not source_id.startswith("msg_"):
                source_id = f"msg_{uuid.uuid4().hex[:24]}"
            self.id = source_id
            self.model = event.get("model") or self.model
            return self._ensure_started()
        if event_type == "usage":
            if "input_tokens" in event:
                self.input_tokens = max(self.input_tokens, event["input_tokens"])
            if "output_tokens" in event:
                self.output_tokens = max(self.output_tokens, event["output_tokens"])
            if "cached_tokens" in event:
                self.cached_tokens = max(self.cached_tokens, event["cached_tokens"])
            return []
        if event_type == "stop":
            self.stop_reason = event.get("reason") or self.stop_reason
            return []
        if event_type == "error":
            return self._error(event.get("message", "上游流式请求失败"))
        if event_type == "end":
            return self.finish()

        output = self._ensure_started()
        if event_type == "reasoning":
            reasoning = event.get("text", "")
            self.has_output = self.has_output or bool(reasoning)
            if self.target_mode == "chat":
                output.append(
                    self._sse(
                        {
                            "id": self.id,
                            "object": "chat.completion.chunk",
                            "created": self.created,
                            "model": self.model,
                            "choices": [{"index": 0, "delta": {"reasoning_content": reasoning}, "finish_reason": None}],
                        }
                    )
                )
            elif self.target_mode == "responses":
                output.append(self._response_event("response.reasoning_summary_text.delta", delta=reasoning))
            else:
                if self.anthropic_reasoning_index is None:
                    self.anthropic_reasoning_index = self.anthropic_next_index
                    self.anthropic_next_index += 1
                    output.append(
                        self._sse(
                            {
                                "type": "content_block_start",
                                "index": self.anthropic_reasoning_index,
                                "content_block": {"type": "thinking", "thinking": ""},
                            },
                            "content_block_start",
                        )
                    )
                output.append(
                    self._sse(
                        {
                            "type": "content_block_delta",
                            "index": self.anthropic_reasoning_index,
                            "delta": {"type": "thinking_delta", "thinking": reasoning},
                        },
                        "content_block_delta",
                    )
                )
        elif event_type == "text":
            text = event.get("text", "")
            self.text += text
            self.has_output = self.has_output or bool(text)
            if self.target_mode == "chat":
                chunk = {
                    "id": self.id,
                    "object": "chat.completion.chunk",
                    "created": self.created,
                    "model": self.model,
                    "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                }
                output.append(self._sse(chunk))
            elif self.target_mode == "responses":
                if not self.response_text_started:
                    self.response_text_started = True
                    self.response_text_output_index = self.response_next_output_index
                    self.response_next_output_index += 1
                    item = {
                        "id": self.response_message_id,
                        "type": "message",
                        "status": "in_progress",
                        "role": "assistant",
                        "content": [],
                    }
                    part = {"type": "output_text", "text": "", "annotations": []}
                    output.extend(
                        [
                            self._response_event(
                                "response.output_item.added", output_index=self.response_text_output_index, item=item
                            ),
                            self._response_event(
                                "response.content_part.added",
                                item_id=self.response_message_id,
                                output_index=self.response_text_output_index,
                                content_index=0,
                                part=part,
                            ),
                        ]
                    )
                output.append(
                    self._response_event(
                        "response.output_text.delta",
                        item_id=self.response_message_id,
                        output_index=self.response_text_output_index,
                        content_index=0,
                        delta=text,
                    )
                )
            else:
                if self.anthropic_text_index is None:
                    self.anthropic_text_index = self.anthropic_next_index
                    self.anthropic_next_index += 1
                    output.append(
                        self._sse(
                            {
                                "type": "content_block_start",
                                "index": self.anthropic_text_index,
                                "content_block": {"type": "text", "text": ""},
                            },
                            "content_block_start",
                        )
                    )
                output.append(
                    self._sse(
                        {
                            "type": "content_block_delta",
                            "index": self.anthropic_text_index,
                            "delta": {"type": "text_delta", "text": text},
                        },
                        "content_block_delta",
                    )
                )
        elif event_type == "tool_start":
            index = int(event.get("index", 0))
            self.has_output = True
            tool = self.tools.setdefault(
                index, {"id": event.get("id", ""), "name": event.get("name", ""), "arguments": ""}
            )
            tool["id"] = event.get("id") or tool["id"]
            tool["name"] = event.get("name") or tool["name"]
            tool["type"] = "custom" if tool["name"] in self.custom_tool_names else "function"
            if tool.get("emitted"):
                return output
            tool["emitted"] = True
            if self.target_mode == "chat":
                delta = {
                    "tool_calls": [
                        {
                            "index": index,
                            "id": tool["id"],
                            "type": "function",
                            "function": {"name": tool["name"], "arguments": ""},
                        }
                    ]
                }
                output.append(
                    self._sse(
                        {
                            "id": self.id,
                            "object": "chat.completion.chunk",
                            "created": self.created,
                            "model": self.model,
                            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                        }
                    )
                )
            elif self.target_mode == "responses":
                if tool["type"] == "custom":
                    item = {
                        "id": f"ctc_{uuid.uuid4().hex}",
                        "type": "custom_tool_call",
                        "status": "in_progress",
                        "call_id": tool["id"],
                        "name": tool["name"],
                        "input": "",
                    }
                else:
                    item = {
                        "id": f"fc_{uuid.uuid4().hex}",
                        "type": "function_call",
                        "status": "in_progress",
                        "call_id": tool["id"],
                        "name": tool["name"],
                        "arguments": "",
                    }
                tool["item"] = item
                tool["output_index"] = self.response_next_output_index
                self.response_next_output_index += 1
                output.append(
                    self._response_event("response.output_item.added", output_index=tool["output_index"], item=item)
                )
            else:
                block_index = self.anthropic_next_index
                self.anthropic_next_index += 1
                self.anthropic_tool_indices[index] = block_index
                output.append(
                    self._sse(
                        {
                            "type": "content_block_start",
                            "index": block_index,
                            "content_block": {"type": "tool_use", "id": tool["id"], "name": tool["name"], "input": {}},
                        },
                        "content_block_start",
                    )
                )
        elif event_type == "tool_args":
            index = int(event.get("index", 0))
            if index not in self.tools:
                output.extend(self.feed({"type": "tool_start", "index": index, "id": "", "name": ""}))
            tool = self.tools[index]
            arguments = event.get("arguments", "")
            tool["arguments"] += arguments
            self.has_output = self.has_output or bool(arguments)
            if self.target_mode == "chat":
                delta = {"tool_calls": [{"index": index, "function": {"arguments": arguments}}]}
                output.append(
                    self._sse(
                        {
                            "id": self.id,
                            "object": "chat.completion.chunk",
                            "created": self.created,
                            "model": self.model,
                            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                        }
                    )
                )
            elif self.target_mode == "responses":
                if tool.get("type") != "custom":
                    output.append(
                        self._response_event(
                            "response.function_call_arguments.delta",
                            item_id=tool["item"]["id"],
                            output_index=tool["output_index"],
                            delta=arguments,
                        )
                    )
            else:
                output.append(
                    self._sse(
                        {
                            "type": "content_block_delta",
                            "index": self.anthropic_tool_indices[index],
                            "delta": {"type": "input_json_delta", "partial_json": arguments},
                        },
                        "content_block_delta",
                    )
                )
        return output

    def _error(self, message: str):
        self.finished = True
        if self.target_mode == "chat":
            return [self._sse({"error": {"message": message, "type": "upstream_error"}}), b"data: [DONE]\n\n"]
        payload = {"type": "error", "error": {"type": "api_error", "message": message}}
        return [self._sse(payload, "error")]

    def finish(self):
        if self.finished:
            return []
        self.finished = True
        output = self._ensure_started()
        if self.target_mode == "chat":
            reason = "tool_calls" if self.tools else "stop"
            chunk = {
                "id": self.id,
                "object": "chat.completion.chunk",
                "created": self.created,
                "model": self.model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
                "usage": {
                    "prompt_tokens": self.input_tokens,
                    "completion_tokens": self.output_tokens,
                    "total_tokens": self.input_tokens + self.output_tokens,
                },
            }
            output.extend([self._sse(chunk), b"data: [DONE]\n\n"])
        elif self.target_mode == "responses":
            response_output = []
            if self.response_text_started:
                part = {"type": "output_text", "text": self.text, "annotations": []}
                item = {
                    "id": self.response_message_id,
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [part],
                }
                output.extend(
                    [
                        self._response_event(
                            "response.output_text.done",
                            item_id=self.response_message_id,
                            output_index=self.response_text_output_index,
                            content_index=0,
                            text=self.text,
                        ),
                        self._response_event(
                            "response.content_part.done",
                            item_id=self.response_message_id,
                            output_index=self.response_text_output_index,
                            content_index=0,
                            part=part,
                        ),
                        self._response_event(
                            "response.output_item.done", output_index=self.response_text_output_index, item=item
                        ),
                    ]
                )
                response_output.append(item)
            for _index, tool in sorted(self.tools.items()):
                if tool.get("type") == "custom":
                    parsed = _json_object(tool["arguments"])
                    custom_input = parsed.get("input", tool["arguments"])
                    item = {**tool["item"], "status": "completed", "input": custom_input}
                    output.extend(
                        [
                            self._response_event(
                                "response.custom_tool_call_input.done",
                                item_id=item["id"],
                                output_index=tool["output_index"],
                                input=custom_input,
                            ),
                            self._response_event(
                                "response.output_item.done", output_index=tool["output_index"], item=item
                            ),
                        ]
                    )
                else:
                    item = {**tool["item"], "status": "completed", "arguments": tool["arguments"]}
                    output.extend(
                        [
                            self._response_event(
                                "response.function_call_arguments.done",
                                item_id=item["id"],
                                output_index=tool["output_index"],
                                name=tool["name"],
                                arguments=tool["arguments"],
                            ),
                            self._response_event(
                                "response.output_item.done", output_index=tool["output_index"], item=item
                            ),
                        ]
                    )
                response_output.append(item)
            incomplete = self.stop_reason in {"length", "max_tokens", "incomplete", "content_filter"}
            response = {
                "id": self.id,
                "object": "response",
                "created_at": self.created,
                "status": "incomplete" if incomplete else "completed",
                "incomplete_details": {
                    "reason": "content_filter" if self.stop_reason == "content_filter" else "max_output_tokens"
                }
                if incomplete
                else None,
                "model": self.model,
                "output": response_output,
                "usage": {
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                    "total_tokens": self.input_tokens + self.output_tokens,
                },
            }
            output.append(self._response_event("response.completed", response=response))
        else:
            if self.anthropic_reasoning_index is not None:
                output.append(
                    self._sse(
                        {"type": "content_block_stop", "index": self.anthropic_reasoning_index}, "content_block_stop"
                    )
                )
            if self.anthropic_text_index is not None:
                output.append(
                    self._sse({"type": "content_block_stop", "index": self.anthropic_text_index}, "content_block_stop")
                )
            for index in sorted(self.anthropic_tool_indices.values()):
                output.append(self._sse({"type": "content_block_stop", "index": index}, "content_block_stop"))
            reason = (
                "tool_use"
                if self.tools
                else "max_tokens"
                if self.stop_reason in {"length", "max_tokens", "incomplete"}
                else "end_turn"
            )
            output.extend(
                [
                    self._sse(
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": reason, "stop_sequence": None},
                            "usage": {"output_tokens": self.output_tokens},
                        },
                        "message_delta",
                    ),
                    self._sse({"type": "message_stop"}, "message_stop"),
                ]
            )
        return output


class _SSEUsageCollector:
    """Extract token usage from a passthrough SSE byte stream without buffering it."""

    def __init__(self, api_mode: str):
        self.api_mode = api_mode
        self.buffer = bytearray()
        self.input_tokens = 0
        self.output_tokens = 0
        self.cached_tokens = 0

    def feed(self, chunk: bytes):
        self.buffer.extend(chunk)
        while True:
            index = self.buffer.find(b"\n")
            if index < 0:
                break
            line = bytes(self.buffer[:index])
            del self.buffer[: index + 1]
            self._handle_line(line)

    def finish(self):
        if self.buffer:
            self._handle_line(bytes(self.buffer))
            self.buffer.clear()

    def summary(self) -> tuple[int, int, int]:
        return self.input_tokens, self.output_tokens, self.cached_tokens

    def _handle_line(self, raw_line: bytes):
        line = raw_line.rstrip(b"\r")
        if not line.startswith(b"data:"):
            return
        data = line[5:].strip()
        if not data or data == b"[DONE]":
            return
        try:
            payload = json.loads(data.decode("utf-8", "replace"))
        except ValueError:
            return
        if not isinstance(payload, dict):
            return
        usage = payload.get("usage")
        if self.api_mode == "responses" and not isinstance(usage, dict):
            response_data = payload.get("response")
            usage = response_data.get("usage") if isinstance(response_data, dict) else None
        elif self.api_mode == "anthropic":
            if payload.get("type") == "message_start":
                usage = (payload.get("message") or {}).get("usage") if not isinstance(usage, dict) else usage
            elif payload.get("type") != "message_delta":
                usage = None
        if not isinstance(usage, dict):
            return
        input_tokens, output_tokens, cached_tokens = extract_usage_tokens(usage, self.api_mode)
        self.input_tokens = max(self.input_tokens, input_tokens)
        self.output_tokens = max(self.output_tokens, output_tokens)
        self.cached_tokens = max(self.cached_tokens, cached_tokens)
