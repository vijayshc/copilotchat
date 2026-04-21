"""Shared constants for Playwright-based Copilot automation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

DEFAULT_COPILOT_URL = "https://m365.cloud.microsoft/chat/"
CHAT_HUB_URL_FRAGMENT = "/m365Copilot/Chathub/"
RECORD_SEPARATOR = "\x1e"

_TRUE_ENV_VALUES = {"1", "true", "yes", "on", "y"}
_FALSE_ENV_VALUES = {"0", "false", "no", "off", "n"}


def get_boolean_env_flag(name: str, default: bool = False) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if not normalized:
        return default
    if normalized in _TRUE_ENV_VALUES:
        return True
    if normalized in _FALSE_ENV_VALUES:
        return False
    return default

SUPPORTED_PLAYWRIGHT_BROWSERS = ("firefox", "chrome")
DEFAULT_PLAYWRIGHT_BROWSER = "firefox"
_PLAYWRIGHT_BROWSER_ALIASES = {
    "firefox": "firefox",
    "ff": "firefox",
    "chrome": "chrome",
    "chromium": "chrome",
}


def get_playwright_browser_name(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return DEFAULT_PLAYWRIGHT_BROWSER
    normalized = _PLAYWRIGHT_BROWSER_ALIASES.get(raw)
    if normalized is None:
        supported = ", ".join(SUPPORTED_PLAYWRIGHT_BROWSERS)
        raise ValueError(f"Unsupported COPILOT_BROWSER {value!r}. Supported values: {supported}")
    return normalized


def _default_firefox_profile_dir() -> str:
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base_dir = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return str(base_dir / "copilot-firefox-profile")
    return str(Path.home() / ".playwright-firefox-profile")


def _default_chrome_profile_dir() -> str:
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base_dir = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return str(base_dir / "copilot-chrome-profile")
    return str(Path.home() / ".playwright-chrome-profile")


DEFAULT_FIREFOX_PROFILE_DIR = _default_firefox_profile_dir()
DEFAULT_CHROME_PROFILE_DIR = _default_chrome_profile_dir()


def default_profile_dir_for_browser(browser_name: str) -> str:
    normalized_browser = get_playwright_browser_name(browser_name)
    if normalized_browser == "chrome":
        return DEFAULT_CHROME_PROFILE_DIR
    return DEFAULT_FIREFOX_PROFILE_DIR


DEFAULT_PLAYWRIGHT_PROFILE_DIR = default_profile_dir_for_browser(DEFAULT_PLAYWRIGHT_BROWSER)


def resolve_browser_profile_dir(browser_name: str, explicit_profile: str | None = None) -> str:
    if explicit_profile:
        return explicit_profile
    normalized_browser = get_playwright_browser_name(browser_name)
    if normalized_browser == "chrome":
        configured = (os.environ.get("COPILOT_CHROME_PROFILE") or "").strip()
    else:
        configured = (os.environ.get("COPILOT_FIREFOX_PROFILE") or "").strip()
    if configured:
        return configured
    shared_profile = (os.environ.get("COPILOT_BROWSER_PROFILE") or "").strip()
    if shared_profile:
        return shared_profile
    return default_profile_dir_for_browser(normalized_browser)

TEXTBOX_SELECTORS = [
    "[data-testid='chatQuestion'] [role='textbox']",
    "[data-testid='chatQuestion'] [contenteditable='true']",
    "[data-testid='chatQuestion'] textarea",
    "[data-testid='bizchat-input-section'] [role='textbox']",
    "[data-testid='bizchat-input-section'] [contenteditable='true']",
    "[data-testid='bizchat-input-section'] textarea",
    "textarea",
    "[role='textbox']",
    "[contenteditable='true']",
]

SEND_BUTTON_SELECTORS = [
    "[data-testid='fai-SendButton']",
    "[data-testid='fai-send-button']",
    "button[aria-label='Send']",
    "button[type='submit']",
]

DEFAULT_FIREFOX_PREFS: dict[str, Any] = {
    "browser.aboutConfig.showWarning": False,
    "browser.download.panel.shown": True,
    "browser.shell.checkDefaultBrowser": False,
    "browser.startup.homepage_override.mstone": "ignore",
    "browser.tabs.warnOnClose": False,
    "datareporting.healthreport.uploadEnabled": False,
    "dom.webnotifications.enabled": False,
    "media.autoplay.default": 0,
    "toolkit.telemetry.reportingpolicy.firstRun": False,
}

BENIGN_PAGE_ERROR_SNIPPETS = (
    "hasAttribute is not a function",
    "ResizeObserver loop completed with undelivered notifications",
)