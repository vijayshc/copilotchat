/**
 * API communication — send messages via non-stream and streaming endpoints.
 */
var CopilotAPI = (function () {
  'use strict';

  /**
   * Send a message using the synchronous /send endpoint.
   * Returns the response text.
   */
  async function sendNonStream(message) {
    var res = await fetch('/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: message })
    });
    var data = await res.json();
    if (!res.ok || data.error) {
      throw new Error(data.error || 'Request failed');
    }
    return data.response || '';
  }

  /**
   * Send a message using the streaming /send_stream + SSE endpoint.
   * Calls onSnapshot(fullText) for each snapshot, onDone() when complete.
   */
  async function sendStream(message, onSnapshot, onDone, onError) {
    // Step 1 — register the stream
    var initRes = await fetch('/send_stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: message })
    });
    var initData = await initRes.json();
    if (initData.error || !initData.stream_id) {
      throw new Error(initData.error || 'Failed to start stream');
    }

    // Step 2 — SSE connection
    return new Promise(function (resolve, reject) {
      var source = new EventSource('/stream_events/' + initData.stream_id);
      var completed = false;

      source.onmessage = function (event) {
        try {
          var payload = JSON.parse(event.data);
          if (payload.snapshot != null) {
            onSnapshot(payload.snapshot);
          }
        } catch (e) {
          console.error('Invalid SSE payload', e);
        }
      };

      source.addEventListener('end', function () {
        completed = true;
        onDone();
        source.close();
        resolve(source);
      });

      source.addEventListener('stream_error', function (event) {
        var msg = 'Streaming error.';
        try {
          var p = JSON.parse(event.data);
          if (p.error) msg = 'Error: ' + p.error;
        } catch (_) { /* keep generic */ }
        onError(msg);
        source.close();
        reject(new Error(msg));
      });

      source.onerror = function () {
        if (!completed) {
          onError('Stream connection error.');
          source.close();
          reject(new Error('Stream connection error.'));
        } else {
          source.close();
          resolve();
        }
      };

    });
  }

  return {
    sendNonStream: sendNonStream,
    sendStream: sendStream
  };
})();
