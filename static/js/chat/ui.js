/**
 * UI helpers — message display, status bar, DOM utilities.
 */
var CopilotUI = (function () {
  'use strict';

  var chatPanel = document.getElementById('chatPanel');
  var statusBar = document.getElementById('status');

  function setMessageContent(el, text, role) {
    if (role === 'assistant') {
      el.innerHTML = text ? CopilotMarkdown.render(text) : '';
    } else {
      el.textContent = text || '';
    }
  }

  function appendMessage(text, role) {
    var div = document.createElement('div');
    div.className = 'message message--' + role;
    setMessageContent(div, text, role);
    chatPanel.appendChild(div);
    scrollToBottom();
    return div;
  }

  function scrollToBottom() {
    chatPanel.scrollTop = chatPanel.scrollHeight;
  }

  function setStatus(text) {
    statusBar.textContent = text;
  }

  return {
    appendMessage: appendMessage,
    setMessageContent: setMessageContent,
    setStatus: setStatus,
    scrollToBottom: scrollToBottom
  };
})();
