#!/usr/bin/env python3
"""Compatibility shim — standalone CLI for launching Copilot in Playwright browser mode."""

from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv

from copilot_capture.constants import get_playwright_browser_name, resolve_browser_profile_dir
from copilot_capture import CopilotChatCapture, CopilotChatCaptureCDP

load_dotenv()


def main() -> int:
    browser_name = get_playwright_browser_name(os.environ.get("COPILOT_BROWSER"))
    capture = CopilotChatCapture(
        copilot_url=os.environ.get("COPILOT_URL", "https://m365.cloud.microsoft/chat/"),
        user_data_dir=resolve_browser_profile_dir(browser_name),
        browser_name=browser_name,
        headless=os.environ.get("COPILOT_HEADLESS", "false").lower() in {"1", "true", "yes"},
        login_timeout=float(os.environ.get("COPILOT_LOGIN_TIMEOUT", "300")),
        launch_timeout=float(os.environ.get("COPILOT_LAUNCH_TIMEOUT", "60")),
    )
    try:
        # Send a test message and print the response
        response = capture.send_message_and_wait_for_ai_sync("Hello", timeout=60.0)
        print(response)
    except KeyboardInterrupt:
        capture.close_sync()
        return 0
    except Exception:
        capture.close_sync()
        logging.exception("Copilot capture failed")
        return 1
    finally:
        capture.close_sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())