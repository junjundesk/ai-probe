"""请求体在 Chat、Responses 与 Anthropic 之间的转换。"""

from __future__ import annotations

import json


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else str(content)
    chunks = []
    for part in content:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str):
            chunks.append(text)
    return "".join(chunks)


def _json_object(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _custom_tool_names(body: dict) -> set[str]:
    return {
        str(tool.get("name", ""))
        for tool in body.get("tools") or []
        if isinstance(tool, dict) and tool.get("type") == "custom" and tool.get("name")
    }


def _custom_tool_parameters(tool: dict) -> dict:
    parameters = tool.get("parameters")
    if isinstance(parameters, dict):
        return parameters
    tool_format = tool.get("format")
    if isinstance(tool_format, dict):
        if isinstance(tool_format.get("schema"), dict):
            return tool_format["schema"]
        if tool_format.get("type") == "json_schema" and isinstance(tool_format.get("json_schema"), dict):
            return tool_format["json_schema"]
    return {
        "type": "object",
        "properties": {"input": {"type": "string"}},
        "required": ["input"],
        "additionalProperties": False,
    }


def _responses_tool_to_chat(tool: dict) -> dict:
    if isinstance(tool.get("function"), dict):
        return dict(tool)
    name = str(tool.get("name") or tool.get("type") or "tool")
    function = {
        "name": name,
        "description": tool.get("description", ""),
        "parameters": _custom_tool_parameters(tool),
    }
    if "strict" in tool:
        function["strict"] = tool["strict"]
    return {"type": "function", "function": function}


def _responses_tool_to_responses_function(tool: dict) -> dict:
    if tool.get("type") == "function" and not isinstance(tool.get("function"), dict):
        return dict(tool)
    result = {
        "type": "function",
        "name": str(tool.get("name") or tool.get("type") or "tool"),
        "description": tool.get("description", ""),
        "parameters": _custom_tool_parameters(tool),
    }
    if "strict" in tool:
        result["strict"] = tool["strict"]
    return result


def _responses_custom_to_function(body: dict) -> dict:
    converted = dict(body)
    converted["tools"] = [
        _responses_tool_to_responses_function(tool) for tool in body.get("tools") or [] if isinstance(tool, dict)
    ]
    source = body.get("input")
    if isinstance(source, list):
        input_items = []
        for item in source:
            if not isinstance(item, dict):
                continue
            item = dict(item)
            if item.get("type") == "custom_tool_call":
                value = item.pop("input", "")
                item["type"] = "function_call"
                item["arguments"] = json.dumps({"input": value}, ensure_ascii=False)
            elif item.get("type") == "custom_tool_call_output":
                item["type"] = "function_call_output"
            input_items.append(item)
        converted["input"] = input_items
    choice = body.get("tool_choice")
    if isinstance(choice, dict) and choice.get("type") == "custom":
        converted["tool_choice"] = {"type": "function", "name": choice.get("name", "")}
    return converted


def _responses_part_to_chat(part: dict) -> dict:
    part_type = part.get("type")
    if part_type in {"input_text", "output_text"}:
        return {"type": "text", "text": part.get("text", "")}
    if part_type == "input_image":
        image_url = part.get("image_url", "")
        if isinstance(image_url, dict):
            image_url = image_url.get("url", "")
        return {"type": "image_url", "image_url": {"url": image_url}}
    return dict(part)


def _chat_part_to_responses(part: dict) -> dict:
    part_type = part.get("type")
    if part_type == "text":
        return {"type": "input_text", "text": part.get("text", "")}
    if part_type == "image_url":
        image_url = part.get("image_url", "")
        if isinstance(image_url, dict):
            image_url = image_url.get("url", "")
        return {"type": "input_image", "image_url": image_url}
    return dict(part)


def _reasoning_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            part.get("text", "") for part in value if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _anthropic_part_to_chat(part: dict) -> dict:
    if part.get("type") == "text":
        return {"type": "text", "text": part.get("text", "")}
    if part.get("type") == "image":
        source = part.get("source") or {}
        if source.get("type") == "base64":
            url = f"data:{source.get('media_type', 'application/octet-stream')};base64,{source.get('data', '')}"
            return {"type": "image_url", "image_url": {"url": url}}
    return dict(part)


def _chat_part_to_anthropic(part: dict) -> dict:
    if part.get("type") == "text":
        return {"type": "text", "text": part.get("text", "")}
    if part.get("type") == "image_url":
        image_url = part.get("image_url", "")
        if isinstance(image_url, dict):
            image_url = image_url.get("url", "")
        if isinstance(image_url, str) and image_url.startswith("data:") and ";base64," in image_url:
            header, data = image_url.split(",", 1)
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": header[5:].split(";", 1)[0], "data": data},
            }
        return {"type": "image", "source": {"type": "url", "url": image_url}}
    return dict(part)


def _chat_to_anthropic(body: dict) -> dict:
    messages = []
    system = []
    pending_tool_results = []

    def flush_tool_results():
        if pending_tool_results:
            messages.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for item in body.get("messages") or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "user"))
        if role in {"system", "developer"}:
            flush_tool_results()
            text = _content_text(item.get("content"))
            if text:
                system.append(text)
            continue
        if role == "tool":
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": item.get("tool_call_id", ""),
                    "content": _content_text(item.get("content")),
                }
            )
            continue
        flush_tool_results()
        source_content = item.get("content", "")
        if isinstance(source_content, list):
            content = [_chat_part_to_anthropic(part) for part in source_content if isinstance(part, dict)]
        else:
            content = [{"type": "text", "text": source_content}] if source_content else []
        for tool_call in item.get("tool_calls") or []:
            function = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
            content.append(
                {
                    "type": "tool_use",
                    "id": tool_call.get("id", "") if isinstance(tool_call, dict) else "",
                    "name": function.get("name", ""),
                    "input": _json_object(function.get("arguments", {})),
                }
            )
        messages.append({"role": "assistant" if role == "assistant" else "user", "content": content or ""})

    flush_tool_results()

    result = {
        "model": body.get("model", ""),
        "messages": messages,
        "max_tokens": body.get("max_tokens") or body.get("max_completion_tokens") or 1024,
    }
    if system:
        result["system"] = "\n".join(system)
    for key in ("temperature", "top_p", "stream"):
        if key in body:
            result[key] = body[key]
    stop = body.get("stop_sequences", body.get("stop"))
    if stop is not None:
        result["stop_sequences"] = [stop] if isinstance(stop, str) else stop
    tools = []
    for tool in body.get("tools") or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(function, dict):
            converted = {"name": function.get("name", ""), "input_schema": function.get("parameters", {})}
            if "description" in function:
                converted["description"] = function["description"]
            tools.append(converted)
    if tools:
        result["tools"] = tools
    tool_choice = body.get("tool_choice")
    if tool_choice == "required":
        result["tool_choice"] = {"type": "any"}
    elif tool_choice in {"auto", "none"}:
        if tool_choice == "none":
            result.pop("tools", None)
        else:
            result["tool_choice"] = {"type": "auto"}
    elif isinstance(tool_choice, dict):
        function = tool_choice.get("function") or {}
        result["tool_choice"] = {"type": "tool", "name": function.get("name", tool_choice.get("name", ""))}
    return result


def _chat_to_responses(body: dict) -> dict:
    input_items = []
    instructions = []
    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role in {"system", "developer"}:
            text = _content_text(message.get("content"))
            if text:
                instructions.append(text)
            continue
        if role == "tool":
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.get("tool_call_id", ""),
                    "output": message.get("content", ""),
                }
            )
            continue
        content = message.get("content")
        reasoning = _reasoning_text(message.get("reasoning_content") or message.get("reasoning"))
        if role == "assistant" and reasoning:
            input_items.append(
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": reasoning}],
                }
            )
        if isinstance(content, list):
            content = [_chat_part_to_responses(part) for part in content if isinstance(part, dict)]
        if content not in (None, ""):
            input_items.append({"role": role, "content": content})
        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
            input_items.append(
                {
                    "type": "function_call",
                    "call_id": tool_call.get("id", "") if isinstance(tool_call, dict) else "",
                    "name": function.get("name", ""),
                    "arguments": function.get("arguments", "{}"),
                }
            )

    result = {"model": body.get("model", ""), "input": input_items}
    if instructions:
        result["instructions"] = "\n".join(instructions)
    if "max_tokens" in body or "max_completion_tokens" in body:
        result["max_output_tokens"] = body.get("max_tokens", body.get("max_completion_tokens"))
    for key in (
        "temperature",
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "stream",
        "parallel_tool_calls",
        "metadata",
        "user",
    ):
        if key in body:
            result[key] = body[key]
    if body.get("reasoning_effort"):
        result["reasoning"] = {"effort": body["reasoning_effort"]}

    tools = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if tool.get("type") == "function" and isinstance(function, dict):
            converted = {
                "type": "function",
                "name": function.get("name", ""),
                "parameters": function.get("parameters", {}),
            }
            for key in ("description", "strict"):
                if key in function:
                    converted[key] = function[key]
            tools.append(converted)
        else:
            tools.append(dict(tool))
    if tools:
        result["tools"] = tools

    tool_choice = body.get("tool_choice")
    if isinstance(tool_choice, dict) and isinstance(tool_choice.get("function"), dict):
        result["tool_choice"] = {"type": "function", "name": tool_choice["function"].get("name", "")}
    elif tool_choice is not None:
        result["tool_choice"] = tool_choice
    return result


def _responses_to_chat(body: dict) -> dict:
    messages = []
    if body.get("instructions"):
        messages.append({"role": "system", "content": body["instructions"]})
    source = body.get("input", "")
    if isinstance(source, str):
        messages.append({"role": "user", "content": source})
    else:
        pending_reasoning = ""
        pending_tool_calls = []

        def flush_tool_calls():
            nonlocal pending_reasoning
            if not pending_tool_calls:
                return
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": list(pending_tool_calls),
            }
            if pending_reasoning:
                message["reasoning_content"] = pending_reasoning
            messages.append(message)
            pending_tool_calls.clear()
            pending_reasoning = ""

        for item in source or []:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "reasoning":
                pending_reasoning += _reasoning_text(item.get("summary") or item.get("content"))
                continue
            if item_type == "function_call":
                pending_tool_calls.append(
                    {
                        "id": item.get("call_id") or item.get("id", ""),
                        "type": "function",
                        "function": {"name": item.get("name", ""), "arguments": item.get("arguments", "{}")},
                    }
                )
            elif item_type == "custom_tool_call":
                pending_tool_calls.append(
                    {
                        "id": item.get("call_id") or item.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": item.get("name", ""),
                            "arguments": json.dumps({"input": item.get("input", "")}, ensure_ascii=False),
                        },
                    }
                )
            elif item_type == "function_call_output" or item_type == "custom_tool_call_output":
                flush_tool_calls()
                messages.append(
                    {"role": "tool", "tool_call_id": item.get("call_id", ""), "content": item.get("output", "")}
                )
            elif item_type == "message" or "role" in item:
                flush_tool_calls()
                role = "system" if item.get("role") == "developer" else item.get("role", "user")
                content = item.get("content")
                if isinstance(content, list):
                    converted = [_responses_part_to_chat(part) for part in content if isinstance(part, dict)]
                    content = (
                        "".join(part.get("text", "") for part in converted)
                        if converted and all(part.get("type") == "text" for part in converted)
                        else converted
                    )
                message = {"role": role, "content": content}
                if role == "assistant" and pending_reasoning:
                    message["reasoning_content"] = pending_reasoning
                    pending_reasoning = ""
                messages.append(message)
        flush_tool_calls()
        if pending_reasoning:
            messages.append({"role": "assistant", "content": None, "reasoning_content": pending_reasoning})
    result = {"model": body.get("model", ""), "messages": messages}
    if "max_output_tokens" in body:
        result["max_tokens"] = body["max_output_tokens"]
    for key in (
        "stream",
        "temperature",
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "stop",
        "seed",
        "parallel_tool_calls",
        "response_format",
        "n",
        "logit_bias",
        "user",
    ):
        if key in body:
            result[key] = body[key]
    tools = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        tools.append(_responses_tool_to_chat(tool))
    if tools:
        result["tools"] = tools
    tool_choice = body.get("tool_choice")
    if (
        isinstance(tool_choice, dict)
        and tool_choice.get("type") == "function"
        or isinstance(tool_choice, dict)
        and tool_choice.get("type") == "custom"
    ):
        result["tool_choice"] = {"type": "function", "function": {"name": tool_choice.get("name", "")}}
    elif tool_choice is not None:
        result["tool_choice"] = tool_choice
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort"):
        result["reasoning_effort"] = reasoning["effort"]
    return result


def _anthropic_to_chat(body: dict) -> dict:
    messages = []
    system = body.get("system")
    if isinstance(system, str) and system:
        messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        text = "\n".join(
            part.get("text", "") for part in system if isinstance(part, dict) and part.get("type") == "text"
        )
        if text:
            messages.append({"role": "system", "content": text})
    for item in body.get("messages") or []:
        if not isinstance(item, dict):
            continue
        role = item.get("role", "user")
        content = item.get("content", "")
        if isinstance(content, str):
            messages.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            continue
        if role == "user" and any(isinstance(part, dict) and part.get("type") == "tool_result" for part in content):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_result":
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": part.get("tool_use_id", ""),
                            "content": _content_text(part.get("content")),
                        }
                    )
                else:
                    messages.append({"role": "user", "content": [_anthropic_part_to_chat(part)]})
            continue
        if role == "assistant":
            text_parts = []
            tool_calls = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                elif part.get("type") == "tool_use":
                    tool_calls.append(
                        {
                            "id": part.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": part.get("name", ""),
                                "arguments": json.dumps(part.get("input", {}), ensure_ascii=False),
                            },
                        }
                    )
            message = {"role": "assistant", "content": "\n".join(text_parts) if text_parts else None}
            if tool_calls:
                message["tool_calls"] = tool_calls
            messages.append(message)
            continue
        messages.append(
            {"role": role, "content": [_anthropic_part_to_chat(part) for part in content if isinstance(part, dict)]}
        )
    result = {"model": body.get("model", ""), "messages": messages}
    if "max_tokens" in body:
        result["max_tokens"] = body["max_tokens"]
    for key in ("temperature", "top_p", "stream"):
        if key in body:
            result[key] = body[key]
    if "stop_sequences" in body:
        result["stop"] = body["stop_sequences"]
    tools = []
    for tool in body.get("tools") or []:
        if isinstance(tool, dict):
            function = {"name": tool.get("name", ""), "parameters": tool.get("input_schema", {})}
            if "description" in tool:
                function["description"] = tool["description"]
            tools.append({"type": "function", "function": function})
    if tools:
        result["tools"] = tools
    choice = body.get("tool_choice")
    if isinstance(choice, dict):
        if choice.get("type") == "any":
            result["tool_choice"] = "required"
        elif choice.get("type") == "tool":
            result["tool_choice"] = {"type": "function", "function": {"name": choice.get("name", "")}}
        elif choice.get("type") == "auto":
            result["tool_choice"] = "auto"
    return result


def _request_mode(path: str) -> str:
    if "/responses" in path:
        return "responses"
    if "/messages" in path:
        return "anthropic"
    return "chat"


def _convert_request(body: dict, source_mode: str, target_mode: str) -> dict:
    if source_mode == target_mode:
        return dict(body)
    chat = (
        body
        if source_mode == "chat"
        else _responses_to_chat(body)
        if source_mode == "responses"
        else _anthropic_to_chat(body)
    )
    if target_mode == "chat":
        return chat
    if target_mode == "responses":
        return _chat_to_responses(chat)
    return _chat_to_anthropic(chat)
