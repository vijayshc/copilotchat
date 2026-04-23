"""SignalR protocol parsing and Copilot event extraction."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .constants import RECORD_SEPARATOR
from .helpers import coerce_text, decode_transport_text
from .models import ParsedCopilotEvent


logger = logging.getLogger(__name__)

_ASSISTANT_AUTHORS = {"assistant", "bot", "copilot"}
# messageType values that are internal/system signals and must never be treated as response text
_NOISE_MESSAGE_TYPES = frozenset({"referenceslistcomplete", "escapehatch"})


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
    """Extract Copilot events using the real SignalR message shapes Copilot sends."""

    def extract_events(
        self,
        message: dict[str, Any],
        direction: str,
        socket_id: Optional[str] = None,
    ) -> list[ParsedCopilotEvent]:
        if direction != "received":
            return []

        message_type = message.get("type")
        if message_type == 1:
            return self._extract_update_events(message, direction, socket_id)
        if message_type == 2:
            return self._extract_invocation_result_events(message, direction, socket_id)
        if message_type == 3:
            return [self._protocol_event("completion", message, direction, socket_id)]
        if message_type == 7:
            return [self._protocol_event("socket_close", message, direction, socket_id)]
        return []

    def _extract_update_events(
        self,
        message: dict[str, Any],
        direction: str,
        socket_id: Optional[str],
    ) -> list[ParsedCopilotEvent]:
        events: list[ParsedCopilotEvent] = []
        for update in message.get("arguments") or []:
            if not isinstance(update, dict):
                continue

            # Only process messages[] when a cursor is present alongside them.
            # The cursor field establishes the rendering position and marks the real
            # first content snapshot. Updates that lack a cursor are pre-response
            # system messages (EscapeHatch, thinking/planning artifacts, nonce-only
            # acks, etc.) and must be ignored to avoid polluting the response.
            if update.get("cursor") is not None:
                for item in update.get("messages") or []:
                    event = self._assistant_message_event(
                        message=item,
                        root_message=message,
                        direction=direction,
                        socket_id=socket_id,
                        kind="assistant_delta",
                        streaming_mode="snapshot",
                    )
                    if event is not None:
                        events.append(event)

            fragment = decode_transport_text(coerce_text(update.get("writeAtCursor")))
            if fragment:
                is_thinking = self._looks_like_thinking(update, fragment)
                events.append(
                    ParsedCopilotEvent(
                        kind="thinking_delta" if is_thinking else "assistant_delta",
                        role="thinking" if is_thinking else "ai",
                        content=fragment,
                        invocation_id=coerce_text(message.get("invocationId")) or None,
                        socket_id=socket_id,
                        direction=direction,
                        streaming_mode="delta",
                        raw_type=1,
                        payload=update,
                    )
                )
        return events

    def _extract_invocation_result_events(
        self,
        message: dict[str, Any],
        direction: str,
        socket_id: Optional[str],
    ) -> list[ParsedCopilotEvent]:
        item = message.get("item")
        if not isinstance(item, dict):
            return []

        assistant_message = self._select_final_assistant_message(item)
        if assistant_message is not None:
            event = self._assistant_message_event(
                message=assistant_message,
                root_message=message,
                direction=direction,
                socket_id=socket_id,
                kind="assistant_final",
            )
            if event is not None:
                return [event]

        result = item.get("result")
        if isinstance(result, dict):
            text = decode_transport_text(coerce_text(result.get("message")))
            if not text:
                candidate = decode_transport_text(coerce_text(result.get("value")))
                if self._is_meaningful_result_text(candidate):
                    text = candidate
            if text:
                return [
                    ParsedCopilotEvent(
                        kind="assistant_final",
                        role="ai",
                        content=text,
                        invocation_id=coerce_text(message.get("invocationId")) or None,
                        socket_id=socket_id,
                        direction=direction,
                        raw_type=2,
                        payload=result,
                    )
                ]

        return []

    def _assistant_message_event(
        self,
        *,
        message: Any,
        root_message: dict[str, Any],
        direction: str,
        socket_id: Optional[str],
        kind: str,
        streaming_mode: Optional[str] = None,
    ) -> Optional[ParsedCopilotEvent]:
        if not isinstance(message, dict):
            return None
        if coerce_text(message.get("messageType")).strip().lower() in _NOISE_MESSAGE_TYPES:
            return None
        if self._message_author(message) not in _ASSISTANT_AUTHORS:
            return None

        text = self._extract_message_text(message)
        if not text:
            return None

        event_kind = kind
        role = "ai"
        if self._looks_like_thinking(message, text):
            event_kind = "thinking_delta" if kind == "assistant_delta" else "thinking_final"
            role = "thinking"

        return ParsedCopilotEvent(
            kind=event_kind,
            role=role,
            content=text,
            message_id=coerce_text(message.get("messageId") or message.get("id")) or None,
            invocation_id=coerce_text(root_message.get("invocationId")) or None,
            socket_id=socket_id,
            direction=direction,
            streaming_mode=streaming_mode,
            raw_type=root_message.get("type"),
            timestamp=coerce_text(message.get("timestamp") or message.get("createdAt")) or None,
            payload=message,
        )

    def _select_final_assistant_message(self, item: dict[str, Any]) -> Optional[dict[str, Any]]:
        messages = item.get("messages")
        if not isinstance(messages, list) or not messages:
            return None

        start_index = self._resolve_first_new_index(item.get("firstNewMessageIndex"), len(messages))
        best_message: Optional[dict[str, Any]] = None
        best_text_length = -1
        for raw_message in messages[start_index:]:
            if not isinstance(raw_message, dict):
                continue
            if self._message_author(raw_message) not in _ASSISTANT_AUTHORS:
                continue
            text = self._extract_message_text(raw_message)
            if not text:
                continue
            if len(text) > best_text_length:
                best_message = raw_message
                best_text_length = len(text)
        return best_message

    @staticmethod
    def _resolve_first_new_index(raw_index: Any, total_messages: int) -> int:
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            return 0
        return max(0, min(index, total_messages))

    @staticmethod
    def _message_author(message: dict[str, Any]) -> str:
        return coerce_text(message.get("author") or message.get("role")).strip().lower()

    def _extract_message_text(self, message: dict[str, Any]) -> str:
        for key in ("text", "content", "message", "body", "displayText"):
            text = decode_transport_text(coerce_text(message.get(key)))
            if text:
                return text

        adaptive_text = decode_transport_text(coerce_text(message.get("adaptiveCards")))
        if adaptive_text:
            return adaptive_text
        return ""

    def _protocol_event(
        self,
        kind: str,
        message: dict[str, Any],
        direction: str,
        socket_id: Optional[str],
    ) -> ParsedCopilotEvent:
        return ParsedCopilotEvent(
            kind=kind,
            invocation_id=coerce_text(message.get("invocationId")) or None,
            socket_id=socket_id,
            direction=direction,
            raw_type=message.get("type"),
            payload=message,
        )

    @staticmethod
    def _is_meaningful_result_text(text: str) -> bool:
        normalized = (text or "").strip().lower()
        if not normalized:
            return False
        if normalized in {"success", "ok", "completed", "done"}:
            return False
        return True

    def _looks_like_thinking(self, message: dict[str, Any], text: str) -> bool:
        lowered = json.dumps(message, default=str).lower()
        if any(token in lowered for token in ("thinking", "reasoning", "chainofthought", "chain_of_thought", "reasoned")):
            return True
        preview = text.strip().lower()
        return preview.startswith("reasoned for ") or preview.startswith("thinking")