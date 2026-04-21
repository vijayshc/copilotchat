"""Helpers for choosing and reusing the right browser page during login and chat."""

from __future__ import annotations

from urllib.parse import urlparse


AUTH_FLOW_HOST_SNIPPETS = (
    "login.microsoftonline.com",
    "login.live.com",
    "device.login.microsoftonline.com",
    "autologon.microsoftazuread-sso.com",
    "duosecurity.com",
)


def is_auth_flow_url(url: str) -> bool:
    normalized = (url or "").strip().lower()
    return any(snippet in normalized for snippet in AUTH_FLOW_HOST_SNIPPETS)


def score_page_url(url: str, copilot_url: str) -> int:
    normalized = (url or "").strip().lower()
    copilot_host = urlparse(copilot_url).netloc.lower()

    if not normalized or normalized.startswith("about:"):
        return 1
    if normalized.startswith(copilot_url.lower()):
        return 10
    if copilot_host and copilot_host in normalized:
        return 9
    if is_auth_flow_url(normalized):
        return 8
    if "copilot.microsoft.com" in normalized:
        return 6
    if "/chat" in normalized or "/chats" in normalized:
        return 4
    return 2


def should_navigate_selected_page(url: str, copilot_url: str) -> bool:
    normalized = (url or "").strip().lower()
    copilot_host = urlparse(copilot_url).netloc.lower()

    if not normalized or normalized.startswith("about:"):
        return True
    if is_auth_flow_url(normalized):
        return False
    if normalized.startswith(copilot_url.lower()):
        return False
    if copilot_host and copilot_host in normalized:
        return False
    if "copilot.microsoft.com" in normalized:
        return False
    return True