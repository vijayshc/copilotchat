/**
 * Stream renderer — receives full-text snapshots and renders them.
 * The backend sends full snapshots (not deltas), so we simply replace content.
 */
var CopilotStream = (function () {
  'use strict';

  function createRenderer(element) {
    var currentText = '';
    var rafId = null;

    function render() {
      rafId = null;
      CopilotUI.setMessageContent(element, currentText, 'assistant');
      CopilotUI.scrollToBottom();
    }

    return {
      /** Replace content with a full snapshot from the server */
      setSnapshot: function (snapshot) {
        currentText = snapshot;
        if (!rafId) {
          rafId = requestAnimationFrame(render);
        }
      },

      /** Set content for non-stream (complete) responses */
      setFinal: function (text) {
        currentText = text;
        if (rafId) {
          cancelAnimationFrame(rafId);
          rafId = null;
        }
        CopilotUI.setMessageContent(element, currentText, 'assistant');
        CopilotUI.scrollToBottom();
      },

      /** Force immediate render of current content */
      flush: function () {
        if (rafId) {
          cancelAnimationFrame(rafId);
          rafId = null;
        }
        CopilotUI.setMessageContent(element, currentText, 'assistant');
        CopilotUI.scrollToBottom();
      },

      hasContent: function () {
        return currentText.length > 0;
      }
    };
  }

  return { createRenderer: createRenderer };
})();
