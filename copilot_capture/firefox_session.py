"""Async Playwright browser session that drives Copilot and listens to WebSocket frames."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable, Optional

from playwright.async_api import (
    BrowserContext,
    Dialog,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    WebSocket,
    async_playwright,
)

from .constants import (
    BENIGN_PAGE_ERROR_SNIPPETS,
    CHAT_HUB_URL_FRAGMENT,
    DEFAULT_PLAYWRIGHT_BROWSER,
    DEFAULT_FIREFOX_PREFS,
    get_boolean_env_flag,
    get_playwright_browser_name,
)
from .helpers import normalize_line_endings, sanitize_url
from .models import ParsedCopilotEvent, TurnContext
from .page_actions import send_message_fast, wait_for_chat_ready
from .page_targeting import is_auth_flow_url, score_page_url, should_navigate_selected_page
from .signalr import CopilotSignalRExtractor, SignalRProtocolParser
from .state import ConversationState


SOCKET_CLOSE_DRAIN_SECONDS = 0.5
POST_COMPLETION_GRACE_SECONDS = 0.15


class FirefoxCopilotSession:
    """Async session that launches a Playwright browser and forwards Copilot messages."""

    def __init__(
        self,
        *,
        copilot_url: str,
        user_data_dir: Path,
        browser_name: str = DEFAULT_PLAYWRIGHT_BROWSER,
        headless: bool,
        login_timeout: float,
        launch_timeout: float,
        logger: logging.Logger,
    ) -> None:
        self.copilot_url = copilot_url
        self.user_data_dir = user_data_dir
        self.browser_name = get_playwright_browser_name(browser_name)
        self.headless = headless
        self.login_timeout = login_timeout
        self.launch_timeout = launch_timeout
        self.logger = logger
        self.login_username = os.environ.get("COPILOT_USERNAME", "").strip()
        self.login_password = os.environ.get("COPILOT_PASSWORD", "")
        self.auto_login_enabled = get_boolean_env_flag("COPILOT_AUTO_LOGIN", default=False)
        self._manual_login_hint_logged = False

        if not self.auto_login_enabled and (self.login_username or self.login_password):
            self.logger.info(
                "Automatic login is disabled (COPILOT_AUTO_LOGIN=false); provided credentials will not be used"
            )

        self.playwright = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.state = ConversationState(logger=logger)

        self._parser = SignalRProtocolParser()
        self._extractor = CopilotSignalRExtractor()
        self._conversation_lock = asyncio.Lock()
        self._attached_page_ids: set[int] = set()
        self._tracked_websocket_urls: set[str] = set()
        self._websocket_counter = 0
        self._socket_frame_queues: dict[str, asyncio.Queue[tuple[str, str | bytes | None] | None]] = {}
        self._socket_frame_tasks: dict[str, asyncio.Task[None]] = {}
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

    async def ensure_started(self) -> None:
        if self.context is not None:
            return
        self.logger.info(
            "Launching %s with persistent profile at %s",
            self._browser_display_name(),
            self.user_data_dir,
        )
        self.playwright = await async_playwright().start()
        self.context = await self._launch_persistent_context()
        self.context.on("page", lambda page: asyncio.create_task(self._attach_page(page)))
        if self.context.pages:
            for existing_page in self.context.pages:
                await self._attach_page(existing_page)
        else:
            await self._attach_page(await self.context.new_page())

        await self._select_copilot_page(allow_open=True)

    async def _launch_persistent_context(self) -> BrowserContext:
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(self.user_data_dir),
            "accept_downloads": True,
            "headless": self.headless,
            "locale": "en-US",
            "no_viewport": True,
            "timeout": self.launch_timeout * 1000,
        }

        if self.browser_name == "firefox":
            return await self.playwright.firefox.launch_persistent_context(
                firefox_user_prefs=DEFAULT_FIREFOX_PREFS,
                **launch_kwargs,
            )

        chrome_channel = (os.environ.get("COPILOT_CHROME_CHANNEL") or "chrome").strip()
        if chrome_channel:
            launch_kwargs["channel"] = chrome_channel
        try:
            return await self.playwright.chromium.launch_persistent_context(**launch_kwargs)
        except PlaywrightError:
            if chrome_channel:
                self.logger.warning(
                    "Could not launch Playwright chromium with channel '%s'; retrying with bundled chromium",
                    chrome_channel,
                )
                launch_kwargs.pop("channel", None)
                return await self.playwright.chromium.launch_persistent_context(**launch_kwargs)
            raise

    def _browser_display_name(self) -> str:
        return "Firefox" if self.browser_name == "firefox" else "Chrome"

    async def start_new_chat(self) -> None:
        await self.ensure_started()
        if self.context is None:
            raise RuntimeError("Playwright browser context is not started")
        await self._select_copilot_page(allow_open=True)
        if self.page is None:
            raise RuntimeError("No active Copilot page is available to reset")
        await self.page.bring_to_front()
        if await self._start_new_chat_in_current_page(self.page):
            self.logger.info("Started a fresh Copilot chat in the current browser page")
            return
        await self.page.goto(self.copilot_url, wait_until="domcontentloaded")
        await self.page.bring_to_front()
        self.logger.info("Reset Copilot chat by navigating the current browser page to %s", self.copilot_url)

    async def close(self) -> None:
        if self.context is None:
            return
        try:
            await self.context.close()
        finally:
            await self._stop_socket_processors()
            self.context = None
            self.page = None
            self._attached_page_ids.clear()
            self._tracked_websocket_urls.clear()
            if self.playwright is not None:
                await self.playwright.stop()
                self.playwright = None

    async def send_message_and_wait(self, message: str, timeout: float) -> str:
        await self.ensure_started()
        async with self._conversation_lock:
            turn = self.state.begin_turn(message)
            try:
                await self._wait_for_chat_ready()
                await self._send_message_fast(message)
                return await self._await_final_answer(turn, timeout)
            finally:
                self.state.finalize_turn(turn)

    async def stream_response(self, message: str, timeout: float, emit: Callable[[str, Any], None]) -> None:
        await self.ensure_started()
        async with self._conversation_lock:
            turn = self.state.begin_turn(message)
            try:
                await self._wait_for_chat_ready()
                await self._send_message_fast(message)
                await self._stream_turn(turn, timeout, emit)
            except Exception as exc:
                emit("error", exc)
                raise
            finally:
                self.state.finalize_turn(turn)

    # ------------------------------------------------------------------ response collection

    async def _await_final_answer(self, turn: TurnContext, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for AI response")
            event = await asyncio.wait_for(turn.event_queue.get(), timeout=remaining)
            if event.kind in {"assistant_final", "assistant_delta", "user_message"}:
                continue
            if event.kind in {"completion", "socket_close"}:
                # Drain any trailing events quickly
                await self._drain_briefly(turn, deadline)
                answer = turn.response_text or turn.full_text or turn.stream_text
                if answer.strip():
                    if self._looks_like_prompt_echo(turn, answer):
                        raise TimeoutError("Copilot echoed the prompt without producing an assistant answer")
                    return answer
                raise TimeoutError("Copilot connection completed without a response body")

    async def _drain_briefly(self, turn: TurnContext, deadline: float) -> None:
        drain_until = min(deadline, time.monotonic() + POST_COMPLETION_GRACE_SECONDS)
        while True:
            remaining = drain_until - time.monotonic()
            if remaining <= 0:
                return
            try:
                await asyncio.wait_for(turn.event_queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return

    async def _stream_turn(self, turn: TurnContext, timeout: float, emit: Callable[[str, Any], None]) -> None:
        deadline = time.monotonic() + timeout
        last_snapshot = ""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for streamed AI response")
            event = await asyncio.wait_for(turn.event_queue.get(), timeout=remaining)
            if event.kind in {"assistant_delta", "assistant_final"}:
                snapshot = turn.response_text or turn.full_text or turn.stream_text or event.content
                last_snapshot = self._emit_snapshot(emit, snapshot, last_snapshot)
            elif event.kind in {"completion", "socket_close"}:
                # Drain remaining events quickly, then finish
                await self._drain_stream_tail(turn, deadline, emit, last_snapshot)
                emit("done", None)
                return
            # user_message, thinking events — skip

    async def _drain_stream_tail(
        self,
        turn: TurnContext,
        deadline: float,
        emit: Callable[[str, Any], None],
        last_snapshot: str,
    ) -> None:
        drain_until = min(deadline, time.monotonic() + POST_COMPLETION_GRACE_SECONDS)
        while True:
            remaining = drain_until - time.monotonic()
            if remaining <= 0:
                break
            try:
                event = await asyncio.wait_for(turn.event_queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if event.kind in {"assistant_delta", "assistant_final"}:
                snapshot = turn.response_text or turn.full_text or turn.stream_text or event.content
                last_snapshot = self._emit_snapshot(emit, snapshot, last_snapshot)
        # Final snapshot
        final = turn.response_text or turn.full_text or turn.stream_text
        if final and final != last_snapshot:
            emit("snapshot", final)

    @staticmethod
    def _emit_snapshot(emit: Callable[[str, Any], None], snapshot: str, last_snapshot: str) -> str:
        if not snapshot or snapshot == last_snapshot:
            return last_snapshot
        emit("snapshot", snapshot)
        return snapshot

    @staticmethod
    def _looks_like_prompt_echo(turn: TurnContext, content: str) -> bool:
        prompt = FirefoxCopilotSession._normalize_echo_candidate(turn.prompt)
        answer = FirefoxCopilotSession._normalize_echo_candidate(content)
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

    # ------------------------------------------------------------------ page management

    async def _attach_page(self, page: Page) -> None:
        page_id = id(page)
        if page_id in self._attached_page_ids:
            return
        self._attached_page_ids.add(page_id)
        page.on("websocket", self._handle_websocket)
        page.on("pageerror", self._handle_page_error)
        page.on("dialog", lambda dialog: asyncio.create_task(self._dismiss_dialog(dialog)))
        try:
            page_url = page.url or "about:blank"
        except PlaywrightError:
            page_url = "about:blank"
        self.logger.info("Attached to %s page: %s", self._browser_display_name(), page_url)
        if self.page is None:
            self.page = page

    def _handle_websocket(self, ws: WebSocket) -> None:
        if CHAT_HUB_URL_FRAGMENT not in ws.url:
            return
        sanitized = sanitize_url(ws.url)
        self._websocket_counter += 1
        socket_id = f"ws-{self._websocket_counter}"
        self._tracked_websocket_urls.add(sanitized)
        self._socket_frame_queues[socket_id] = asyncio.Queue()
        self._socket_frame_tasks[socket_id] = asyncio.create_task(self._consume_socket_frames(socket_id))
        self.logger.info("Tracking Copilot WebSocket %s: %s", socket_id, sanitized)
        ws.on("framereceived", lambda payload: self._enqueue_socket_frame_nowait("received", payload, socket_id))
        ws.on("framesent", lambda payload: self._enqueue_socket_frame_nowait("sent", payload, socket_id))
        ws.on("socketerror", lambda error: self.logger.warning("WebSocket error on %s: %s", sanitized, error))
        ws.on("close", lambda *_: asyncio.create_task(self._mark_socket_closing(socket_id)))

    def _enqueue_socket_frame_nowait(self, direction: str, payload: str | bytes, socket_id: str) -> None:
        queue = self._socket_frame_queues.get(socket_id)
        if queue is None:
            return
        queue.put_nowait((direction, payload))

    async def _consume_socket_frames(self, socket_id: str) -> None:
        queue = self._socket_frame_queues[socket_id]
        closing = False
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=SOCKET_CLOSE_DRAIN_SECONDS) if closing else await queue.get()
            except asyncio.TimeoutError:
                await self._queue_socket_close(socket_id)
                return
            if item is None:
                return
            direction, payload = item
            if direction == "__close__":
                closing = True
                continue
            await self._process_websocket_frame(direction, payload, socket_id)

    async def _mark_socket_closing(self, socket_id: str) -> None:
        queue = self._socket_frame_queues.get(socket_id)
        if queue is None:
            return
        await queue.put(("__close__", None))

    async def _stop_socket_processors(self) -> None:
        for socket_id, queue in list(self._socket_frame_queues.items()):
            await queue.put(None)
        tasks = list(self._socket_frame_tasks.values())
        self._socket_frame_queues.clear()
        self._socket_frame_tasks = {}
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    def _handle_page_error(self, error: Exception) -> None:
        message = str(error)
        if any(snippet in message for snippet in BENIGN_PAGE_ERROR_SNIPPETS):
            return
        self.logger.warning("Page error: %s", message)

    async def _dismiss_dialog(self, dialog: Dialog) -> None:
        try:
            await dialog.dismiss()
        except PlaywrightError:
            pass

    async def _process_websocket_frame(self, direction: str, payload: str | bytes, socket_id: str) -> None:
        records = self._parser.parse_frame(payload)
        if direction == "sent":
            self._bind_turn_from_sent_records(socket_id, records)
        for record in records:
            for event in self._extractor.extract_events(record, direction, socket_id=socket_id):
                await self.state.handle_event(event)

    def _bind_turn_from_sent_records(self, socket_id: str, records: list[dict[str, Any]]) -> None:
        turn = self.state.current_turn
        if turn is None:
            return
        meaningful_records = [record for record in records if record.get("type") not in {6, 7}]
        if not meaningful_records:
            return
        turn.socket_id = turn.socket_id or socket_id
        if turn.invocation_id is None:
            for record in meaningful_records:
                invocation_id = record.get("invocationId")
                if invocation_id:
                    turn.invocation_id = str(invocation_id)
                    break

    async def _queue_socket_close(self, socket_id: str) -> None:
        turn = self.state.current_turn
        if turn is not None:
            await self.state.handle_event(ParsedCopilotEvent(kind="socket_close", socket_id=socket_id))

    async def _select_copilot_page(self, allow_open: bool = True) -> None:
        if self.context is None:
            raise RuntimeError("Playwright browser context is not started")
        if self.page is not None:
            try:
                if self.page in self.context.pages:
                    await self.page.bring_to_front()
                    if should_navigate_selected_page(self.page.url or "", self.copilot_url):
                        await self.page.goto(self.copilot_url, wait_until="domcontentloaded")
                    return
            except PlaywrightError:
                self.page = None
        selected_page: Optional[Page] = None
        best_score = -1
        for candidate in self.context.pages:
            page_url = candidate.url or ""
            score = score_page_url(page_url, self.copilot_url)
            if score > best_score:
                best_score = score
                selected_page = candidate
        if selected_page is not None:
            self.page = selected_page
            await self.page.bring_to_front()
            if should_navigate_selected_page(self.page.url or "", self.copilot_url):
                await self.page.goto(self.copilot_url, wait_until="domcontentloaded")
            return
        if allow_open:
            self.page = await self.context.new_page()
            await self._attach_page(self.page)
            await self.page.goto(self.copilot_url, wait_until="domcontentloaded")
            await self.page.bring_to_front()

    async def _wait_for_chat_ready(self) -> None:
        if self.page is None:
            await self._select_copilot_page(allow_open=True)
        assert self.page is not None
        deadline = time.monotonic() + self.login_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Timed out waiting for the Copilot chat page to become ready")
            try:
                await wait_for_chat_ready(self.page, self.copilot_url, min(remaining, 10.0))
                self._manual_login_hint_logged = False
                return
            except Exception:
                if self.auto_login_enabled and await self._maybe_complete_login_flow(min(remaining, 8.0)):
                    await asyncio.sleep(0.15)
                    continue
                if is_auth_flow_url(self.page.url or ""):
                    if not self.auto_login_enabled and not self._manual_login_hint_logged:
                        self.logger.info(
                            "Detected Microsoft sign-in page. Automatic login is disabled; complete sign-in manually in the browser"
                        )
                        self._manual_login_hint_logged = True
                    await asyncio.sleep(0.15)
                    continue
                raise

    async def _send_message_fast(self, message: str) -> None:
        assert self.page is not None
        await send_message_fast(self.page, message, self.logger)

    async def _start_new_chat_in_current_page(self, page: Page) -> bool:
        candidates = [
            page.get_by_role("button", name=re.compile(r"new\s+(chat|conversation)", re.IGNORECASE)),
            page.get_by_role("link", name=re.compile(r"new\s+(chat|conversation)", re.IGNORECASE)),
            page.locator("[data-testid='new-chat-button']").first,
            page.locator("[data-testid='newChatButton']").first,
            page.locator("button[aria-label*='New chat' i]").first,
            page.locator("a[aria-label*='New chat' i]").first,
            page.locator("button:has-text('New chat')").first,
            page.locator("a:has-text('New chat')").first,
            page.locator("button:has-text('New conversation')").first,
            page.locator("a:has-text('New conversation')").first,
        ]
        for locator in candidates:
            if await self._click_if_visible(locator.first if hasattr(locator, "first") else locator, 1_000):
                await asyncio.sleep(0.3)
                return True
        return False

    # ------------------------------------------------------------------ login helpers

    async def _maybe_complete_login_flow(self, timeout_seconds: float) -> bool:
        assert self.page is not None
        page = self.page
        action_timeout = max(200, int(min(timeout_seconds, 1.0) * 1000))

        if await self._click_saved_account_tile(page, action_timeout):
            self.logger.info("Selected saved Microsoft account from account picker")
            return True

        if await self._click_if_visible(page.locator("text=/Use another account/i").first, action_timeout):
            self.logger.info("Clicked 'Use another account' during Microsoft sign-in")
            return True

        if await self._click_if_visible(page.get_by_role("link", name="Use password instead"), action_timeout):
            self.logger.info("Switched Microsoft sign-in flow to password entry")
            return True

        password_box = page.locator(
            "input[name='passwd'], input[type='password'], input[placeholder*='Password' i]"
        ).first
        if await self._is_visible(password_box, action_timeout):
            password_value = await self._read_input_value(password_box)
            if self.login_password:
                await password_box.fill(self.login_password, timeout=action_timeout)
                password_value = self.login_password
            elif not password_value:
                return False
            if not await self._click_primary_submit(page, action_timeout):
                await password_box.press("Enter")
            self.logger.info("Submitted Microsoft password for automatic sign-in")
            return True

        email_box = page.locator("input[name='loginfmt'], input[type='email']").first
        if await self._is_visible(email_box, action_timeout):
            email_value = await self._read_input_value(email_box)
            if self.login_username:
                await email_box.fill(self.login_username, timeout=action_timeout)
                email_value = self.login_username
            elif not email_value:
                return False
            if not await self._click_primary_submit(page, action_timeout):
                await email_box.press("Enter")
            self.logger.info("Submitted Microsoft username for automatic sign-in")
            return True

        stay_signed_in_yes = page.locator("input[value='Yes'], button:has-text('Yes'), #idSIButton9").first
        if await self._click_if_visible(stay_signed_in_yes, action_timeout):
            self.logger.info("Accepted Microsoft 'Stay signed in' prompt")
            return True

        stay_signed_in_no = page.locator("input[value='No'], button:has-text('No'), #idBtn_Back").first
        if await self._click_if_visible(stay_signed_in_no, action_timeout):
            self.logger.info("Dismissed Microsoft 'Stay signed in' prompt")
            return True

        return False

    async def _read_input_value(self, locator) -> str:
        try:
            value = await locator.input_value(timeout=400)
        except (PlaywrightTimeoutError, PlaywrightError):
            return ""
        return value.strip()

    async def _click_saved_account_tile(self, page: Page, timeout_ms: int) -> bool:
        picker_timeout = min(timeout_ms, 200)
        picker_markers = [
            page.get_by_text("Pick an account", exact=False).first,
            page.locator("text=/Use another account/i").first,
        ]
        picker_found = False
        for marker in picker_markers:
            if await self._is_visible(marker, picker_timeout):
                picker_found = True
                break
        if not picker_found:
            return False

        escaped_username = self.login_username.replace("'", "\\'")
        account_name_pattern = re.compile(re.escape(self.login_username), re.IGNORECASE)
        candidates = [
            page.get_by_role("button", name=account_name_pattern),
            page.get_by_role("link", name=account_name_pattern),
            page.locator(f"[role='button']:has-text('{escaped_username}')").first,
            page.locator(f"[role='link']:has-text('{escaped_username}')").first,
            page.locator(f"div.table:has-text('{escaped_username}')").first,
            page.get_by_text(self.login_username, exact=False).first,
        ]
        for locator in candidates:
            if await self._click_if_visible(locator, timeout_ms):
                return True
        return False

    async def _click_primary_submit(self, page: Page, timeout_ms: int) -> bool:
        candidates = [
            page.locator("#idSIButton9").first,
            page.locator("button[type='submit']").first,
            page.locator("input[type='submit']").first,
            page.get_by_role("button", name="Next"),
            page.get_by_role("button", name="Sign in"),
        ]
        for locator in candidates:
            if await self._click_if_visible(locator, timeout_ms):
                return True
        return False

    async def _click_if_visible(self, locator, timeout_ms: int) -> bool:
        if await self._is_visible(locator, timeout_ms):
            try:
                await locator.click(timeout=timeout_ms)
            except PlaywrightError:
                try:
                    await locator.click(timeout=timeout_ms, force=True)
                except PlaywrightError:
                    handle = await locator.element_handle()
                    if handle is None:
                        return False
                    await handle.evaluate(
                        """el => {
                            el.scrollIntoView({ block: 'center', inline: 'center' });
                            el.click();
                        }"""
                    )
            return True
        return False

    async def _is_visible(self, locator, timeout_ms: int) -> bool:
        try:
            await locator.wait_for(state="visible", timeout=timeout_ms)
            return True
        except (PlaywrightTimeoutError, PlaywrightError):
            return False