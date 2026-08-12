"""上游响应归一化与流事件转换。"""

from __future__ import annotations

import json
import time
import uuid

from ..utils import _int_field, extract_usage_tokens
from .conversion import _content_text, _json_object


def _include_stream_usage(body: dict, api_mode: str) -> dict:
    if api_mode != "chat" or not body.get("stream"):
        return body
    stream_options = body.get("stream_options")
    if not isinstance(stream_options, dict):
        stream_options = {}
        body["stream_options"] = stream_options
    stream_options.setdefault("include_usage", True)
    return body


def _normalize_response(payload: dict, api_mode: str, model: str, custom_tool_names: set[str] | None = None) -> dict:
    custom_tool_names = custom_tool_names or set()
    result = {
        "id": payload.get("id", "relay"),
        "model": payload.get("model", model),
        "created": payload.get("created_at", payload.get("created", int(time.time()))),
        "text": "",
        "reasoning": "",
        "tool_calls": [],
        "usage": {},
        "finish_reason": None,
    }
    if api_mode == "chat":
        choices = payload.get("choices") or []
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        message = (choice.get("message") or {}) if isinstance(choice, dict) else {}
        result["finish_reason"] = choice.get("finish_reason") if isinstance(choice, dict) else None
        result["text"] = _content_text(message.get("content"))
        result["reasoning"] = message.get("reasoning_content") or message.get("reasoning") or ""
        for call in message.get("tool_calls") or []:
            function = call.get("function", {}) if isinstance(call, dict) else {}
            call_name = function.get("name", "")
            result["tool_calls"].append(
                {
                    "id": call.get("id", ""),
                    "name": call_name,
                    "arguments": function.get("arguments", "{}"),
                    "type": "custom" if call_name in custom_tool_names else "function",
                }
            )
        usage = payload.get("usage") or {}
        input_tokens, output_tokens, cached_tokens = extract_usage_tokens(usage, api_mode)
        details = usage.get("completion_tokens_details") if isinstance(usage, dict) else None
        result["usage"] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cached_tokens": cached_tokens,
            "reasoning_tokens": _int_field(details, "reasoning_tokens") if isinstance(details, dict) else 0,
        }
    elif api_mode == "anthropic":
        texts = []
        reasoning = []
        for part in payload.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                texts.append(part.get("text", ""))
            elif part.get("type") == "thinking":
                reasoning.append(part.get("thinking", ""))
            elif part.get("type") == "tool_use":
                call_name = part.get("name", "")
                result["tool_calls"].append(
                    {
                        "id": part.get("id", ""),
                        "name": call_name,
                        "arguments": json.dumps(part.get("input", {}), ensure_ascii=False),
                        "type": "custom" if call_name in custom_tool_names else "function",
                    }
                )
        result["text"] = "".join(texts)
        result["reasoning"] = "".join(reasoning)
        result["finish_reason"] = payload.get("stop_reason")
        usage = payload.get("usage") or {}
        input_tokens, output_tokens, cached_tokens = extract_usage_tokens(usage, api_mode)
        result["usage"] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cached_tokens": cached_tokens,
            "reasoning_tokens": 0,
        }
    else:
        texts = []
        reasoning = []
        for item in payload.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "function_call":
                call_name = item.get("name", "")
                result["tool_calls"].append(
                    {
                        "id": item.get("call_id") or item.get("id", ""),
                        "name": call_name,
                        "arguments": item.get("arguments", "{}"),
                        "type": "custom" if call_name in custom_tool_names else "function",
                    }
                )
            elif item.get("type") == "reasoning":
                reasoning.append(_content_text(item.get("summary") or item.get("content")))
            for part in item.get("content") or []:
                if isinstance(part, dict):
                    if isinstance(part.get("text"), str):
                        texts.append(part["text"])
                    elif part.get("type") == "summary_text":
                        reasoning.append(part.get("text", ""))
        result["text"] = payload.get("output_text") if isinstance(payload.get("output_text"), str) else "".join(texts)
        result["reasoning"] = "".join(reasoning)
        result["finish_reason"] = payload.get("status")
        usage = payload.get("usage") or {}
        input_tokens, output_tokens, cached_tokens = extract_usage_tokens(usage, api_mode)
        result["usage"] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cached_tokens": cached_tokens,
            "reasoning_tokens": 0,
        }
    return result


def _canonical_stream_events(response, api_mode: str, model: str, custom_tool_names: set[str] | None = None):
    custom_tool_names = custom_tool_names or set()
    started = False
    stopped = False
    event_name = ""
    tool_names = {}
    tool_indices = {}

    def tool_index(source_index: int) -> int:
        if source_index not in tool_indices:
            tool_indices[source_index] = len(tool_indices)
        return tool_indices[source_index]

    lines = iter(response.iter_lines(chunk_size=1))
    while True:
        try:
            raw_line = next(lines)
        except StopIteration:
            break
        except Exception:
            if not stopped:
                raise
            break
        line = raw_line.decode("utf-8", "replace") if isinstance(raw_line, bytes) else raw_line
        if line.startswith("event:"):
            event_name = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            yield {"type": "end"}
            return
        try:
            payload = json.loads(data)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        event_type = payload.get("type") or event_name
        if event_type == "error" or "error" in payload and not payload.get("choices"):
            error = payload.get("error", payload)
            yield {
                "type": "error",
                "message": error.get("message", str(error)) if isinstance(error, dict) else str(error),
            }
            return
        if api_mode == "chat":
            if not started:
                started = True
                yield {"type": "start", "id": payload.get("id", "relay"), "model": payload.get("model", model)}
            choices = payload.get("choices") or []
            choice = choices[0] if choices and isinstance(choices[0], dict) else {}
            delta = choice.get("delta") or {}
            if isinstance(delta.get("reasoning_content"), str):
                yield {"type": "reasoning", "text": delta["reasoning_content"]}
            if isinstance(delta.get("content"), str):
                yield {"type": "text", "text": delta["content"]}
            for call in delta.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                index = tool_index(int(call.get("index", 0)))
                function = call.get("function") or {}
                if call.get("id") or function.get("name"):
                    tool_names[index] = function.get("name", tool_names.get(index, ""))
                    yield {"type": "tool_start", "index": index, "id": call.get("id", ""), "name": tool_names[index]}
                if function.get("arguments"):
                    yield {"type": "tool_args", "index": index, "arguments": function["arguments"]}
            usage = payload.get("usage")
            if isinstance(usage, dict):
                input_tokens, output_tokens, cached_tokens = extract_usage_tokens(usage, api_mode)
                yield {
                    "type": "usage",
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cached_tokens": cached_tokens,
                }
            if choice.get("finish_reason"):
                stopped = True
                yield {"type": "stop", "reason": choice["finish_reason"]}
        elif api_mode == "responses":
            response_data = payload.get("response") or {}
            if event_type in {"response.created", "response.in_progress"} and not started:
                started = True
                yield {
                    "type": "start",
                    "id": response_data.get("id", "relay"),
                    "model": response_data.get("model", model),
                }
            elif event_type == "response.output_text.delta":
                yield {"type": "text", "text": payload.get("delta", "")}
            elif event_type in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
                yield {"type": "reasoning", "text": payload.get("delta", "")}
            elif event_type == "response.output_item.added":
                item = payload.get("item") or {}
                if item.get("type") == "function_call":
                    index = tool_index(int(payload.get("output_index", 0)))
                    tool_names[index] = item.get("name", "")
                    yield {
                        "type": "tool_start",
                        "index": index,
                        "id": item.get("call_id") or item.get("id", ""),
                        "name": item.get("name", ""),
                    }
            elif event_type == "response.function_call_arguments.delta":
                yield {
                    "type": "tool_args",
                    "index": tool_index(int(payload.get("output_index", 0))),
                    "arguments": payload.get("delta", ""),
                }
            elif event_type == "response.completed":
                if not started:
                    started = True
                    yield {
                        "type": "start",
                        "id": response_data.get("id", "relay"),
                        "model": response_data.get("model", model),
                    }
                    normalized = _normalize_response(response_data, "responses", model, custom_tool_names)
                    if normalized["text"]:
                        yield {"type": "text", "text": normalized["text"]}
                    if normalized["reasoning"]:
                        yield {"type": "reasoning", "text": normalized["reasoning"]}
                    for index, call in enumerate(normalized["tool_calls"]):
                        yield {"type": "tool_start", "index": index, "id": call["id"], "name": call["name"]}
                        yield {"type": "tool_args", "index": index, "arguments": call["arguments"]}
                usage = response_data.get("usage") or {}
                input_tokens, output_tokens, cached_tokens = extract_usage_tokens(usage, api_mode)
                yield {
                    "type": "usage",
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cached_tokens": cached_tokens,
                }
                yield {"type": "end"}
                return
        else:
            if event_type == "message_start":
                message = payload.get("message") or {}
                started = True
                yield {"type": "start", "id": message.get("id", "relay"), "model": message.get("model", model)}
                usage = message.get("usage") or {}
                input_tokens, output_tokens, cached_tokens = extract_usage_tokens(usage, api_mode)
                yield {
                    "type": "usage",
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cached_tokens": cached_tokens,
                }
            elif event_type == "content_block_start":
                block = payload.get("content_block") or {}
                if block.get("type") == "tool_use":
                    index = tool_index(int(payload.get("index", 0)))
                    tool_names[index] = block.get("name", "")
                    yield {
                        "type": "tool_start",
                        "index": index,
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                    }
            elif event_type == "content_block_delta":
                delta = payload.get("delta") or {}
                if delta.get("type") == "text_delta":
                    yield {"type": "text", "text": delta.get("text", "")}
                elif delta.get("type") == "thinking_delta":
                    yield {"type": "reasoning", "text": delta.get("thinking", "")}
                elif delta.get("type") == "input_json_delta":
                    yield {
                        "type": "tool_args",
                        "index": tool_index(int(payload.get("index", 0))),
                        "arguments": delta.get("partial_json", ""),
                    }
            elif event_type == "message_delta":
                usage = payload.get("usage") or {}
                input_tokens, output_tokens, cached_tokens = extract_usage_tokens(usage, api_mode)
                yield {
                    "type": "usage",
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cached_tokens": cached_tokens,
                }
                stopped = bool((payload.get("delta") or {}).get("stop_reason"))
                yield {"type": "stop", "reason": (payload.get("delta") or {}).get("stop_reason")}
            elif event_type == "message_stop":
                yield {"type": "end"}
                return
    yield {"type": "end"}


def _collect_stream_result(response, api_mode: str, model: str, custom_tool_names: set[str] | None = None) -> dict:
    result = {
        "id": "relay",
        "model": model,
        "text": "",
        "reasoning": "",
        "tool_calls": [],
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cached_tokens": 0, "reasoning_tokens": 0},
        "finish_reason": None,
    }
    tools = {}
    for event in _canonical_stream_events(response, api_mode, model, custom_tool_names):
        event_type = event.get("type")
        if event_type == "start":
            result["id"] = event.get("id") or result["id"]
            result["model"] = event.get("model") or result["model"]
        elif event_type == "text":
            result["text"] += event.get("text", "")
        elif event_type == "reasoning":
            result["reasoning"] += event.get("text", "")
        elif event_type == "tool_start":
            index = int(event.get("index", 0))
            tool = tools.setdefault(index, {"id": "", "name": "", "arguments": ""})
            tool["id"] = event.get("id") or tool["id"]
            tool["name"] = event.get("name") or tool["name"]
        elif event_type == "tool_args":
            index = int(event.get("index", 0))
            tools.setdefault(index, {"id": "", "name": "", "arguments": ""})["arguments"] += event.get("arguments", "")
        elif event_type == "usage":
            if "input_tokens" in event:
                result["usage"]["input_tokens"] = max(result["usage"]["input_tokens"], event["input_tokens"])
            if "output_tokens" in event:
                result["usage"]["output_tokens"] = max(result["usage"]["output_tokens"], event["output_tokens"])
            if "cached_tokens" in event:
                result["usage"]["cached_tokens"] = max(result["usage"]["cached_tokens"], event["cached_tokens"])
        elif event_type == "stop":
            result["finish_reason"] = event.get("reason")
        elif event_type == "error":
            raise RuntimeError(event.get("message", "上游流式请求失败"))
    custom_tool_names = custom_tool_names or set()
    for tool in tools.values():
        if tool.get("name") in custom_tool_names:
            tool["type"] = "custom"
    result["tool_calls"] = [tools[index] for index in sorted(tools)]
    result["usage"]["total_tokens"] = result["usage"]["input_tokens"] + result["usage"]["output_tokens"]
    return result


def _response_for_mode(result: dict, target_mode: str, custom_tool_names: set[str] | None = None) -> dict:
    usage = result["usage"]
    reason = result.get("finish_reason")
    custom_tool_names = custom_tool_names or set()
    if target_mode == "chat":
        response_id = str(result["id"])
        if response_id.startswith("resp-"):
            response_id = response_id.replace("resp-", "chatcmpl-", 1)
        elif response_id.startswith("resp_"):
            response_id = response_id.replace("resp_", "chatcmpl-", 1)
        message = {
            "role": "assistant",
            "content": result["text"] if result["text"] else None if result["tool_calls"] else "",
        }
        if result.get("reasoning"):
            message["reasoning_content"] = result["reasoning"]
        if result["tool_calls"]:
            message["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": call["arguments"]},
                }
                for call in result["tool_calls"]
            ]
        finish_reason = (
            "tool_calls"
            if result["tool_calls"]
            else "length"
            if reason in {"length", "max_tokens", "incomplete"}
            else "content_filter"
            if reason == "content_filter"
            else "stop"
        )
        return {
            "id": response_id,
            "object": "chat.completion",
            "created": result.get("created", int(time.time())),
            "model": result["model"],
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": {
                "prompt_tokens": usage["input_tokens"],
                "completion_tokens": usage["output_tokens"],
                "total_tokens": usage["total_tokens"],
                "prompt_tokens_details": {"cached_tokens": usage.get("cached_tokens", 0)},
                "completion_tokens_details": {"reasoning_tokens": usage.get("reasoning_tokens", 0)},
            },
        }
    if target_mode == "anthropic":
        content = []
        if result.get("reasoning"):
            content.append({"type": "thinking", "thinking": result["reasoning"]})
        if result["text"]:
            content.append({"type": "text", "text": result["text"]})
        content.extend(
            {"type": "tool_use", "id": call["id"], "name": call["name"], "input": _json_object(call["arguments"])}
            for call in result["tool_calls"]
        )
        if not content:
            content.append({"type": "text", "text": ""})
        stop_reason = (
            "tool_use"
            if result["tool_calls"]
            else "max_tokens"
            if reason in {"length", "max_tokens", "incomplete"}
            else "end_turn"
        )
        return {
            "id": result["id"] if str(result["id"]).startswith("msg_") else f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "model": result["model"],
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "cache_read_input_tokens": usage.get("cached_tokens", 0),
            },
        }
    response_id = str(result["id"])
    if response_id.startswith("chatcmpl-"):
        response_id = response_id.replace("chatcmpl-", "resp-", 1)
    elif response_id.startswith("chatcmpl"):
        response_id = response_id.replace("chatcmpl", "resp", 1)
    elif not response_id.startswith("resp"):
        response_id = f"resp_{response_id}"
    output = []
    if result.get("reasoning"):
        output.append(
            {
                "id": f"rs_{uuid.uuid4().hex}",
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": result["reasoning"]}],
            }
        )
    if result["text"]:
        output.append(
            {
                "id": f"msg_{uuid.uuid4().hex}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": result["text"], "annotations": []}],
            }
        )
    for call in result["tool_calls"]:
        if call.get("type") == "custom" or call.get("name") in custom_tool_names:
            arguments = _json_object(call.get("arguments", ""))
            output.append(
                {
                    "id": f"ctc_{uuid.uuid4().hex}",
                    "type": "custom_tool_call",
                    "status": "completed",
                    "call_id": call["id"],
                    "name": call["name"],
                    "input": arguments.get("input", call.get("arguments", "")),
                }
            )
        else:
            output.append(
                {
                    "id": f"fc_{uuid.uuid4().hex}",
                    "type": "function_call",
                    "status": "completed",
                    "call_id": call["id"],
                    "name": call["name"],
                    "arguments": call["arguments"],
                }
            )
    incomplete = reason in {"length", "max_tokens", "incomplete", "content_filter"}
    response_usage = {
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "total_tokens": usage["total_tokens"],
        "input_tokens_details": {"cached_tokens": usage.get("cached_tokens", 0)},
        "output_tokens_details": {"reasoning_tokens": usage.get("reasoning_tokens", 0)},
    }
    return {
        "id": response_id,
        "object": "response",
        "created_at": result.get("created", int(time.time())),
        "status": "incomplete" if incomplete else "completed",
        "error": None,
        "incomplete_details": {"reason": "content_filter" if reason == "content_filter" else "max_output_tokens"}
        if incomplete
        else None,
        "model": result["model"],
        "output": output,
        "output_text": result["text"],
        "usage": response_usage,
    }
