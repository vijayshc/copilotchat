"""Lightweight conversation state — routes SignalR events to the active turn."""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

from .helpers import normalize_line_endings
from .models import ParsedCopilotEvent, TurnContext


class ConversationState:
    """Tracks the active turn and routes parsed events into its queue."""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self.current_turn: Optional[TurnContext] = None

    def begin_turn(self, prompt: str) -> TurnContext:
        turn = TurnContext(prompt=prompt)
        self.current_turn = turn
        return turn

    def finalize_turn(self, turn: TurnContext) -> None:
        if self.current_turn is turn:
            self.current_turn = None

    async def handle_event(self, event: ParsedCopilotEvent) -> None:
        turn = self.current_turn
        if turn is None:
            return

        if event.kind == "user_message":
            if turn.user_sent:
                return
            if normalize_line_endings(event.content) != normalize_line_endings(turn.prompt):
                return
            turn.invocation_id = event.invocation_id or turn.invocation_id
            turn.socket_id = event.socket_id or turn.socket_id
            turn.user_sent = True
            await turn.event_queue.put(event)
            return

        if event.kind == "assistant_delta":
            if not self._ready_for_response(turn):
                return
            if not self._matches_turn(event, turn):
                return
            fragment = normalize_line_endings(event.content)
            merged_stream = self._merge_stream_fragment(turn.stream_text, fragment, event.streaming_mode)
            if self._looks_like_prompt_echo(turn, merged_stream):
                return
            turn.stream_text = merged_stream
            if not turn.full_text:
                turn.response_text = turn.stream_text
            await turn.event_queue.put(event)
            return

        if event.kind == "assistant_final":
            if not self._ready_for_response(turn):
                return
            if not self._matches_turn(event, turn):
                return
            content = normalize_line_endings(event.content)
            if self._looks_like_prompt_echo(turn, content):
                return
            if event.raw_type == 2:
                turn.full_text = content
            else:
                turn.full_text = self._prefer_longer_text(turn.full_text, content)
            turn.response_text = turn.full_text or self._prefer_longer_text(turn.response_text, content)
            turn.assistant_message_id = event.message_id or turn.assistant_message_id
            await turn.event_queue.put(event)
            return

        if event.kind in {"completion", "socket_close"}:
            if not (turn.response_text or turn.stream_text or turn.full_text or turn.invocation_id or turn.socket_id):
                return
            if turn.invocation_id and event.invocation_id and turn.invocation_id != event.invocation_id:
                return
            if turn.socket_id and event.socket_id and turn.socket_id != event.socket_id:
                return
            await turn.event_queue.put(event)

        # thinking_delta / thinking_final — ignored (not forwarded to client)

    # ------------------------------------------------------------------

    def _ready_for_response(self, turn: TurnContext) -> bool:
        return turn.user_sent or turn.invocation_id is not None or turn.socket_id is not None

    def _matches_turn(self, event: ParsedCopilotEvent, turn: TurnContext) -> bool:
        if turn.invocation_id and event.invocation_id and turn.invocation_id != event.invocation_id:
            return False
        if turn.socket_id and event.socket_id and turn.socket_id != event.socket_id:
            return False
        turn.invocation_id = turn.invocation_id or event.invocation_id
        turn.socket_id = turn.socket_id or event.socket_id
        return True

    @staticmethod
    def _looks_like_prompt_echo(turn: TurnContext, content: str) -> bool:
        prompt = ConversationState._normalize_echo_candidate(turn.prompt)
        answer = ConversationState._normalize_echo_candidate(content)
        if not prompt or not answer:
            return False
        if answer == prompt:
            return True
        if prompt in answer and len(answer) <= len(prompt) + 20:
            return True
        if answer.startswith(prompt) and len(answer) <= len(prompt) + 4:
            return True
        if answer.endswith(prompt) and len(answer) <= len(prompt) + 4:
            return True
        return False

    @staticmethod
    def _normalize_echo_candidate(text: str) -> str:
        normalized = normalize_line_endings(text or "")
        filtered = "".join(ch for ch in normalized if unicodedata.category(ch) != "Cf")
        return re.sub(r"\s+", " ", filtered).strip()

    @staticmethod
    def _merge_stream_fragment(existing: str, fragment: str, streaming_mode: Optional[str]) -> str:
        if not existing:
            return fragment

        mode = (streaming_mode or "delta").lower()
        if mode == "delta":
            if fragment.startswith(existing):
                return fragment
            if existing.startswith(fragment):
                return existing
            overlap = ConversationState._find_overlap(existing, fragment)
            if overlap > 0:
                return existing + fragment[overlap:]
            return existing + fragment

        if fragment.startswith(existing) or len(fragment) >= len(existing):
            return fragment
        return existing

    @staticmethod
    def _prefer_longer_text(existing: str, incoming: str) -> str:
        existing = normalize_line_endings(existing)
        incoming = normalize_line_endings(incoming)
        if not existing:
            return incoming
        if not incoming:
            return existing
        if incoming.startswith(existing) or len(incoming) >= len(existing):
            return incoming
        return existing

    @staticmethod
    def _find_overlap(existing: str, incoming: str) -> int:
        max_overlap = min(len(existing), len(incoming))
        for size in range(max_overlap, 0, -1):
            if existing[-size:] == incoming[:size]:
                return size
        return 0
