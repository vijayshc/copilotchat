"""SignalR protocol parsing and Copilot event extraction."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .constants import RECORD_SEPARATOR
from .helpers import coerce_text, decode_transport_text
from .models import ParsedCopilotEvent


logger = logging.getLogger(__name__)


class SignalRProtocolParser:
    """Parse SignalR JSON records from a WebSocket frame payload."""

    def parse_frame(self, payload: str | bytes) -> list[dict[str, Any]]:
        if isinstance(payload, bytes):
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                logger.warning("Skipping non-UTF-8 websocket frame payload at byte offset %s", exc.start)
                return []
        else:
            text = payload
        text = decode_transport_text(text)
        records: list[dict[str, Any]] = []
        for chunk in text.split(RECORD_SEPARATOR):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                parsed = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
        return records


class CopilotSignalRExtractor:
    """Extract Copilot-relevant events from SignalR protocol messages."""

    def extract_events(
        self,
        message: dict[str, Any],
        direction: str,
        socket_id: Optional[str] = None,
    ) -> list[ParsedCopilotEvent]:
        events: list[ParsedCopilotEvent] = []
        message_type = message.get("type")

        if direction == "received" and message_type in {3, 7}:
            kind = "completion" if message_type == 3 else "socket_close"
            events.append(
                ParsedCopilotEvent(
                    kind=kind,
                    invocation_id=message.get("invocationId"),
                    socket_id=socket_id,
                    direction=direction,
                    raw_type=message_type,
                    payload=message,
                )
            )

        containers: list[Any] = [message]
        if isinstance(message.get("arguments"), list):
            containers.extend(message["arguments"])
        if "item" in message:
            containers.append(message["item"])
        if "result" in message:
            containers.append(message["result"])

        seen: set[tuple[str, Optional[str], str, Optional[str]]] = set()
        for container in containers:
            for event in self._extract_from_container(container, message, direction, socket_id):
                signature = (event.kind, event.message_id, event.content, event.invocation_id)
                if signature in seen:
                    continue
                seen.add(signature)
                events.append(event)
        return events

    def _extract_from_container(
        self,
        container: Any,
        root_message: dict[str, Any],
        direction: str,
        socket_id: Optional[str],
    ) -> list[ParsedCopilotEvent]:
        events: list[ParsedCopilotEvent] = []
        if isinstance(container, dict):
            if direction == "received":
                fragment, streaming_mode = self._extract_delta_fragment(container)
                if fragment:
                    kind = "thinking_delta" if self._looks_like_thinking(container, fragment) else "assistant_delta"
                    events.append(
                        ParsedCopilotEvent(
                            kind=kind,
                            role="thinking" if kind == "thinking_delta" else "ai",
                            content=fragment,
                            invocation_id=root_message.get("invocationId"),
                            socket_id=socket_id,
                            direction=direction,
                            streaming_mode=streaming_mode,
                            raw_type=root_message.get("type"),
                            payload=container,
                        )
                    )

            messages = container.get("messages")
            if isinstance(messages, list):
                for item in messages:
                    event = self._message_dict_to_event(item, root_message, socket_id, direction)
                    if event:
                        events.append(event)

            direct_event = self._message_dict_to_event(container, root_message, socket_id, direction)
            if direct_event:
                events.append(direct_event)

            for value in container.values():
                if isinstance(value, (dict, list)):
                    events.extend(self._extract_from_container(value, root_message, direction, socket_id))
        elif isinstance(container, list):
            for item in container:
                events.extend(self._extract_from_container(item, root_message, direction, socket_id))
        return events

    def _extract_delta_fragment(self, container: dict[str, Any]) -> tuple[str, Optional[str]]:
        streaming_mode = coerce_text(container.get("streamingMode")) or None
        for key in ("writeAtCursor", "appendText", "textDelta", "contentDelta", "messageDelta"):
            fragment = coerce_text(container.get(key))
            if fragment:
                normalized_mode = streaming_mode or ("Delta" if key in {"writeAtCursor", "appendText"} else None)
                return decode_transport_text(fragment), normalized_mode
        return "", streaming_mode

    def _message_dict_to_event(
        self,
        message: Any,
        root_message: dict[str, Any],
        socket_id: Optional[str],
        direction: str,
    ) -> Optional[ParsedCopilotEvent]:
        if not isinstance(message, dict):
            return None

        text = ""
        for key in ("text", "content", "message", "body", "displayText"):
            text = coerce_text(message.get(key))
            if text:
                break
        if not text:
            return None
        text = decode_transport_text(text)

        author = coerce_text(message.get("author") or message.get("role")).lower()
        if author in {"user", "human", "me"}:
            kind, role = "user_message", "user"
        elif author in {"bot", "assistant", "copilot"}:
            kind, role = ("thinking_final", "thinking") if self._looks_like_thinking(message, text) else ("assistant_final", "ai")
        elif self._looks_like_thinking(message, text):
            kind, role = "thinking_final", "thinking"
        elif self._looks_like_bot_payload(message):
            kind, role = "assistant_final", "ai"
        else:
            return None

        return ParsedCopilotEvent(
            kind=kind,
            role=role,
            content=text,
            message_id=coerce_text(message.get("messageId") or message.get("id")) or None,
            invocation_id=root_message.get("invocationId"),
            socket_id=socket_id,
            direction=direction,
            raw_type=root_message.get("type"),
            timestamp=coerce_text(message.get("timestamp") or message.get("createdAt")) or None,
            payload=message,
        )

    def _looks_like_bot_payload(self, message: dict[str, Any]) -> bool:
        lowered = json.dumps(message, default=str).lower()
        explicit_author_markers = (
            '"author":"assistant"',
            '"author":"bot"',
            '"author":"copilot"',
            '"role":"assistant"',
            '"role":"bot"',
            '"role":"copilot"',
        )
        structural_markers = (
            "sourceattributions",
            "citations",
            "contentorigin",
            "messagekind",
            "messagetype",
            "copilotreferences",
            "grounding",
        )
        return any(token in lowered for token in (*explicit_author_markers, *structural_markers))

    def _looks_like_thinking(self, message: dict[str, Any], text: str) -> bool:
        lowered = json.dumps(message, default=str).lower()
        if any(token in lowered for token in ("thinking", "reasoning", "chainofthought", "chain_of_thought", "reasoned")):
            return True
        preview = text.strip().lower()
        return preview.startswith("reasoned for ") or preview.startswith("thinking")