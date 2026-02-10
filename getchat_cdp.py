#!/usr/bin/env python3

import asyncio
import time
import json
import datetime
import logging
import sys
import signal
import threading
from pathlib import Path
from typing import Set, Dict, Any, Optional

from playwright.async_api import async_playwright, Browser, Page, BrowserContext


class CopilotChatCaptureCDP:
    
    def __init__(self, 
                 output_file: str = "copilot_chat_capture.txt",
                 capture_interval: float = 0.5,
                 cdp_endpoint: str = "http://172.27.240.1:9222",
                 auto_select_copilot_tab: bool = True,
                 auto_start_without_prompt: bool = True,
                 self_test_enabled: bool = True,
                 self_test_message: str = "Please reply with a short greeting for capture test.",
                 self_test_timeout: float = 60.0):
        self.output_file = Path(output_file)
        self.capture_interval = capture_interval
        self.cdp_endpoint = cdp_endpoint
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.running = False
        self.auto_select_copilot_tab = auto_select_copilot_tab
        self.auto_start_without_prompt = auto_start_without_prompt
        self.self_test_enabled = self_test_enabled
        self.self_test_message = self_test_message
        self.self_test_timeout = self_test_timeout
        self.user_messages_list = []
        self.ai_messages_list = []
        self.last_user_index_written = -1
        self.last_ai_index_written = -1
        self.message_count = 0
        self.loop_count = 0

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)

        self.output_file.parent.mkdir(parents=True, exist_ok=True)

        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False
    
    async def _connect_to_browser(self) -> tuple[Browser, Page]:
        self.logger.info(f"Connecting to browser via CDP at {self.cdp_endpoint}...")
        
        try:
            playwright = await async_playwright().start()
            
            browser = await playwright.chromium.connect_over_cdp(self.cdp_endpoint)
            contexts = browser.contexts
            if not contexts:
                self.logger.error("No browser contexts found. Make sure Chrome is running with a tab open.")
                raise Exception("No browser contexts found")
            context = contexts[0]
            pages = context.pages
            if not pages:
                self.logger.error("No pages found in browser context. Make sure you have a tab open.")
                raise Exception("No pages found")
            if len(pages) > 0:
                page = pages[0]
                self.logger.info(f"Using existing page: {page.url}")
            else:
                page = await context.new_page()
                self.logger.info("Created new page")

            def _handle_page_error(error):
                message = str(error)
                if "hasAttribute is not a function" in message:
                    self.logger.debug(f"Suppressed page error: {message}")
                    return
                self.logger.error(f"Page error: {message}")

            page.on("error", _handle_page_error)
            page.on("pageerror", _handle_page_error)
            
            self.logger.info("✓ Successfully connected to browser via CDP")
            return browser, page
            
        except Exception as e:
            self.logger.error(f"Failed to connect to browser: {e}")
            self.logger.info("\nTroubleshooting:")
            self.logger.info("1. Make sure Chrome is running with: chrome.exe --remote-debugging-port=9222")
            self.logger.info("2. Check if port 9222 is accessible")
            self.logger.info("3. Ensure Chrome has at least one tab open")
            raise
    
    async def _wait_for_user_ready(self):
        if self.auto_select_copilot_tab:
            await self._select_copilot_page(allow_open=True)

        if not self.auto_start_without_prompt:
            await asyncio.get_event_loop().run_in_executor(None, input)

        await self._wait_for_chat_textbox()

    async def _wait_for_chat_textbox(self, timeout_ms: int = 30000):
        try:
            await self.page.wait_for_selector('[role="textbox"], [contenteditable="true"], textarea, input', timeout=timeout_ms)
        except Exception as e:
            self.logger.error(f"Chat textbox not found within timeout: {e}")
            raise

    async def _find_textbox(self):
        selectors = [
            '[data-testid="chatQuestion"] [role="textbox"]',
            '[data-testid="chatQuestion"] [contenteditable="true"]',
            '[data-testid="chatQuestion"] textarea',
            '[data-testid="chatQuestion"] input[type="text"]',
            '[data-testid="bizchat-input-section"] [role="textbox"]',
            '[data-testid="bizchat-input-section"] [contenteditable="true"]',
            '[role="textbox"]',
            '[contenteditable="true"]',
            'textarea',
            'input[type="text"]'
        ]

        frames = []
        try:
            frames = [self.page] + list(self.page.frames)
        except Exception:
            frames = [self.page]

        for frame in frames:
            for selector in selectors:
                try:
                    element = await frame.query_selector(selector)
                    if element:
                        return frame, element
                except Exception:
                    continue
        return None, None

    async def _set_textbox_value(self, textbox, message: str):
        try:
            await textbox.evaluate(
                """(el, value) => {
                    const isEditable = el.isContentEditable;
                    if (isEditable) {
                        el.focus();
                        el.textContent = value;
                    } else if ('value' in el) {
                        el.focus();
                        el.value = value;
                    }
                    const ev = new Event('input', { bubbles: true });
                    el.dispatchEvent(ev);
                    const ev2 = new Event('change', { bubbles: true });
                    el.dispatchEvent(ev2);
                }""",
                message
            )
        except Exception:
            pass

    async def _is_stop_generating_present(self) -> bool:
        selectors = [
            '[aria-label="Stop generating"]',
            'button[aria-label="Stop generating"]'
        ]
        for selector in selectors:
            try:
                element = await self.page.query_selector(selector)
                if element:
                    return True
            except Exception:
                continue
        return False

    async def _get_loading_message_text(self) -> str:
        try:
            elements = await self.page.query_selector_all('[data-testid="loading-message"]')
            if not elements:
                return ""
            text = await elements[-1].inner_text()
            return text.strip() if text else ""
        except Exception:
            return ""

    async def _is_loading_message_present(self) -> bool:
        try:
            element = await self.page.query_selector('[data-testid="loading-message"]')
            return element is not None
        except Exception:
            return False

    def _normalize_stream_text(self, text: str) -> str:
        if not text:
            return ""

        lines = [line.rstrip() for line in text.splitlines()]
        skip_prefixes = (
            "Copilot",
            "Generating response",
            "Reasoned for",
            "Get a quick answer",
            "You said:",
            "Today",
        )

        cleaned = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                cleaned.append("")
                continue
            if any(stripped.startswith(prefix) for prefix in skip_prefixes):
                continue
            if stripped == ":":
                continue
            cleaned.append(line)

        return "\n".join(cleaned).strip()

    def _longest_common_prefix_len(self, a: str, b: str) -> int:
        if not a or not b:
            return 0
        limit = min(len(a), len(b))
        i = 0
        while i < limit and a[i] == b[i]:
            i += 1
        return i

    async def _select_copilot_page(self, allow_open: bool = True):
        try:
            selected_page = None
            best_score = -1
            for context in self.browser.contexts:
                for page in context.pages:
                    page_url = page.url or ""
                    if page_url.startswith("devtools://"):
                        continue

                    score = 0
                    if "m365.cloud.microsoft/chat" in page_url:
                        score += 7
                    if "m365.cloud.microsoft.com/chat" in page_url:
                        score += 6
                    if "/chat" in page_url or "/chats" in page_url:
                        score += 4
                    if "copilot.microsoft.com" in page_url or "m365.cloud.microsoft.com" in page_url:
                        score += 2

                    try:
                        textbox = await page.query_selector('[role="textbox"]')
                        if textbox:
                            score += 5
                    except Exception:
                        pass

                    if score > best_score:
                        best_score = score
                        selected_page = page

            if selected_page and best_score > 0:
                self.page = selected_page
                self.logger.info(f"Selected existing Copilot page: {self.page.url}")
            elif allow_open:
                self.logger.info("No suitable Copilot chat page found; opening new tab...")
                await self._open_copilot_page()

        except Exception as e:
            self.logger.error(f"Failed to select Copilot page: {e}")
            raise

    async def _open_copilot_page(self):
        context = self.browser.contexts[0]
        self.page = await context.new_page()
        await self.page.goto("https://m365.cloud.microsoft/chat/?fromcode=cmmyr718qsb&refOrigin=Google&auth=2", wait_until="domcontentloaded")
        self.logger.info(f"Opened Copilot page: {self.page.url}")

    async def _send_message(self, message: str):
        try:
            await self.page.wait_for_selector(
                '[role="textbox"], [contenteditable="true"], textarea, input',
                state="visible",
                timeout=10000
            )
            await asyncio.sleep(1)

            last_error = None
            for attempt in range(3):
                frame, textbox = await self._find_textbox()
                if not textbox:
                    last_error = "Chat textbox not found"
                    await asyncio.sleep(1)
                    continue

                await textbox.click()
                await asyncio.sleep(0.2)

                await self.page.keyboard.press("Control+A")
                await self.page.keyboard.press("Backspace")

                try:
                    await textbox.evaluate("""el => {
                        if (el.isContentEditable) {
                            el.textContent = '';
                        } else if ('value' in el) {
                            el.value = '';
                        }
                    }""")
                except Exception:
                    pass

                await self._set_textbox_value(textbox, message)
                await asyncio.sleep(0.2)

                try:
                    await self.page.keyboard.type(message)
                except Exception:
                    pass

                try:
                    current_value = await textbox.evaluate("""el => {
                        if (el.isContentEditable) return el.textContent || '';
                        if ('value' in el) return el.value || '';
                        return '';
                    }""")
                except Exception:
                    current_value = ""

                if message[:10] in (current_value or ""):
                    self.logger.info(f"Message set in textbox at {datetime.datetime.now().isoformat()}")
                    await self.page.keyboard.press("Enter")
                    self.logger.info(f"Message sent at {datetime.datetime.now().isoformat()}")
                    return

                last_error = "Message not set in textbox"
                await asyncio.sleep(1)

            raise Exception(last_error or "Failed to send message")
        except Exception as e:
            self.logger.error(f"Failed to send message: {e}")
            raise

    async def _run_self_test(self):
        start_user_count = len(self.user_messages_list)
        start_ai_count = len(self.ai_messages_list)

        await self._send_message(self.self_test_message)

        test_start = time.time()
        user_captured = False
        ai_captured = False

        while time.time() - test_start < self.self_test_timeout:
            await asyncio.sleep(self.capture_interval)
            await self._capture_messages()

            if len(self.user_messages_list) > start_user_count:
                user_captured = True
            if len(self.ai_messages_list) > start_ai_count:
                ai_captured = True

            if user_captured and ai_captured:
                return True

        return False

    async def send_message_and_wait_for_ai(self, message: str, timeout: float = 60.0) -> str:
        if not self.browser or not self.page:
            self.browser, self.page = await self._connect_to_browser()

        if self.auto_select_copilot_tab:
            await self._select_copilot_page(allow_open=True)

        await self._wait_for_chat_textbox()

        current_user_messages = await self._get_current_user_messages()
        current_ai_messages = await self._get_current_ai_messages()
        start_user_count = len(current_user_messages)
        start_ai_count = len(current_ai_messages)
        baseline_last_ai = current_ai_messages[-1]['content'] if current_ai_messages else ""
        baseline_last_ai_normalized = self._normalize_stream_text(baseline_last_ai)
        baseline_loading = await self._get_loading_message_text()

        await self._send_message(message)
        send_time = time.time()
        self.logger.info(f"Send timestamp: {datetime.datetime.now().isoformat()}")

        start_time = time.time()
        last_content = baseline_last_ai_normalized
        first_token_logged = False
        seen_stop_generating = False
        first_new_content = False
        unchanged_polls = 0

        while time.time() - start_time < timeout:
            await asyncio.sleep(self.capture_interval)

            current_user_messages = await self._get_current_user_messages()
            current_ai_messages = await self._get_current_ai_messages()

            if len(current_user_messages) > len(self.user_messages_list):
                new_user_messages = current_user_messages[len(self.user_messages_list):]
                self.user_messages_list.extend(new_user_messages)
                for i, message_data in enumerate(new_user_messages):
                    message_index = len(self.user_messages_list) - len(new_user_messages) + i
                    await self._save_message_by_index("user", message_index, message_data)
                    self.last_user_index_written = message_index

            loading_text = await self._get_loading_message_text()
            latest = loading_text or (current_ai_messages[-1]['content'] if current_ai_messages else "")
            latest = self._normalize_stream_text(latest)

            if not latest:
                if await self._is_stop_generating_present():
                    seen_stop_generating = True
                continue

            if loading_text:
                if loading_text == baseline_loading:
                    if await self._is_stop_generating_present():
                        seen_stop_generating = True
                    continue
            else:
                if len(current_ai_messages) <= start_ai_count and latest == baseline_last_ai_normalized:
                    if await self._is_stop_generating_present():
                        seen_stop_generating = True
                    continue

            stop_generating_present = await self._is_stop_generating_present()
            if stop_generating_present:
                seen_stop_generating = True

            if latest != last_content:
                if not first_token_logged:
                    first_token_logged = True
                    self.logger.info(
                        f"First token received at {datetime.datetime.now().isoformat()} "
                        f"(latency {time.time() - send_time:.3f}s)"
                    )
                first_new_content = True
                unchanged_polls = 0
                self.logger.info(
                    f"Content update at {datetime.datetime.now().isoformat()} (len {len(latest)})"
                )
                last_content = latest
            elif first_new_content:
                unchanged_polls += 1

            if first_new_content and not stop_generating_present:
                if seen_stop_generating or unchanged_polls >= 6:
                    if current_ai_messages:
                        last_index = len(current_ai_messages) - 1
                        message_data = current_ai_messages[last_index]
                        normalized_content = self._normalize_stream_text(message_data['content'])
                        if not normalized_content:
                            continue
                        self.ai_messages_list = current_ai_messages
                        await self._save_message_by_index("ai", last_index, message_data)
                        self.last_ai_index_written = last_index
                        return normalized_content
                    continue

        raise TimeoutError("Timed out waiting for AI response")

    async def stream_ai_response(self, message: str, timeout: float = 60.0):
        if not self.browser or not self.page:
            self.browser, self.page = await self._connect_to_browser()

        if self.auto_select_copilot_tab:
            await self._select_copilot_page(allow_open=True)

        await self._wait_for_chat_textbox()

        current_ai_messages = await self._get_current_ai_messages()
        start_ai_count = len(current_ai_messages)
        baseline_last_ai = current_ai_messages[-1]['content'] if current_ai_messages else ""
        baseline_last_ai_normalized = self._normalize_stream_text(baseline_last_ai)
        baseline_loading = await self._get_loading_message_text()

        await self._send_message(message)
        send_time = time.time()
        self.logger.info(f"Send timestamp: {datetime.datetime.now().isoformat()}")

        last_content = baseline_last_ai_normalized
        start_time = time.time()
        first_token_logged = False
        seen_stop_generating = False
        first_new_content = False
        unchanged_polls = 0

        while time.time() - start_time < timeout:
            await asyncio.sleep(self.capture_interval)

            current_ai_messages = await self._get_current_ai_messages()
            loading_text = await self._get_loading_message_text()
            latest = loading_text or (current_ai_messages[-1]['content'] if current_ai_messages else "")
            latest = self._normalize_stream_text(latest)
            if not latest:
                if await self._is_stop_generating_present():
                    seen_stop_generating = True
                continue

            if loading_text:
                if loading_text == baseline_loading:
                    if await self._is_stop_generating_present():
                        seen_stop_generating = True
                    continue
            else:
                if len(current_ai_messages) <= start_ai_count and latest == baseline_last_ai_normalized:
                    if await self._is_stop_generating_present():
                        seen_stop_generating = True
                    continue

            stop_generating_present = await self._is_stop_generating_present()
            if stop_generating_present:
                seen_stop_generating = True

            if latest != last_content:
                if not first_token_logged:
                    first_token_logged = True
                    self.logger.info(
                        f"First token received at {datetime.datetime.now().isoformat()} "
                        f"(latency {time.time() - send_time:.3f}s)"
                    )
                first_new_content = True
                unchanged_polls = 0
                self.logger.info(
                    f"Content update at {datetime.datetime.now().isoformat()} (len {len(latest)})"
                )
                yield latest
                last_content = latest
            elif first_new_content:
                unchanged_polls += 1

            if first_new_content and not stop_generating_present:
                if seen_stop_generating or unchanged_polls >= 6:
                    if current_ai_messages:
                        await self._save_message_by_index("ai", len(current_ai_messages) - 1, current_ai_messages[-1])
                        self.last_ai_index_written = len(current_ai_messages) - 1
                    return

        raise TimeoutError("Timed out waiting for AI response")
    
    def _generate_message_id(self, message_type: str, index: int) -> str:
        return f"{message_type}_{index}"
    
    async def _capture_messages(self) -> int:
        new_messages = 0
        
        try:
            try:
                page_title = await self.page.title()
                current_url = self.page.url
            except Exception as e:
                return 0
            current_user_messages = await self._get_current_user_messages()
            current_ai_messages = await self._get_current_ai_messages()

            if len(current_user_messages) > len(self.user_messages_list):
                new_user_messages = current_user_messages[len(self.user_messages_list):]
                self.user_messages_list.extend(new_user_messages)
                
                for i, message_data in enumerate(new_user_messages):
                    message_index = len(self.user_messages_list) - len(new_user_messages) + i
                    await self._save_message_by_index("user", message_index, message_data)
                    self.last_user_index_written = message_index
                    new_messages += 1
            
            loading_present = await self._is_loading_message_present()
            if len(current_ai_messages) > len(self.ai_messages_list) and not loading_present:
                new_ai_messages = current_ai_messages[len(self.ai_messages_list):]
                for i, message_data in enumerate(new_ai_messages):
                    message_index = len(self.ai_messages_list) + i
                    self.ai_messages_list.append(message_data)
                    await self._save_message_by_index("ai", message_index, message_data)
                    self.last_ai_index_written = message_index
                    new_messages += 1
            
            return new_messages
            
        except Exception as e:
            self.logger.error(f"Error capturing messages: {e}")
            return 0
    
    
    
    async def _get_current_user_messages(self):
        messages = []
        
        selectors = [
            '[data-testid="chatOutput"]',
            '[data-content="user-message"]',
            '[data-testid="user-message"]',
            '.user-message'
        ]
        
        for selector in selectors:
            try:
                elements = await self.page.query_selector_all(selector)
                if elements:
                    for element in elements:
                        try:
                            text = await element.inner_text()
                            if text.strip():
                                box = await element.bounding_box()
                                html_snippet = await element.inner_html()
                                messages.append({
                                    'content': text.strip(),
                                    'html_snippet': html_snippet[:500] if html_snippet else "",
                                    'element_location': box
                                })
                        except Exception as e:
                            self.logger.debug(f"Error processing user message element: {e}")
                        return messages
            except Exception as e:
                self.logger.debug(f"Error with user selector {selector}: {e}")
                continue
        return messages
    
    async def _get_current_ai_messages(self):
        messages = []
        
        selectors = [
            '[data-testid="copilot-message-reply-div"]',
            '[data-testid="m365-chat-llm-web-ui-chat-message"]',
            '[data-testid="copilot-message-div"]',
            '[data-testid="lastChatMessage"]',
            '[data-testid="markdown-reply"]',
            '[data-content="ai-message"]',
            '[data-testid="bot-message"]',
            '[data-testid="ai-message"]',
            '.ai-message',
            '.bot-message'
        ]
        
        for selector in selectors:
            try:
                elements = await self.page.query_selector_all(selector)
                if elements:
                    for element in elements:
                        try:
                            text = await element.inner_text()
                            if text.strip():
                                clean_text = text.strip()
                                if "Copilot said" in clean_text:
                                    clean_text = clean_text.replace("Copilot said", "").strip()
                                    if clean_text.endswith("Edit in a page"):
                                        clean_text = clean_text[:-13].strip()

                                if clean_text:
                                    box = await element.bounding_box()
                                    html_snippet = await element.inner_html()
                                    messages.append({
                                        'content': clean_text,
                                        'html_snippet': html_snippet[:500] if html_snippet else "",
                                        'element_location': box
                                    })
                        except Exception as e:
                            self.logger.debug(f"Error processing AI message element: {e}")
                    return messages
            except Exception as e:
                self.logger.debug(f"Error with AI selector {selector}: {e}")
                continue
        return messages
    
    async def _save_message_by_index(self, message_type: str, index: int, message_data: dict):
        try:
            timestamp = datetime.datetime.now().isoformat()
            message_id = self._generate_message_id(message_type, index)
            
            file_data = {
                'timestamp': timestamp,
                'message_id': message_id,
                'type': message_type,
                'content': message_data['content'],
                'html_snippet': message_data['html_snippet'],
                'element_location': message_data['element_location']
            }
            
            with open(self.output_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(file_data, ensure_ascii=False) + '\n')
            self.message_count += 1
            
            self.logger.debug(f"Captured {message_type} message #{index}")
            
        except Exception as e:
            self.logger.error(f"Error saving message: {e}")
    
    async def start_capture(self, navigate_to_copilot: bool = False):
        try:
            self.browser, self.page = await self._connect_to_browser()
            if navigate_to_copilot:
                self.logger.info("Selecting Copilot chat tab...")
                await self._select_copilot_page(allow_open=True)
            await self._wait_for_user_ready()

            if self.self_test_enabled:
                test_ok = await self._run_self_test()
                if not test_ok:
                    self.logger.warning("Self-test failed to capture messages within timeout")
            
            current_url = self.page.url
            with open(self.output_file, 'a', encoding='utf-8') as f:
                session_info = {
                    'session_start': datetime.datetime.now().isoformat(),
                    'url': current_url,
                    'capture_method': 'playwright_cdp',
                    'cdp_endpoint': self.cdp_endpoint,
                    'target_selectors': {
                        'user_messages': '[data-testid="chatOutput"]',
                        'ai_messages': '[data-testid="markdown-reply"]'
                    }
                }
                f.write(json.dumps(session_info, ensure_ascii=False) + '\n')
            self.running = True
            last_count = 0
            while self.running:
                try:
                    self.loop_count += 1
                    new_messages = await self._capture_messages()
                    await asyncio.sleep(self.capture_interval)

                    if self.self_test_enabled:
                        self.running = False
                except KeyboardInterrupt:
                    self.logger.info("Capture interrupted by user")
                    break
                except Exception as e:
                    self.logger.error(f"Error during capture loop: {e}")
                    await asyncio.sleep(2)
                    
        except Exception as e:
            self.logger.error(f"Fatal error: {e}")
        finally:
            await self._cleanup()
    
    async def _cleanup(self):
        try:
            self.logger.info("✓ Disconnected from browser (browser remains open)")
        except Exception as e:
            self.logger.debug(f"Error during cleanup: {e}")
        try:
            with open(self.output_file, 'a', encoding='utf-8') as f:
                session_end = {
                    'session_end': datetime.datetime.now().isoformat(),
                    'total_messages_captured': self.message_count,
                    'total_user_messages': len(self.user_messages_list),
                    'total_ai_messages': len(self.ai_messages_list),
                    'last_user_index_written': self.last_user_index_written,
                    'last_ai_index_written': self.last_ai_index_written,
                    'total_loops': self.loop_count
                }
                f.write(json.dumps(session_end, ensure_ascii=False) + '\n')
        except Exception as e:
            self.logger.debug(f"Error writing session footer: {e}")


async def main():
    try:
        capture = CopilotChatCaptureCDP(
            output_file="copilot_cdp_capture.txt",
            capture_interval=2.0,
            cdp_endpoint="http://172.27.240.1:9222",
            auto_select_copilot_tab=True,
            auto_start_without_prompt=True,
            self_test_enabled=True
        )

        await capture.start_capture(navigate_to_copilot=True)

    except KeyboardInterrupt:
        pass
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())