/**
 * Markdown rendering utilities.
 * Uses markdown-it + DOMPurify for safe HTML output.
 */
var CopilotMarkdown = (function () {
  'use strict';

  var md = window.markdownit
    ? window.markdownit({ html: false, breaks: true, linkify: true, typographer: false })
    : null;

  function escapeHtml(text) {
    return (text || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function render(text) {
    var content = (text || '').replace(/\r\n/g, '\n');
    if (!content) return '';
    if (!md || !window.DOMPurify) {
      return '<p>' + escapeHtml(content).replace(/\n/g, '<br>') + '</p>';
    }
    return window.DOMPurify.sanitize(md.render(content), { USE_PROFILES: { html: true } });
  }

  return { render: render, escapeHtml: escapeHtml };
})();
