#$env:PORT="5099"; python app.py
import json
import os
import queue
import threading
import uuid
from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from dotenv import load_dotenv
from werkzeug.serving import WSGIRequestHandler

from copilot_capture.constants import get_playwright_browser_name, resolve_browser_profile_dir
from copilot_capture import CopilotChatCapture
from copilot_capture.openai_proxy import DEFAULT_PROXY_MODEL, OpenAICompatProxy, OpenAIProxyError

# Local defaults for the Playwright browser + WebSocket capture flow.
# Blank values fall back to the application defaults.

# COPILOT_URL=https://m365.cloud.microsoft/chat/
# # Browser engine used by Playwright automation: firefox or chrome
# COPILOT_BROWSER=firefox
# # Leave blank to use the built-in default profile directory.
# # Windows default: %LOCALAPPDATA%\copilot-firefox-profile
# # Linux/macOS default: ~/.playwright-firefox-profile
# COPILOT_FIREFOX_PROFILE=
# # Leave blank to use the built-in default profile directory.
# # Windows default: %LOCALAPPDATA%\copilot-chrome-profile
# # Linux/macOS default: ~/.playwright-chrome-profile
# COPILOT_CHROME_PROFILE=
# # Optional when COPILOT_BROWSER=chrome. Defaults to "chrome" and falls back to bundled chromium if unavailable.
# COPILOT_CHROME_CHANNEL=chrome
# COPILOT_HEADLESS=false
# # Set to true only when you explicitly want credential-driven Microsoft sign-in automation.
# # Keep false in production so sign-in happens manually (or via an already authenticated profile).
# COPILOT_AUTO_LOGIN=false
# COPILOT_CAPTURE_FILE=copilot_cdp_capture.txt
# COPILOT_CAPTURE_INTERVAL=0.05
# COPILOT_LOGIN_TIMEOUT=300
# COPILOT_LAUNCH_TIMEOUT=60
# COPILOT_USERNAME=
# COPILOT_PASSWORD=
# PORT=5000

load_dotenv()

# Use HTTP/1.1 so Werkzeug flushes SSE chunks immediately
WSGIRequestHandler.protocol_version = "HTTP/1.1"

app = Flask(__name__)
app.json.ensure_ascii = False

_pending_streams: dict[str, str] = {}
_capture_service: CopilotChatCapture | None = None
_capture_service_lock = threading.Lock()
_openai_proxy: OpenAICompatProxy | None = None
_openai_proxy_lock = threading.Lock()


def get_capture_service() -> CopilotChatCapture:
    global _capture_service
    if _capture_service is None:
        with _capture_service_lock:
            if _capture_service is None:
                browser_name = get_playwright_browser_name(os.environ.get("COPILOT_BROWSER"))
                _capture_service = CopilotChatCapture(
                    copilot_url=os.environ.get("COPILOT_URL", "https://m365.cloud.microsoft/chat/"),
                    user_data_dir=resolve_browser_profile_dir(browser_name),
                    browser_name=browser_name,
                    headless=os.environ.get("COPILOT_HEADLESS", "false").lower() == "true",
                    login_timeout=float(os.environ.get("COPILOT_LOGIN_TIMEOUT", "300")),
                    launch_timeout=float(os.environ.get("COPILOT_LAUNCH_TIMEOUT", "60")),
                )
    return _capture_service


def _send_and_receive(message: str) -> str:
    return get_capture_service().send_message_and_wait_for_ai_sync(message, timeout=180.0)


def _stream_response(message: str):
    q: "queue.Queue[tuple[str, str]]" = queue.Queue()

    def worker():
        try:
            for kind, payload in get_capture_service().stream_ai_response_sync(message, timeout=180.0):
                if kind == "snapshot":
                    q.put(("snapshot", payload))
            q.put(("end", ""))
        except Exception as e:
            q.put(("error", str(e)))

    threading.Thread(target=worker, daemon=True).start()

    def event_stream():
        yield "event: start\ndata: ok\n\n"
        while True:
            event_type, payload = q.get()
            if event_type == "snapshot":
                yield f"data: {json.dumps({'snapshot': payload}, ensure_ascii=False)}\n\n"
            elif event_type == "end":
                yield "event: end\ndata: done\n\n"
                break
            elif event_type == "error":
                yield f"event: stream_error\ndata: {json.dumps({'error': payload}, ensure_ascii=False)}\n\n"
                break

    return event_stream()


def get_openai_proxy() -> OpenAICompatProxy:
    global _openai_proxy
    if _openai_proxy is None:
        with _openai_proxy_lock:
            if _openai_proxy is None:
                proxy_kwargs = {
                    "send_prompt": _send_and_receive,
                    "default_model": os.environ.get("OPENAI_PROXY_MODEL", DEFAULT_PROXY_MODEL),
                }
                if os.environ.get("OPENAI_PROXY_AUTO_NEW_CHAT", "false").lower() == "true":
                    proxy_kwargs["reset_conversation"] = lambda: get_capture_service().start_new_chat_sync()
                _openai_proxy = OpenAICompatProxy(**proxy_kwargs)
    return _openai_proxy


def _json_response(payload: dict[str, object], *, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, ensure_ascii=False),
        status=status,
        content_type="application/json; charset=utf-8",
    )


def _openai_error_response(exc: OpenAIProxyError) -> Response:
    return _json_response(exc.to_response(), status=exc.status_code)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/send", methods=["POST"])
def send_message():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()

    if not message:
        return jsonify({"error": "Message cannot be empty."}), 400

    try:
        response_text = _send_and_receive(message)
        return Response(
            json.dumps({"response": response_text}, ensure_ascii=False),
            content_type="application/json; charset=utf-8",
        )
    except Exception as e:
        return Response(
            json.dumps({"error": str(e)}, ensure_ascii=False),
            status=500,
            content_type="application/json; charset=utf-8",
        )


@app.route("/send_stream", methods=["POST"])
def send_stream():
    """Step 1: POST message via JSON body, get back a stream_id."""
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()

    if not message:
        return jsonify({"error": "Message cannot be empty."}), 400

    stream_id = uuid.uuid4().hex
    _pending_streams[stream_id] = message
    return jsonify({"stream_id": stream_id})


@app.route("/stream_events/<stream_id>")
def stream_events(stream_id):
    """Step 2: EventSource connects here (GET) to receive SSE events."""
    message = _pending_streams.pop(stream_id, None)
    if not message:
        return Response("event: stream_error\ndata: {\"error\": \"Invalid or expired stream id.\"}\n\n",
                        content_type="text/event-stream; charset=utf-8")

    return Response(stream_with_context(_stream_response(message)),
                    content_type="text/event-stream; charset=utf-8",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                    })


@app.route("/v1/models", methods=["GET"])
def openai_models():
    return _json_response(get_openai_proxy().list_models_response())


@app.route("/v1/models/<model_id>", methods=["GET"])
def openai_model(model_id: str):
    return _json_response(get_openai_proxy().retrieve_model_response(model_id))


@app.route("/v1/chat/completions", methods=["POST"])
def openai_chat_completions():
    data = request.get_json(silent=True)
    if data is None:
        return _json_response(
            {
                "error": {
                    "message": "Request body must be valid JSON.",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": None,
                }
            },
            status=400,
        )

    try:
        if bool(data.get("stream")):
            stream = get_openai_proxy().stream_chat_completion(data)
            return Response(
                stream_with_context(stream),
                content_type="text/event-stream; charset=utf-8",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )
        return _json_response(get_openai_proxy().create_chat_completion(data))
    except OpenAIProxyError as exc:
        return _openai_error_response(exc)
    except Exception as exc:
        return _json_response(
            {
                "error": {
                    "message": str(exc),
                    "type": "server_error",
                    "param": None,
                    "code": None,
                }
            },
            status=500,
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5056"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)
