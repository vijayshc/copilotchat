import asyncio
import json
import os
import queue
import threading
import uuid
from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from werkzeug.serving import WSGIRequestHandler

from getchat_cdp import CopilotChatCaptureCDP

# Use HTTP/1.1 so Werkzeug flushes SSE chunks immediately
WSGIRequestHandler.protocol_version = "HTTP/1.1"

app = Flask(__name__)

_chat_lock = threading.Lock()
_pending_streams: dict[str, str] = {}


async def _send_and_receive(message: str) -> str:
    capture = CopilotChatCaptureCDP(
        output_file="copilot_cdp_capture.txt",
        capture_interval=0.15,
        cdp_endpoint="http://172.27.240.1:9222",
        auto_select_copilot_tab=True,
        auto_start_without_prompt=True,
        self_test_enabled=False
    )
    return await capture.send_message_and_wait_for_ai(message, timeout=180.0)


def _stream_response(message: str):
    q: "queue.Queue[tuple[str, str]]" = queue.Queue()

    def worker():
        async def run():
            capture = CopilotChatCaptureCDP(
                output_file="copilot_cdp_capture.txt",
                capture_interval=0.10,
                cdp_endpoint="http://172.27.240.1:9222",
                auto_select_copilot_tab=True,
                auto_start_without_prompt=True,
                self_test_enabled=False
            )
            async for delta in capture.stream_ai_response(message, timeout=180.0):
                q.put(("data", delta))
            q.put(("end", "[DONE]"))

        try:
            asyncio.run(run())
        except Exception as e:
            q.put(("error", str(e)))

    threading.Thread(target=worker, daemon=True).start()

    def event_stream():
        yield "event: start\ndata: ok\n\n"
        while True:
            event_type, payload = q.get()
            if event_type == "data":
                yield f"data: {json.dumps({'content': payload})}\n\n"
            elif event_type == "end":
                yield "event: end\ndata: done\n\n"
                break
            elif event_type == "error":
                yield f"event: error\ndata: {json.dumps({'error': payload})}\n\n"
                break

    return event_stream()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/send", methods=["POST"])
def send_message():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()

    if not message:
        return jsonify({"error": "Message cannot be empty."}), 400

    with _chat_lock:
        try:
            response_text = asyncio.run(_send_and_receive(message))
            return jsonify({"response": response_text})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


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
        return Response("event: error\ndata: {\"error\": \"Invalid or expired stream id.\"}\n\n",
                        mimetype="text/event-stream")

    return Response(stream_with_context(_stream_response(message)),
                    mimetype="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)
