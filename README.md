# Microsoft 365 Copilot Playwright WebSocket Capture and OpenAI-Compatible Proxy

This application uses **Playwright** with a **persistent browser profile** (Firefox or Chrome) and captures Copilot replies from the **SignalR WebSocket** instead of scraping HTML. On top of that capture pipeline, it also exposes an **OpenAI-compatible chat completions API** so standard SDKs can use Microsoft Copilot through a local proxy.

## Current application status

- No Chrome/CDP scraping dependency.
- No DOM polling for assistant text.
- Prompts are filled in bulk instead of being typed word-by-word.
- A single long-lived Playwright browser session is reused across requests for much lower latency.
- Runtime browser switching between Playwright Firefox and Playwright Chrome (`COPILOT_BROWSER`).
- Streaming mode consumes websocket deltas; non-streaming mode waits for the final websocket message.
- The Flask app exposes both the **local web chat UI** and **OpenAI-compatible `/v1/*` endpoints**.
- The OpenAI-compatible layer supports messages, tools, tool_choice, streaming, JSON-style response shaping, and tool-result follow-up turns.
- The proxy includes continuation recovery by replaying the full transcript in a fresh Copilot chat when lightweight continuation fails.
- Automated tests and live OpenAI SDK validation are present and were verified successfully.

## Project structure

- `app.py` — Flask server for the web UI, SSE endpoints, and OpenAI-compatible API routes.
- `getchat_cdp.py` — compatibility entry point; delegates to the Playwright implementation.
- `copilot_capture/` — modular Playwright/websocket backend.
- `templates/index.html` — frontend chat UI.
- `docs/` — architecture and operational documentation.
- `tests/test_openai_proxy.py` — OpenAI compatibility tests.
- `tests/test_turn_state.py` — websocket/state-response assembly tests.
- `tests/e2e_openai_client_validation.py` — live end-to-end validation through the official OpenAI Python SDK.
- `tests/e2e_browser_switch_validation.py` — live browser-switch validation across Playwright Firefox and Playwright Chrome.
- `setup_firefox.sh` — installs/updates Playwright browser binaries.

## Playwright browser setup

This repository is intended to run with:

- Python: `python` on Windows, or `~/anaconda3/bin/python3` on Linux/macOS
- Playwright: latest available version from PyPI at install time
- Browser: Firefox and Chromium installed via Playwright, with optional Chrome channel

### Recommended setup

#### Windows PowerShell

```powershell
./setup_firefox.ps1
```

#### Linux/macOS

```bash
chmod +x setup_firefox.sh
./setup_firefox.sh
```

That script will:

1. Upgrade `pip`
2. Upgrade `flask`, `playwright`, and `python-dotenv`
3. Install Playwright-managed Firefox and Chromium
4. Optionally install Playwright Chrome channel

## Running the app

```bash
python app.py
```

By default, the Flask app listens on port `5056` unless `PORT` is set.

Then open:

```text
http://127.0.0.1:5056/
```

You can also run dedicated local instances on custom ports, for example:

```powershell
$env:PORT="5099"
python app.py
```

```powershell
$env:PORT="5101"
python app.py
```

The same server now also exposes OpenAI-compatible endpoints:

- `GET /v1/models`
- `GET /v1/models/<model>`
- `POST /v1/chat/completions`

That lets you point the official `openai` Python package at the local Copilot proxy by setting `base_url` to your local server (for example `http://127.0.0.1:5056/v1`) and using any placeholder API key.

Example:

```python
from openai import OpenAI

client = OpenAI(
	base_url="http://127.0.0.1:5101/v1",
	api_key="dummy-key",
)

response = client.chat.completions.create(
	model="copilot-proxy",
	messages=[{"role": "user", "content": "What is 12 + 5?"}],
)

print(response.choices[0].message.content)
```

On first use, the selected browser opens with a persistent profile stored in a user-local directory. By default that is `%LOCALAPPDATA%\copilot-firefox-profile` (Firefox) or `%LOCALAPPDATA%\copilot-chrome-profile` (Chrome) on Windows, and `~/.playwright-firefox-profile` / `~/.playwright-chrome-profile` on Linux/macOS. Sign in to Microsoft 365 Copilot once and keep using the same profile directory afterward.

The repository also includes a local `.env` file template. `app.py` and `getchat_cdp.py` load it automatically.

## Optional configuration

These environment variables are supported:

- `COPILOT_BROWSER` — browser engine for Playwright (`firefox` or `chrome`)
- `COPILOT_FIREFOX_PROFILE` — override the persistent Firefox profile directory
- `COPILOT_CHROME_PROFILE` — override the persistent Chrome profile directory
- `COPILOT_CHROME_CHANNEL` — Playwright chromium channel when `COPILOT_BROWSER=chrome` (default: `chrome`, then fallback to bundled chromium)
- `COPILOT_URL` — override the default Copilot chat URL
- `COPILOT_HEADLESS` — set to `true` or `1` to run headless
- `COPILOT_AUTO_LOGIN` — set to `true` to allow automated Microsoft login steps; keep `false` in production
- `COPILOT_CAPTURE_FILE` — transcript JSONL output path
- `COPILOT_CAPTURE_INTERVAL` — background capture loop sleep interval
- `COPILOT_LOGIN_TIMEOUT` — seconds to wait for login/chat readiness
- `COPILOT_LAUNCH_TIMEOUT` — Playwright browser launch timeout in seconds
- `PORT` — Flask server port for the local web chat UI
- `COPILOT_USERNAME` — optional Microsoft account username for auto-login on a fresh browser profile
- `COPILOT_PASSWORD` — optional Microsoft account password for auto-login on a fresh browser profile
- `OPENAI_PROXY_MODEL` — override the model id returned by the local OpenAI-compatible proxy

When `COPILOT_AUTO_LOGIN=true` and `COPILOT_USERNAME` / `COPILOT_PASSWORD` are provided, the app can automatically step through the standard Microsoft username/password pages. If your tenant introduces extra MFA or third-party SSO challenges (for example Duo approval pages), those challenges are controlled by the identity provider and may still require the second factor to complete before the Copilot chat page becomes ready.

With `COPILOT_AUTO_LOGIN=false`, automatic credential submission and sign-in prompts are skipped. This is the recommended production setting.

## Windows notes

- Playwright-managed browsers were verified on Windows with `python -m playwright install firefox chromium`.
- Optional Playwright Chrome channel install: `python -m playwright install chrome`.
- Browser binaries are stored under `%USERPROFILE%\AppData\Local\ms-playwright` by default.
- Persistent browser profile defaults are `%LOCALAPPDATA%\copilot-firefox-profile` (Firefox) and `%LOCALAPPDATA%\copilot-chrome-profile` (Chrome).
- If your organization requires a proxy for browser downloads, Playwright supports standard proxy environment variables such as `HTTPS_PROXY`, `PLAYWRIGHT_DOWNLOAD_HOST`, `PLAYWRIGHT_FIREFOX_DOWNLOAD_HOST`, and `PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST` during installation.

## WebSocket capture behavior

The backend listens for Copilot websocket traffic under the chat hub path:

- websocket deltas: `writeAtCursor`
- final assistant message: `messages` from bot/assistant payloads
- final completion signal: SignalR `type: 3`
- user turn echo: `type: 2` item payloads
- per-turn websocket correlation: invocation id and socket id are tracked so unrelated closes/history events do not complete the wrong request

This means:

- `/send_stream` uses streaming websocket deltas and emits snapshots over SSE.
- `/send` waits for the final websocket message and returns the final response body.
- `/v1/chat/completions` maps OpenAI-style requests to the same Copilot-backed response pipeline.

Thinking/reasoning frames are tracked internally but not surfaced to the UI by default.

## OpenAI-compatible proxy behavior

The compatibility layer in `copilot_capture/openai_proxy.py` currently supports:

- OpenAI-style `messages`
- `tools` and `tool_choice`
- streamed and non-streamed chat completions
- `response_format`
- `stop`
- `max_tokens` / `max_completion_tokens`
- stream usage chunks when `stream_options.include_usage=true`

Conversation behavior follows this rule:

- first turn: send bootstrap instructions/tool catalog/transcript
- later matched continuation turns: send only the latest message when safe

If a continuation turn fails, the proxy can start a fresh Copilot chat and replay the full transcript once.

## Output format

Captured messages are written to `copilot_cdp_capture.txt` as JSON Lines.

Example:

```json
{"timestamp":"2026-04-04T00:27:45.908015+00:00","message_id":"user_0","type":"user","content":"Show me the weather forecast","html_snippet":"","element_location":null,"capture_source":"websocket","streaming_mode":"final"}
{"timestamp":"2026-04-04T00:27:49.123456+00:00","message_id":"a-1","type":"ai","content":"Here's a quick weather forecast for your area this morning.","html_snippet":"","element_location":null,"capture_source":"websocket","streaming_mode":"final"}
```

## Verification

Run the automated checks with:

```bash
python -m unittest discover -s tests -v
```

Run the live end-to-end OpenAI SDK validation with:

```powershell
$env:PORT="5101"
python app.py
```

In another terminal:

```powershell
$env:OPENAI_PROXY_BASE_URL="http://127.0.0.1:5101/v1"
python tests\e2e_openai_client_validation.py
```

Verified live validation covers:

- `models.list()`
- non-streamed completion
- streamed completion
- tool call turn
- tool-result follow-up turn

Run the browser switch validation (Firefox + Chrome) with:

```powershell
python tests\e2e_browser_switch_validation.py --browsers firefox chrome
```

If you want to test only one browser:

```powershell
python tests\e2e_browser_switch_validation.py --browsers chrome
```

## Documentation

- `docs/end-to-end-design.md` — detailed architecture and flow documentation
- `docs/setup-run-validation.md` — setup, run, validation, and troubleshooting guide

## Notes

- `getchat_cdp.py` keeps the legacy name so existing imports do not break.
- `run_capture.bat` is legacy-only and no longer represents the recommended Linux workflow.
- If the chat textbox is not found, bring the Copilot chat tab to the front and finish sign-in in the selected persistent browser window.