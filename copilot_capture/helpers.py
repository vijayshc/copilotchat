"""Utility helpers shared across capture modules."""

from __future__ import annotations

import datetime as dt
import unicodedata
from typing import Any, Iterator, Optional
from urllib.parse import urlparse, urlunparse


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def normalize_line_endings(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _transport_text_score(text: str) -> int:
    score = 0
    for char in text:
        if char in {"\n", "\r", "\t"}:
            score += 1
            continue
        category = unicodedata.category(char)
        if category == "Cc":
            score -= 8
        elif category.startswith("C"):
            score -= 2
        elif char == "\ufffd":
            score -= 6
        else:
            score += 2
    return score


def _decode_transport_segment(segment: str) -> str:
    if not segment:
        return segment
    best = segment
    best_score = _transport_text_score(segment)
    for codec in ("latin-1", "cp1252"):
        try:
            candidate = segment.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        candidate_score = _transport_text_score(candidate)
        if candidate_score > best_score:
            best = candidate
            best_score = candidate_score
    return best


def decode_transport_text(text: str) -> str:
    if not text:
        return ""

    parts: list[str] = []
    segment: list[str] = []

    def flush_segment() -> None:
        if segment:
            parts.append(_decode_transport_segment("".join(segment)))
            segment.clear()

    for char in text:
        if ord(char) <= 0xFF:
            segment.append(char)
        else:
            flush_segment()
            parts.append(char)
    flush_segment()
    return "".join(parts)


def safe_next(iterator: Iterator[str], sentinel: object) -> str | object:
    try:
        return next(iterator)
    except StopIteration:
        return sentinel


def sanitize_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def parse_timestamp(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    cleaned = value.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [coerce_text(item) for item in value]
        return "".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "content", "value", "message", "body", "displayText"):
            text = coerce_text(value.get(key))
            if text:
                return text
    return ""