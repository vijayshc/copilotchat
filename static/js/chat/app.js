/**
 * Main application controller — ties together UI, API, streaming, and input.
 */
(function () {
  'use strict';

  var input = document.getElementById('messageInput');
  var sendBtn = document.getElementById('sendBtn');
  var modeBadge = document.getElementById('modeBadge');
  var modeInputs = Array.from(document.querySelectorAll('input[name="responseMode"]'));
  var sending = false;

  /* ======================== Mode Toggle ============================= */

  function getResponseMode() {
    var checked = modeInputs.find(function (el) { return el.checked; });
    return checked ? checked.value : 'stream';
  }

  function updateModeBadge() {
    var mode = getResponseMode();
    modeBadge.innerHTML = '<span class="badge__dot"></span> ' +
      (mode === 'stream' ? 'Streaming' : 'Synchronous');
  }

  /* ======================== Send Logic ============================== */

  function lockUI() {
    sending = true;
    sendBtn.disabled = true;
  }

  function unlockUI() {
    sending = false;
    sendBtn.disabled = false;
  }

  async function sendMessage() {
    var message = input.value.trim();
    if (!message || sending) return;

    var mode = getResponseMode();
    CopilotUI.appendMessage(message, 'user');
    input.value = '';
    input.style.height = 'auto';

    var assistantEl = CopilotUI.appendMessage('', 'assistant');
    var renderer = CopilotStream.createRenderer(assistantEl);

    lockUI();
    CopilotUI.setStatus(mode === 'stream' ? 'Streaming response\u2026' : 'Waiting for response\u2026');

    try {
      if (mode === 'non-stream') {
        var response = await CopilotAPI.sendNonStream(message);
        renderer.setFinal(response);
        CopilotUI.setStatus('Response complete.');
      } else {
        await CopilotAPI.sendStream(
          message,
          function onSnapshot(text) { renderer.setSnapshot(text); },
          function onDone() {
            renderer.flush();
            CopilotUI.setStatus('Response complete.');
          },
          function onError(msg) {
            renderer.flush();
            CopilotUI.setStatus(msg);
          }
        );
      }
    } catch (err) {
      console.error('Send error', err);
      CopilotUI.setStatus('Error: ' + (err.message || 'Unknown error'));
    } finally {
      unlockUI();
    }
  }

  /* ======================== Event Bindings =========================== */

  sendBtn.addEventListener('click', sendMessage);

  modeInputs.forEach(function (el) {
    el.addEventListener('change', updateModeBadge);
  });

  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  input.addEventListener('input', function () {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 140) + 'px';
  });

  /* Init */
  updateModeBadge();
})();
