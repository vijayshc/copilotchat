from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Optional

DEFAULT_PROXY_MODEL = "copilot-proxy"
SYSTEM_FINGERPRINT = "copilot-proxy-openai-v1"
MAX_STORED_CONVERSATIONS = 32


class OpenAIProxyError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        error_type: str = "invalid_request_error",
        param: Optional[str] = None,
        code: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        self.param = param
        self.code = code

    def to_response(self) -> dict[str, Any]:
        return {
            "error": {
                "message": self.message,
                "type": self.error_type,
                "param": self.param,
                "code": self.code,
            }
        }


@dataclass(slots=True)
class ParsedChatRequest:
    model: str
    messages: list[dict[str, Any]]
    stream: bool
    tools: list[dict[str, Any]]
    tool_choice: str | dict[str, Any]
    response_format: dict[str, Any]
    stop: list[str]
    max_output_tokens: Optional[int]
    include_usage: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def last_message(self) -> dict[str, Any]:
        return self.messages[-1]

    @property
    def prior_messages(self) -> list[dict[str, Any]]:
        return self.messages[:-1]

@dataclass(slots=True)
class AssistantTurn:
    content: Optional[str]
    tool_calls: list[dict[str, Any]]
    finish_reason: str
    raw_text: str


@dataclass(slots=True)
class CompletionResult:
    completion_id: str
    created: int
    model: str
    assistant: AssistantTurn
    usage: dict[str, int]
    include_usage: bool


@dataclass(slots=True)
class ConversationRecord:
    id: str
    transcript: list[dict[str, Any]]
    initial_tools: list[dict[str, Any]]
    created_at: int
    updated_at: int



class OpenAICompatProxy:
    def __init__(
        self,
        *,
        send_prompt: Callable[[str], str],
        reset_conversation: Optional[Callable[[], None]] = None,
        default_model: str = DEFAULT_PROXY_MODEL,
    ) -> None:
        self._send_prompt = send_prompt
        self._reset_conversation = reset_conversation
        self.default_model = default_model
        self._lock = threading.RLock()
        self._conversations: dict[str, ConversationRecord] = {}
        self._active_conversation_id: Optional[str] = None

    # ------------------------------------------------------------------ public API

    def list_models_response(self) -> dict[str, Any]:
        created = int(time.time())
        return {
            "object": "list",
            "data": [
                {
                    "id": self.default_model,
                    "object": "model",
                    "created": created,
                    "owned_by": "copilot-proxy",
                }
            ],
        }

    def retrieve_model_response(self, model_id: str) -> dict[str, Any]:
        created = int(time.time())
        return {
            "id": model_id,
            "object": "model",
            "created": created,
            "owned_by": "copilot-proxy",
        }

    def create_chat_completion(self, payload: Any) -> dict[str, Any]:
        result = self._complete(payload)
        return self._build_completion_payload(result)

    def stream_chat_completion(self, payload: Any) -> Iterator[str]:
        result = self._complete(payload)
        yield from self._completion_events(result)

    # ------------------------------------------------------------------ turn orchestration

    def _complete(self, payload: Any) -> CompletionResult:
        request = self._parse_request(payload)
        with self._lock:
            conversation = self._find_conversation(request)
            is_continuation = conversation is not None and conversation.id == self._active_conversation_id

            if is_continuation:
                prompt = self._render_single_message(request.last_message)
            else:
                if self._reset_conversation is not None:
                    self._reset_conversation()
                if conversation is None:
                    now = int(time.time())
                    conversation = ConversationRecord(
                        id=uuid.uuid4().hex,
                        transcript=[],
                        initial_tools=[json.loads(json.dumps(t)) for t in request.tools],
                        created_at=now,
                        updated_at=now,
                    )
                    self._conversations[conversation.id] = conversation
                prompt = self._render_full_request(request)

            raw_text = self._send_prompt(prompt)
            assistant = self._parse_assistant_turn(raw_text, request)
            assistant = self._apply_output_constraints(assistant, request)
            self._commit_turn(conversation, request, assistant)
            usage = self._estimate_usage(request, assistant)
            return CompletionResult(
                completion_id=f"chatcmpl-{uuid.uuid4().hex}",
                created=int(time.time()),
                model=request.model,
                assistant=assistant,
                usage=usage,
                include_usage=request.include_usage,
            )

    def _find_conversation(self, request: ParsedChatRequest) -> Optional[ConversationRecord]:
        prior_messages = request.prior_messages
        provided_tool_signature = self._signature(request.tools) if request.tools else None
        for conversation in self._conversations.values():
            if conversation.transcript != prior_messages:
                continue
            if provided_tool_signature and self._signature(conversation.initial_tools) != provided_tool_signature:
                continue
            return conversation
        return None

    def _commit_turn(self, conversation: ConversationRecord, request: ParsedChatRequest, assistant: AssistantTurn) -> None:
        transcript = [json.loads(json.dumps(message)) for message in request.messages]
        transcript.append(self._canonical_assistant_message(assistant))
        conversation.transcript = transcript
        conversation.updated_at = int(time.time())
        if not conversation.initial_tools and request.tools:
            conversation.initial_tools = [json.loads(json.dumps(tool)) for tool in request.tools]
        self._active_conversation_id = conversation.id
        self._prune_conversations()

    def _prune_conversations(self) -> None:
        if len(self._conversations) <= MAX_STORED_CONVERSATIONS:
            return
        removable = sorted(self._conversations.values(), key=lambda item: item.updated_at)
        while len(self._conversations) > MAX_STORED_CONVERSATIONS and removable:
            record = removable.pop(0)
            if record.id == self._active_conversation_id:
                continue
            self._conversations.pop(record.id, None)

    # ------------------------------------------------------------------ request parsing

    def _parse_request(self, payload: Any) -> ParsedChatRequest:
        if not isinstance(payload, dict):
            raise OpenAIProxyError("Request body must be a JSON object.")

        model = str(payload.get("model") or self.default_model)
        messages = self._normalize_messages(payload.get("messages"))
        if not messages:
            raise OpenAIProxyError("messages must contain at least one item.", param="messages")

        last_role = messages[-1]["role"]
        if last_role not in {"user", "tool"}:
            raise OpenAIProxyError(
                "The last message must have role 'user' or 'tool'.",
                param="messages",
            )

        n = payload.get("n", 1)
        if n not in (None, 1):
            raise OpenAIProxyError("Only n=1 is supported by this proxy.", param="n")

        tools = self._normalize_tools(payload.get("tools"))
        tool_choice = self._normalize_tool_choice(payload.get("tool_choice"), tools)
        response_format = self._normalize_response_format(payload.get("response_format"))
        stop = self._normalize_stop(payload.get("stop"))
        max_output_tokens = self._normalize_max_tokens(payload.get("max_completion_tokens"), payload.get("max_tokens"))
        include_usage = bool((payload.get("stream_options") or {}).get("include_usage"))

        return ParsedChatRequest(
            model=model,
            messages=messages,
            stream=bool(payload.get("stream")),
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            stop=stop,
            max_output_tokens=max_output_tokens,
            include_usage=include_usage,
            metadata={
                "user": payload.get("user"),
                "temperature": payload.get("temperature"),
                "top_p": payload.get("top_p"),
                "presence_penalty": payload.get("presence_penalty"),
                "frequency_penalty": payload.get("frequency_penalty"),
                "parallel_tool_calls": payload.get("parallel_tool_calls"),
                "metadata": payload.get("metadata"),
            },
        )

    def _normalize_messages(self, raw_messages: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_messages, list):
            raise OpenAIProxyError("messages must be an array.", param="messages")

        normalized: list[dict[str, Any]] = []
        for index, message in enumerate(raw_messages):
            if not isinstance(message, dict):
                raise OpenAIProxyError(
                    f"messages[{index}] must be an object.",
                    param=f"messages[{index}]",
                )

            role = str(message.get("role") or "").strip().lower()
            if role not in {"system", "developer", "user", "assistant", "tool"}:
                raise OpenAIProxyError(
                    f"Unsupported message role at messages[{index}]: {role!r}.",
                    param=f"messages[{index}].role",
                )

            content = self._normalize_content(message.get("content"), f"messages[{index}].content")
            item: dict[str, Any] = {"role": role, "content": content}

            if role == "assistant":
                tool_calls = message.get("tool_calls")
                function_call = message.get("function_call")
                if tool_calls is None and isinstance(function_call, dict):
                    tool_calls = [{"id": message.get("tool_call_id"), "type": "function", "function": function_call}]
                if tool_calls is not None:
                    item["tool_calls"] = self._normalize_tool_call_list(tool_calls, f"messages[{index}].tool_calls")

            if role == "tool":
                tool_call_id = str(message.get("tool_call_id") or "").strip()
                if not tool_call_id:
                    raise OpenAIProxyError(
                        f"messages[{index}].tool_call_id is required for tool messages.",
                        param=f"messages[{index}].tool_call_id",
                    )
                item["tool_call_id"] = tool_call_id
                name = str(message.get("name") or "").strip()
                if name:
                    item["name"] = name

            normalized.append(item)
        return normalized

    def _normalize_content(self, content: Any, param: str) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content.replace("\r\n", "\n")
        if isinstance(content, dict):
            content = [content]
        if not isinstance(content, list):
            raise OpenAIProxyError(f"{param} must be a string or content-part array.", param=param)

        parts: list[str] = []
        for index, item in enumerate(content):
            if not isinstance(item, dict):
                raise OpenAIProxyError(
                    f"{param}[{index}] must be an object.",
                    param=f"{param}[{index}]",
                )
            item_type = str(item.get("type") or "text").strip().lower()
            if item_type in {"text", "input_text", "output_text"}:
                text = item.get("text")
                if text is None and isinstance(item.get("value"), str):
                    text = item.get("value")
                parts.append(str(text or ""))
                continue
            raise OpenAIProxyError(
                f"Unsupported content part type {item_type!r}; this proxy currently supports text-only messages.",
                param=f"{param}[{index}].type",
            )
        return "".join(parts).replace("\r\n", "\n")

    def _normalize_tools(self, raw_tools: Any) -> list[dict[str, Any]]:
        if raw_tools in (None, []):
            return []
        if not isinstance(raw_tools, list):
            raise OpenAIProxyError("tools must be an array.", param="tools")

        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(raw_tools):
            if not isinstance(item, dict):
                raise OpenAIProxyError(f"tools[{index}] must be an object.", param=f"tools[{index}]")
            if str(item.get("type") or "").lower() != "function":
                raise OpenAIProxyError(
                    "Only function tools are supported by this proxy.",
                    param=f"tools[{index}].type",
                )
            function = item.get("function")
            if not isinstance(function, dict):
                raise OpenAIProxyError(
                    f"tools[{index}].function must be an object.",
                    param=f"tools[{index}].function",
                )
            name = str(function.get("name") or "").strip()
            if not name:
                raise OpenAIProxyError(
                    f"tools[{index}].function.name is required.",
                    param=f"tools[{index}].function.name",
                )
            normalized.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": str(function.get("description") or "").strip(),
                        "parameters": function.get("parameters") if isinstance(function.get("parameters"), dict) else {},
                    },
                }
            )
        return normalized

    def _normalize_tool_choice(self, tool_choice: Any, tools: list[dict[str, Any]]) -> str | dict[str, Any]:
        if tool_choice in (None, "", False):
            return "auto" if tools else "none"
        if isinstance(tool_choice, str):
            normalized = tool_choice.strip().lower()
            if normalized not in {"auto", "none", "required"}:
                raise OpenAIProxyError("tool_choice must be auto, none, required, or a function choice object.", param="tool_choice")
            if normalized in {"auto", "required"} and not tools:
                raise OpenAIProxyError("tool_choice requires at least one tool.", param="tool_choice")
            return normalized
        if not isinstance(tool_choice, dict):
            raise OpenAIProxyError("tool_choice must be a string or object.", param="tool_choice")
        if str(tool_choice.get("type") or "").lower() != "function":
            raise OpenAIProxyError("Only function tool_choice objects are supported.", param="tool_choice.type")
        function = tool_choice.get("function")
        if not isinstance(function, dict):
            raise OpenAIProxyError("tool_choice.function must be an object.", param="tool_choice.function")
        name = str(function.get("name") or "").strip()
        if not name:
            raise OpenAIProxyError("tool_choice.function.name is required.", param="tool_choice.function.name")
        if tools and name not in {tool["function"]["name"] for tool in tools}:
            raise OpenAIProxyError(
                f"tool_choice.function.name {name!r} was not found in tools.",
                param="tool_choice.function.name",
            )
        return {"type": "function", "function": {"name": name}}

    def _normalize_response_format(self, value: Any) -> dict[str, Any]:
        if value in (None, "", False):
            return {"type": "text"}
        if isinstance(value, str):
            return {"type": value.strip().lower()}
        if not isinstance(value, dict):
            raise OpenAIProxyError("response_format must be an object.", param="response_format")
        normalized = dict(value)
        normalized["type"] = str(normalized.get("type") or "text").strip().lower()
        return normalized

    def _normalize_stop(self, value: Any) -> list[str]:
        if value in (None, "", []):
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return list(value)
        raise OpenAIProxyError("stop must be a string or array of strings.", param="stop")

    def _normalize_max_tokens(self, first: Any, second: Any) -> Optional[int]:
        values = [value for value in (first, second) if value not in (None, "")]
        if not values:
            return None
        normalized: list[int] = []
        for value in values:
            try:
                int_value = int(value)
            except (TypeError, ValueError) as exc:
                raise OpenAIProxyError("max_tokens must be an integer.", param="max_tokens") from exc
            if int_value <= 0:
                raise OpenAIProxyError("max_tokens must be greater than zero.", param="max_tokens")
            normalized.append(int_value)
        return min(normalized)

    def _normalize_tool_call_list(self, raw_tool_calls: Any, param: str) -> list[dict[str, Any]]:
        if not isinstance(raw_tool_calls, list):
            raise OpenAIProxyError(f"{param} must be an array.", param=param)
        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for index, item in enumerate(raw_tool_calls):
            if not isinstance(item, dict):
                raise OpenAIProxyError(f"{param}[{index}] must be an object.", param=f"{param}[{index}]")
            function = item.get("function")
            if not isinstance(function, dict):
                raise OpenAIProxyError(
                    f"{param}[{index}].function must be an object.",
                    param=f"{param}[{index}].function",
                )
            name = str(function.get("name") or "").strip()
            if not name:
                raise OpenAIProxyError(
                    f"{param}[{index}].function.name is required.",
                    param=f"{param}[{index}].function.name",
                )
            arguments = function.get("arguments", "{}")
            if isinstance(arguments, (dict, list)):
                arguments = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
            elif not isinstance(arguments, str):
                arguments = str(arguments)
            call_id = str(item.get("id") or f"call_{uuid.uuid4().hex[:24]}")
            if call_id in seen_ids:
                call_id = f"call_{uuid.uuid4().hex[:24]}"
            seen_ids.add(call_id)
            normalized.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": arguments,
                    },
                }
            )
        return normalized

    # ------------------------------------------------------------------ prompt rendering

    def _render_full_request(self, request: ParsedChatRequest) -> str:
        parts = [self._render_transcript(request.messages)]
        if request.tools:
            parts.append("[tools]\n" + json.dumps(request.tools, ensure_ascii=False, indent=2))
        return "\n\n".join(parts)

    def _render_transcript(self, messages: Iterable[dict[str, Any]]) -> str:
        return "\n\n".join(self._render_single_message(message) for message in messages)

    def _render_single_message(self, message: dict[str, Any]) -> str:
        role = message["role"]
        if role == "assistant" and message.get("tool_calls"):
            tool_calls = json.dumps(message["tool_calls"], ensure_ascii=False, indent=2)
            content = message.get("content") or ""
            if content.strip():
                return f"[assistant]\n{content}\n\n[assistant_tool_calls]\n{tool_calls}"
            return f"[assistant_tool_calls]\n{tool_calls}"
        if role == "tool":
            name = f" name={message.get('name')}" if message.get("name") else ""
            return f"[tool id={message['tool_call_id']}{name}]\n{message.get('content') or ''}"
        return f"[{role}]\n{message.get('content') or ''}"

    # ------------------------------------------------------------------ response parsing

    def _parse_assistant_turn(self, raw_text: str, request: ParsedChatRequest) -> AssistantTurn:
        text = (raw_text or "").replace("\r\n", "\n").strip()

        tool_calls = self._extract_tool_calls(text)
        if tool_calls is not None:
            normalized = self._normalize_tool_call_list(tool_calls, "tool_calls")
            return AssistantTurn(content=None, tool_calls=normalized, finish_reason="tool_calls", raw_text=text)

        if request.response_format.get("type") in {"json_object", "json_schema"}:
            extracted = self._extract_json_fragment(text)
            if extracted is not None:
                return AssistantTurn(content=extracted, tool_calls=[], finish_reason="stop", raw_text=text)
            raise OpenAIProxyError(
                "The Copilot response could not be converted into valid JSON for the requested response_format.",
                status_code=502,
                error_type="server_error",
            )

        return AssistantTurn(content=text, tool_calls=[], finish_reason="stop", raw_text=text)

    def _apply_output_constraints(self, assistant: AssistantTurn, request: ParsedChatRequest) -> AssistantTurn:
        if assistant.tool_calls:
            return assistant

        content = assistant.content or ""
        finish_reason = assistant.finish_reason
        if request.stop:
            truncated = self._apply_stop_sequences(content, request.stop)
            if truncated != content:
                content = truncated
                finish_reason = "stop"
        if request.max_output_tokens is not None:
            limited, was_truncated = self._truncate_to_token_budget(content, request.max_output_tokens)
            if was_truncated:
                content = limited
                finish_reason = "length"
        return AssistantTurn(content=content, tool_calls=[], finish_reason=finish_reason, raw_text=assistant.raw_text)

    def _apply_stop_sequences(self, content: str, stop_sequences: list[str]) -> str:
        cut_points = [content.find(item) for item in stop_sequences if item and content.find(item) >= 0]
        if not cut_points:
            return content
        return content[: min(cut_points)]

    def _truncate_to_token_budget(self, content: str, max_tokens: int) -> tuple[str, bool]:
        if self._estimate_tokens(content) <= max_tokens:
            return content, False
        estimated_chars = max(max_tokens * 4, 1)
        truncated = content[:estimated_chars].rstrip()
        if not truncated:
            return truncated, True
        return truncated, True

    def _extract_json_payload(self, text: str) -> Optional[dict[str, Any]]:
        fragment = self._extract_json_fragment(text)
        if fragment is None:
            return None
        return self._load_json_object(fragment)

    def _extract_tool_calls(self, text: str) -> Optional[list[dict[str, Any]]]:
        parsed = self._extract_json_payload(text)
        if isinstance(parsed, dict) and isinstance(parsed.get("tool_calls"), list):
            return parsed["tool_calls"]
        return None

    @staticmethod
    def _load_json_object(fragment: str) -> Optional[dict[str, Any]]:
        try:
            payload = json.loads(fragment)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            return payload
        return None

    def _extract_json_fragment(self, text: str) -> Optional[str]:
        if not text:
            return None

        stripped = text.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                stripped = "\n".join(lines[1:-1]).strip()
                if stripped.lower().startswith("json"):
                    stripped = stripped[4:].strip()
        if self._looks_like_json(stripped):
            return stripped

        start = stripped.find("{")
        while start >= 0:
            candidate = self._balanced_json_candidate(stripped[start:])
            if candidate is not None:
                return candidate
            start = stripped.find("{", start + 1)
        return None

    def _balanced_json_candidate(self, text: str) -> Optional[str]:
        depth = 0
        in_string = False
        escape = False
        for index, char in enumerate(text):
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[: index + 1]
                    if self._looks_like_json(candidate):
                        return candidate
        return None

    @staticmethod
    def _looks_like_json(text: str) -> bool:
        if not text:
            return False
        text = text.strip()
        return text.startswith("{") and text.endswith("}")

    # ------------------------------------------------------------------ output formatting

    def _build_completion_payload(self, result: CompletionResult) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": result.assistant.content,
        }
        if result.assistant.tool_calls:
            message["content"] = None
            message["tool_calls"] = result.assistant.tool_calls

        return {
            "id": result.completion_id,
            "object": "chat.completion",
            "created": result.created,
            "model": result.model,
            "system_fingerprint": SYSTEM_FINGERPRINT,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "logprobs": None,
                    "finish_reason": result.assistant.finish_reason,
                }
            ],
            "usage": result.usage,
        }

    def _completion_events(self, result: CompletionResult) -> Iterator[str]:
        base = {
            "id": result.completion_id,
            "object": "chat.completion.chunk",
            "created": result.created,
            "model": result.model,
            "system_fingerprint": SYSTEM_FINGERPRINT,
        }
        yield self._sse_chunk(
            {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant"},
                        "logprobs": None,
                        "finish_reason": None,
                    }
                ],
            }
        )

        if result.assistant.tool_calls:
            yield self._sse_chunk(
                {
                    **base,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"tool_calls": result.assistant.tool_calls},
                            "logprobs": None,
                            "finish_reason": None,
                        }
                    ],
                }
            )
        else:
            for piece in self._chunk_text(result.assistant.content or ""):
                yield self._sse_chunk(
                    {
                        **base,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": piece},
                                "logprobs": None,
                                "finish_reason": None,
                            }
                        ],
                    }
                )

        yield self._sse_chunk(
            {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "logprobs": None,
                        "finish_reason": result.assistant.finish_reason,
                    }
                ],
            }
        )
        if result.include_usage:
            yield self._sse_chunk({**base, "choices": [], "usage": result.usage})
        yield "data: [DONE]\n\n"

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 96) -> Iterator[str]:
        if not text:
            return iter(())
        for index in range(0, len(text), chunk_size):
            yield text[index:index + chunk_size]

    @staticmethod
    def _sse_chunk(payload: dict[str, Any]) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    # ------------------------------------------------------------------ usage helpers

    def _estimate_usage(self, request: ParsedChatRequest, assistant: AssistantTurn) -> dict[str, int]:
        prompt_text = json.dumps(
            {
                "messages": request.messages,
                "tools": request.tools,
                "tool_choice": request.tool_choice,
                "response_format": request.response_format,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        completion_text = assistant.content or json.dumps(assistant.tool_calls, ensure_ascii=False, separators=(",", ":"))
        prompt_tokens = self._estimate_tokens(prompt_text)
        completion_tokens = self._estimate_tokens(completion_text)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, (len(text) + 3) // 4)

    def _canonical_assistant_message(self, assistant: AssistantTurn) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": assistant.content or "",
        }
        if assistant.tool_calls:
            message["tool_calls"] = json.loads(json.dumps(assistant.tool_calls))
        return message

    @staticmethod
    def _signature(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
