"""Playwright page interaction helpers for Copilot chat."""

from __future__ import annotations

import asyncio
import logging
import time
import unicodedata

from playwright.async_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

from .constants import SEND_BUTTON_SELECTORS, TEXTBOX_SELECTORS
from .helpers import normalize_line_endings
from .page_targeting import should_navigate_selected_page


def _normalize_editor_value_for_comparison(text: str) -> str:
    normalized = normalize_line_endings(text)
    while normalized and unicodedata.category(normalized[-1]) == "Cf":
        normalized = normalized[:-1]
    return normalized


async def _score_textbox_candidate(locator, selector_priority: int) -> float | None:
    try:
        metadata = await locator.evaluate(
            """el => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                const tag = (el.tagName || '').toLowerCase();
                const type = (el.getAttribute('type') || '').toLowerCase();
                const editable = !!el.isContentEditable || tag === 'textarea' || (tag === 'input' && !['hidden', 'button', 'submit', 'checkbox', 'radio'].includes(type));
                const disabled = !!el.disabled || el.getAttribute('aria-disabled') === 'true' || el.hasAttribute('readonly');
                const intersectsViewport = rect.bottom > 0 && rect.right > 0 && rect.top < window.innerHeight && rect.left < window.innerWidth;
                const visible = style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0 && intersectsViewport;
                const visibleHeight = Math.max(0, Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0));
                const active = document.activeElement === el;
                return { editable, disabled, visible, bottom: rect.bottom, visibleHeight, active };
            }"""
        )
    except PlaywrightError:
        return None

    if not metadata["editable"] or metadata["disabled"] or not metadata["visible"]:
        return None
    active_bonus = 500000 if metadata["active"] else 0
    return selector_priority * 100000 + active_bonus + float(metadata["visibleHeight"]) * 100 + float(metadata["bottom"])


async def _score_send_button_candidate(locator, selector_priority: int) -> float | None:
    try:
        metadata = await locator.evaluate(
            """el => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                const disabled = !!el.disabled || el.getAttribute('aria-disabled') === 'true';
                const intersectsViewport = rect.bottom > 0 && rect.right > 0 && rect.top < window.innerHeight && rect.left < window.innerWidth;
                const visible = style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0 && intersectsViewport;
                const visibleHeight = Math.max(0, Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0));
                const active = document.activeElement === el;
                return { disabled, visible, bottom: rect.bottom, visibleHeight, active };
            }"""
        )
    except PlaywrightError:
        return None

    if not metadata["visible"]:
        return None
    disabled_penalty = -50000 if metadata["disabled"] else 0
    active_bonus = 10000 if metadata["active"] else 0
    return selector_priority * 100000 + float(metadata["visibleHeight"]) * 100 + float(metadata["bottom"]) + disabled_penalty + active_bonus


async def wait_for_chat_ready(page, copilot_url: str, login_timeout: float) -> None:
    current_url = page.url if page else copilot_url
    if should_navigate_selected_page(current_url, copilot_url):
        try:
            await page.goto(copilot_url, wait_until="domcontentloaded", timeout=10_000)
        except (PlaywrightTimeoutError, PlaywrightError):
            pass
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=1_000)
    except PlaywrightTimeoutError:
        pass
    await wait_for_chat_textbox(page, copilot_url, timeout_ms=int(login_timeout * 1000))


async def wait_for_chat_textbox(page, copilot_url: str, timeout_ms: int) -> None:
    deadline = time.monotonic() + (timeout_ms / 1000)
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        current_url = page.url if page else copilot_url
        try:
            await find_textbox_locator(page, attached_timeout_ms=500)
            return
        except Exception as exc:
            last_error = exc
            if should_navigate_selected_page(current_url, copilot_url):
                try:
                    await page.goto(copilot_url, wait_until="domcontentloaded", timeout=10_000)
                except (PlaywrightTimeoutError, PlaywrightError):
                    pass
            await asyncio.sleep(0.2)
    current_url = page.url if page else copilot_url
    raise RuntimeError(
        "Copilot chat textbox was not detected in the browser. "
        f"Open {current_url}, complete sign-in if needed, and keep the chat page visible."
    ) from last_error


async def find_textbox_locator(page, attached_timeout_ms: int = 2_000):
    if page is None:
        raise RuntimeError("No active Copilot page")
    deadline = time.monotonic() + (attached_timeout_ms / 1000)
    while time.monotonic() < deadline:
        best_locator = None
        best_score = None
        for frame in [page, *page.frames]:
            for priority, selector in enumerate(TEXTBOX_SELECTORS, start=1):
                locator = frame.locator(selector)
                try:
                    count = await locator.count()
                except PlaywrightError:
                    continue
                for index in range(count):
                    candidate = locator.nth(index)
                    score = await _score_textbox_candidate(candidate, len(TEXTBOX_SELECTORS) - priority + 1)
                    if score is None:
                        continue
                    if best_score is None or score > best_score:
                        best_score = score
                        best_locator = candidate
        if best_locator is not None:
            return best_locator
        await asyncio.sleep(0.05)
    raise RuntimeError("Chat textbox not found")


async def find_send_button_locator(page, attached_timeout_ms: int = 800):
    if page is None:
        return None
    deadline = time.monotonic() + (attached_timeout_ms / 1000)
    while time.monotonic() < deadline:
        best_locator = None
        best_score = None
        for frame in [page, *page.frames]:
            for priority, selector in enumerate(SEND_BUTTON_SELECTORS, start=1):
                locator = frame.locator(selector)
                try:
                    count = await locator.count()
                except PlaywrightError:
                    continue
                for index in range(count):
                    candidate = locator.nth(index)
                    score = await _score_send_button_candidate(candidate, len(SEND_BUTTON_SELECTORS) - priority + 1)
                    if score is None:
                        continue
                    if best_score is None or score > best_score:
                        best_score = score
                        best_locator = candidate
        if best_locator is not None:
            return best_locator
        await asyncio.sleep(0.05)
    return None


async def send_message_fast(page, message: str, logger: logging.Logger) -> None:
    textbox = await find_textbox_locator(page, attached_timeout_ms=1_500)
    await focus_textbox(textbox)
    await fill_textbox_bulk(page, textbox, message)

    send_button = await find_send_button_locator(page)
    if send_button is not None:
        try:
            await click_send_button(send_button, timeout_ms=2_000)
            if await wait_for_prompt_submission(textbox, message, send_button=send_button):
                logger.info("Sent prompt through browser send button")
                return
            await click_send_button(send_button, timeout_ms=2_000, force=True)
            if await wait_for_prompt_submission(textbox, message, send_button=send_button):
                logger.info("Sent prompt through browser forced send button click")
                return
        except PlaywrightError:
            logger.debug("Send button click failed, falling back to Enter")

    try:
        await dispatch_enter_via_dom(textbox)
        if await wait_for_prompt_submission(textbox, message, send_button=send_button):
            logger.info("Sent prompt through browser DOM Enter dispatch")
            return
    except PlaywrightError:
        logger.debug("DOM Enter dispatch failed, falling back to keyboard")

    try:
        await page.keyboard.press("Enter")
        if await wait_for_prompt_submission(textbox, message, send_button=send_button):
            logger.info("Sent prompt through browser page Enter key")
            return
    except PlaywrightError:
        logger.debug("Page Enter key failed, trying locator Enter")

    await textbox.press("Enter", timeout=5_000)
    if await wait_for_prompt_submission(textbox, message, send_button=send_button):
        logger.info("Sent prompt through browser textbox Enter key")
        return

    raise RuntimeError("Failed to submit the Copilot prompt from the browser textbox")


async def focus_textbox(textbox) -> None:
    handle = await textbox.element_handle()
    if handle is not None:
        try:
            await handle.evaluate(
                """el => {
                    el.scrollIntoView({ block: 'center', inline: 'nearest' });
                    el.focus();
                    if (el.isContentEditable) {
                        const selection = window.getSelection();
                        if (selection) {
                            selection.removeAllRanges();
                            const range = document.createRange();
                            range.selectNodeContents(el);
                            range.collapse(false);
                            selection.addRange(range);
                        }
                    }
                    el.dispatchEvent(new FocusEvent('focus', { bubbles: true }));
                }"""
            )
            return
        except PlaywrightError:
            pass

    try:
        await textbox.focus(timeout=2_000)
        return
    except (PlaywrightTimeoutError, PlaywrightError):
        pass

    try:
        await textbox.click(timeout=2_000)
        return
    except (PlaywrightTimeoutError, PlaywrightError):
        pass

    if handle is None:
        raise RuntimeError("Textbox disappeared before it could be focused")
    await handle.evaluate(
        """el => {
            el.focus();
            el.dispatchEvent(new FocusEvent('focus', { bubbles: true }));
        }"""
    )


async def fill_textbox_bulk(page, textbox, message: str) -> None:
    is_contenteditable = await textbox.evaluate("el => !!el.isContentEditable")

    await clear_textbox_contents(textbox)

    if is_contenteditable:
        try:
            await page.keyboard.insert_text(message)
        except PlaywrightError:
            await set_textbox_value_via_dom(textbox, message)
    else:
        try:
            await textbox.fill(message, timeout=15_000)
        except PlaywrightError:
            await set_textbox_value_via_dom(textbox, message)

    current_value = await textbox.evaluate(
        """el => {
            if (el.isContentEditable) return el.textContent || '';
            if ('value' in el) return el.value || '';
            return '';
        }"""
    )
    if _normalize_editor_value_for_comparison(current_value) != normalize_line_endings(message):
        await set_textbox_value_via_dom(textbox, message)
        current_value = await textbox.evaluate(
            """el => {
                if (el.isContentEditable) return el.textContent || '';
                if ('value' in el) return el.value || '';
                return '';
            }"""
        )
    if _normalize_editor_value_for_comparison(current_value) != normalize_line_endings(message):
        raise RuntimeError(f"Failed to set the full Copilot prompt in the browser textbox (current value: {current_value!r})")


async def clear_textbox_contents(textbox) -> None:
    try:
        await textbox.press("Control+A", timeout=1_000)
        await textbox.press("Backspace", timeout=1_000)
    except PlaywrightError:
        pass

    handle = await textbox.element_handle()
    if handle is None:
        raise RuntimeError("Textbox disappeared before the prompt could be cleared")
    await handle.evaluate(
        """el => {
            el.focus();
            if (el.isContentEditable) {
                const selection = window.getSelection();
                if (selection) {
                    selection.removeAllRanges();
                    const range = document.createRange();
                    range.selectNodeContents(el);
                    selection.addRange(range);
                }
                el.innerHTML = '';
                document.execCommand('delete', false);
                el.innerText = '';
                el.textContent = '';
            } else if ('value' in el) {
                el.value = '';
            }
            el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'deleteContentBackward' }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }"""
    )


async def set_textbox_value_via_dom(textbox, message: str) -> None:
    handle = await textbox.element_handle()
    if handle is None:
        raise RuntimeError("Textbox disappeared before the prompt could be filled")
    await handle.evaluate(
        """(el, value) => {
            el.focus();
            if (el.isContentEditable) {
                const selection = window.getSelection();
                if (selection) {
                    selection.removeAllRanges();
                    const range = document.createRange();
                    range.selectNodeContents(el);
                    selection.addRange(range);
                }
                el.innerHTML = '';
                document.execCommand('insertText', false, value);
                el.innerText = value;
                el.textContent = value;
            } else if ('value' in el) {
                el.value = value;
            }
            el.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""",
        message,
    )


async def click_send_button(send_button, timeout_ms: int, force: bool = False) -> None:
    try:
        await send_button.evaluate("el => el.scrollIntoView({ block: 'center', inline: 'nearest' })")
        await send_button.click(timeout=timeout_ms, force=force)
        return
    except PlaywrightError:
        handle = await send_button.element_handle()
        if handle is None:
            raise
        clicked = await handle.evaluate(
            """el => {
                if (el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true') {
                    return false;
                }
                el.click();
                return true;
            }"""
        )
        if not clicked:
            raise


async def dispatch_enter_via_dom(textbox) -> None:
    handle = await textbox.element_handle()
    if handle is None:
        raise RuntimeError("Textbox disappeared before the prompt could be submitted")
    await handle.evaluate(
        """el => {
            el.scrollIntoView({ block: 'center', inline: 'nearest' });
            el.focus();
            for (const type of ['keydown', 'keypress', 'keyup']) {
                el.dispatchEvent(new KeyboardEvent(type, {
                    key: 'Enter',
                    code: 'Enter',
                    keyCode: 13,
                    which: 13,
                    bubbles: true,
                    cancelable: true,
                }));
            }
        }"""
    )


async def wait_for_prompt_submission(textbox, message: str, timeout_ms: int = 4_000, send_button=None) -> bool:
    deadline = time.monotonic() + (timeout_ms / 1000)
    expected = normalize_line_endings(message)
    while time.monotonic() < deadline:
        current_value = await textbox.evaluate(
            """el => {
                if (el.isContentEditable) return el.textContent || '';
                if ('value' in el) return el.value || '';
                return '';
            }"""
        )
        if _normalize_editor_value_for_comparison(current_value) != expected:
            return True
        if send_button is not None:
            try:
                button_pending = await send_button.evaluate(
                    """el => {
                        if (!el || !el.isConnected) return true;
                        return el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true';
                    }"""
                )
                if button_pending:
                    return True
            except PlaywrightError:
                return True
        await asyncio.sleep(0.1)
    return False