#$env:PORT="5099"; python app.py
import json
import logging
import os
import queue
import socket as _socket
import threading
import traceback
import uuid
from pathlib import Path


def _bootstrap_failure_log_path() -> Path:
    configured_dir = (os.environ.get("COPILOT_LOG_DIR") or "").strip()
    log_dir = Path(configured_dir).expanduser().resolve() if configured_dir else Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "copilot-startup.log"


def _write_bootstrap_failure(stage: str) -> None:
    try:
        with _bootstrap_failure_log_path().open("a", encoding="utf-8") as handle:
            handle.write(f"[{stage}]\n")
            handle.write(traceback.format_exc())
            handle.write("\n")
    except Exception:
        pass


try:
    from flask import Flask, render_template, request, jsonify, Response, stream_with_context
    from dotenv import load_dotenv
    from werkzeug.serving import WSGIRequestHandler

    from copilot_capture.constants import get_playwright_browser_name, resolve_browser_profile_dir
    from copilot_capture import CopilotChatCapture
    from copilot_capture.logging_utils import configure_runtime_logging
    from copilot_capture.openai_proxy import DEFAULT_PROXY_MODEL, OpenAICompatProxy, OpenAIProxyError
except Exception:
    _write_bootstrap_failure("import")
    raise

load_dotenv()
RUNTIME_LOG_PATH = configure_runtime_logging()
logger = logging.getLogger(__name__)

# Use HTTP/1.1 so Werkzeug flushes SSE chunks immediately
WSGIRequestHandler.protocol_version = "HTTP/1.1"


class _NagleDisabledRequestHandler(WSGIRequestHandler):
    """Werkzeug request handler with TCP_NODELAY disabled so each SSE chunk
    is sent immediately without being coalesced by the OS Nagle algorithm.
    Without this, multiple small SSE events can arrive at the client as a
    single TCP segment, making the stream appear to batch or freeze."""

    def setup(self) -> None:  # type: ignore[override]
        super().setup()
        try:
            self.connection.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
        except (AttributeError, OSError):
            pass

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
                logger.info(
                    "Creating capture service browser=%s headless=%s runtime_log=%s",
                    browser_name,
                    os.environ.get("COPILOT_HEADLESS", "false").lower() == "true",
                    RUNTIME_LOG_PATH,
                )
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
    logger.info("Handling non-stream Copilot request prompt_length=%s", len(message))
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
            logger.exception("Streaming Copilot request failed")
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
                logger.info("Creating OpenAI compatibility proxy")
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


@app.route("/healthz", methods=["GET"])
def healthz():
    return _json_response(
        {
            "status": "ok",
            "browser": get_playwright_browser_name(os.environ.get("COPILOT_BROWSER")),
            "headless": os.environ.get("COPILOT_HEADLESS", "false").lower() == "true",
            "capture_initialized": _capture_service is not None,
            "openai_proxy_initialized": _openai_proxy is not None,
            "runtime_log": str(RUNTIME_LOG_PATH),
        }
    )


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
        logger.exception("/send request failed")
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
    logger.info("Registered streaming request stream_id=%s prompt_length=%s", stream_id, len(message))
    _pending_streams[stream_id] = message
    return jsonify({"stream_id": stream_id})


@app.route("/stream_events/<stream_id>")
def stream_events(stream_id):
    """Step 2: EventSource connects here (GET) to receive SSE events."""
    message = _pending_streams.pop(stream_id, None)
    if not message:
        logger.warning("Rejected stream_events request for invalid stream_id=%s", stream_id)
        return Response("event: stream_error\ndata: {\"error\": \"Invalid or expired stream id.\"}\n\n",
                        content_type="text/event-stream; charset=utf-8")

    logger.info("Opening SSE stream stream_id=%s prompt_length=%s", stream_id, len(message))
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
        logger.info("Handling /v1/chat/completions stream=%s message_count=%s", bool(data.get("stream")), len(data.get("messages") or []))
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
        logger.exception("OpenAI compatibility request failed with proxy error")
        return _openai_error_response(exc)
    except Exception as exc:
        logger.exception("OpenAI compatibility request failed with server error")
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
    browser_name = get_playwright_browser_name(os.environ.get("COPILOT_BROWSER"))
    logger.info(
        "Starting Flask server port=%s browser=%s headless=%s auto_login=%s runtime_log=%s",
        port,
        browser_name,
        os.environ.get("COPILOT_HEADLESS", "false").lower() == "true",
        os.environ.get("COPILOT_AUTO_LOGIN", "false").lower() in {"1", "true", "yes", "on", "y"},
        RUNTIME_LOG_PATH,
    )
    try:
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True,
                request_handler=_NagleDisabledRequestHandler)
    except Exception:
        logger.exception("Flask server failed to start")
        _write_bootstrap_failure("app.run")
        raise
