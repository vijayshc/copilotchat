"""Thread-safe synchronous façade around the async Playwright browser session."""

from __future__ import annotations

import atexit
import asyncio
import logging
import signal
import threading
from pathlib import Path
from queue import Queue
from typing import Any, Coroutine, Iterator, Optional

from .constants import (
    DEFAULT_COPILOT_URL,
    DEFAULT_PLAYWRIGHT_BROWSER,
    default_profile_dir_for_browser,
    get_playwright_browser_name,
)
from .firefox_session import FirefoxCopilotSession


class CopilotChatCapture:
    """Simplified message-forwarder: sends prompts to Copilot and returns responses."""

    def __init__(
        self,
        *,
        copilot_url: str = DEFAULT_COPILOT_URL,
        user_data_dir: str | None = None,
        browser_name: str = DEFAULT_PLAYWRIGHT_BROWSER,
        headless: bool = False,
        login_timeout: float = 300.0,
        launch_timeout: float = 60.0,
    ) -> None:
        self.copilot_url = copilot_url
        self.browser_name = get_playwright_browser_name(browser_name)
        resolved_user_data_dir = user_data_dir or default_profile_dir_for_browser(self.browser_name)
        self.user_data_dir = Path(resolved_user_data_dir).expanduser().resolve()
        self.headless = headless
        self.login_timeout = login_timeout
        self.launch_timeout = launch_timeout

        logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
        self.logger = logging.getLogger(__name__)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._loop_ready = threading.Event()
        self._request_lock = threading.RLock()
        self._closed = False
        self._session = FirefoxCopilotSession(
            copilot_url=self.copilot_url,
            user_data_dir=self.user_data_dir,
            browser_name=self.browser_name,
            headless=self.headless,
            login_timeout=self.login_timeout,
            launch_timeout=self.launch_timeout,
            logger=self.logger,
        )
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
        atexit.register(self.close_sync)

    def _signal_handler(self, signum, _frame) -> None:
        self.logger.info("Received signal %s, closing gracefully...", signum)
        self.close_sync()

    def _ensure_worker(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._loop_ready.clear()
        self._thread = threading.Thread(target=self._run_loop, name="copilot-loop", daemon=True)
        self._thread.start()
        self._loop_ready.wait(timeout=10)
        if self._loop is None:
            raise RuntimeError("Failed to start the Copilot worker loop")

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        self._loop.run_forever()
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        if pending:
            self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self._loop.close()

    def _submit(self, coro: Coroutine[Any, Any, Any]):
        self._ensure_worker()
        assert self._loop is not None
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def send_message_and_wait_for_ai_sync(self, message: str, timeout: float = 60.0) -> str:
        with self._request_lock:
            return self._submit(self._session.send_message_and_wait(message, timeout)).result(timeout=timeout + 90)

    def start_new_chat_sync(self, timeout: float = 60.0) -> None:
        with self._request_lock:
            self._submit(self._session.start_new_chat()).result(timeout=timeout + 30)

    def stream_ai_response_sync(self, message: str, timeout: float = 60.0) -> Iterator[tuple[str, Any]]:
        """Yields (kind, payload) tuples: ("snapshot", text) or ("done", None)."""
        with self._request_lock:
            stream_queue: Queue[tuple[str, Any]] = Queue()

            def emit(kind: str, payload: Any) -> None:
                stream_queue.put((kind, payload))

            future = self._submit(self._session.stream_response(message, timeout, emit))
            while True:
                kind, payload = stream_queue.get()
                if kind == "snapshot":
                    yield kind, payload
                elif kind == "done":
                    future.result(timeout=5)
                    break
                elif kind == "error":
                    future.result(timeout=5)
                    if isinstance(payload, Exception):
                        raise payload
                    raise RuntimeError(str(payload))

    def close_sync(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._loop is None:
            return
        try:
            self._submit(self._session.close()).result(timeout=30)
        except Exception as exc:
            self.logger.debug("Close encountered an error: %s", exc)
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._thread = None
        self._loop = None


CopilotChatCaptureCDP = CopilotChatCapture