"""Dataclasses shared across Copilot capture modules."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(slots=True)
class ParsedCopilotEvent:
    kind: str
    role: Optional[str] = None
    content: str = ""
    message_id: Optional[str] = None
    invocation_id: Optional[str] = None
    socket_id: Optional[str] = None
    direction: Optional[str] = None
    streaming_mode: Optional[str] = None
    raw_type: Optional[int] = None
    timestamp: Optional[str] = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TurnContext:
    prompt: str
    started_monotonic: float = field(default_factory=time.monotonic)
    event_queue: asyncio.Queue[ParsedCopilotEvent] = field(default_factory=asyncio.Queue)
    invocation_id: Optional[str] = None
    socket_id: Optional[str] = None
    assistant_message_id: Optional[str] = None
    full_text: str = ""
    stream_text: str = ""
    response_text: str = ""
    user_sent: bool = False