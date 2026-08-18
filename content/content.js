(function () {
  const DEFAULTS = {
    routes: ['info', 'job', 'clip', 'lang'],
    defaultRoute: 'info'
  };

  // Listen for message from background script to open capture dialog
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.action === 'OPEN_CAPTURE_DIALOG') {
      openCaptureDialog();
      sendResponse({ status: 'dialog_opened' });
    }
  });

  async function openCaptureDialog() {
    // Remove existing instance if open
    const existing = document.getElementById('info-triage-capture-host');
    if (existing) {
      existing.remove();
    }

    // Capture text selection & page URL
    const selection = window.getSelection ? window.getSelection().toString().trim() : '';
    const pageUrl = window.location.href;

    // Load stored routes and default route
    const stored = await chrome.storage.sync.get(DEFAULTS);
    const routesList = Array.isArray(stored.routes) && stored.routes.length > 0 ? stored.routes : DEFAULTS.routes;
    const defaultRoute = stored.defaultRoute && routesList.includes(stored.defaultRoute) ? stored.defaultRoute : routesList[0];

    // Initial text value: URL + selection if present, else URL
    const initialText = selection ? `${pageUrl}\n\n${selection}` : pageUrl;

    // Create top-level host element in light DOM with absolute modal positioning
    const host = document.createElement('div');
    host.id = 'info-triage-capture-host';
    host.style.cssText = 'position: fixed !important; top: 0 !important; left: 0 !important; width: 100vw !important; height: 100vh !important; z-index: 2147483647 !important; margin: 0 !important; padding: 0 !important; border: none !important; background: transparent !important; pointer-events: auto !important;';

    const shadow = host.attachShadow({ mode: 'open' });

    // Embedded CSS for instant, reliable rendering in Shadow DOM
    const inlineCss = `
      :host {
        all: initial;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        font-size: 14px;
        line-height: 1.5;
        color: #0f172a;
      }
      *, *::before, *::after {
        box-sizing: border-box;
        margin: 0;
        padding: 0;
      }
      .overlay {
        position: fixed;
        top: 0;
        left: 0;
        width: 100vw;
        height: 100vh;
        background-color: rgba(15, 23, 42, 0.5);
        backdrop-filter: blur(5px);
        -webkit-backdrop-filter: blur(5px);
        z-index: 2147483647;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 20px;
        animation: fadeIn 0.15s ease-out;
      }
      .dialog {
        background: #ffffff;
        width: 100%;
        max-width: 520px;
        border-radius: 12px;
        box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.15), 0 8px 10px -6px rgba(0, 0, 0, 0.1);
        border: 1px solid #e2e8f0;
        overflow: hidden;
        display: flex;
        flex-direction: column;
        animation: slideUp 0.2s cubic-bezier(0.16, 1, 0.3, 1);
      }
      .header {
        padding: 16px 20px;
        border-bottom: 1px solid #f1f5f9;
        display: flex;
        align-items: center;
        justify-content: space-between;
        background: #f8fafc;
      }
      .brand {
        display: flex;
        align-items: center;
        gap: 10px;
      }
      .brand-icon {
        width: 24px;
        height: 24px;
        border-radius: 6px;
        object-fit: cover;
      }
      .brand-svg {
        width: 24px;
        height: 24px;
        color: #2563eb;
      }
      .title {
        font-size: 15px;
        font-weight: 600;
        color: #0f172a;
        letter-spacing: -0.01em;
      }
      .close-btn {
        background: transparent;
        border: none;
        font-size: 20px;
        line-height: 1;
        color: #64748b;
        cursor: pointer;
        width: 30px;
        height: 30px;
        border-radius: 6px;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: all 0.12s ease;
      }
      .close-btn:hover {
        background: #e2e8f0;
        color: #0f172a;
      }
      .body {
        padding: 20px;
        display: flex;
        flex-direction: column;
        gap: 16px;
        background: #ffffff;
      }
      .field {
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      label {
        font-size: 12px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.03em;
        color: #475569;
      }
      select, textarea, input[type="text"] {
        width: 100%;
        padding: 9px 12px;
        font-size: 14px;
        font-family: inherit;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        background: #ffffff;
        color: #0f172a;
        transition: border-color 0.15s ease, box-shadow 0.15s ease;
        outline: none;
      }
      select:focus, textarea:focus, input[type="text"]:focus {
        border-color: #2563eb;
        box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
      }
      textarea {
        min-height: 120px;
        resize: vertical;
        line-height: 1.45;
      }
      .field-hint {
        font-size: 11px;
        color: #64748b;
      }
      .footer {
        padding: 16px 20px;
        border-top: 1px solid #f1f5f9;
        background: #f8fafc;
        display: flex;
        justify-content: flex-end;
        gap: 10px;
        align-items: center;
      }
      .btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
        padding: 9px 18px;
        font-size: 13px;
        font-weight: 500;
        border-radius: 6px;
        cursor: pointer;
        transition: all 0.15s ease;
        border: 1px solid transparent;
      }
      .btn-secondary {
        background: #ffffff;
        border-color: #cbd5e1;
        color: #334155;
      }
      .btn-secondary:hover {
        background: #f1f5f9;
        border-color: #94a3b8;
      }
      .btn-primary {
        background: #2563eb;
        color: #ffffff;
      }
      .btn-primary:hover {
        background: #1d4ed8;
      }
      .btn:disabled {
        opacity: 0.6;
        cursor: not-allowed;
      }
      .status-card {
        padding: 14px 16px;
        border-radius: 8px;
        font-size: 13px;
        display: flex;
        flex-direction: column;
        gap: 10px;
      }
      .status-card.success {
        background: #f0fdf4;
        border: 1px solid #bbf7d0;
        color: #166534;
      }
      .status-card.error {
        background: #fef2f2;
        border: 1px solid #fecaca;
        color: #991b1b;
      }
      .status-title {
        font-weight: 600;
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 14px;
      }
      .meta-grid {
        display: grid;
        grid-template-columns: auto 1fr;
        gap: 6px 12px;
        font-size: 12px;
        background: rgba(255, 255, 255, 0.85);
        padding: 10px;
        border-radius: 6px;
        border: 1px solid rgba(0, 0, 0, 0.05);
      }
      .meta-key {
        font-weight: 600;
        color: #334155;
      }
      .meta-val {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        word-break: break-all;
      }
      .spinner {
        width: 14px;
        height: 14px;
        border: 2px solid currentColor;
        border-right-color: transparent;
        border-radius: 50%;
        animation: spin 0.6s linear infinite;
      }
      .hidden {
        display: none !important;
      }
      @keyframes spin {
        to { transform: rotate(360deg); }
      }
      @keyframes fadeIn {
        from { opacity: 0; }
        to { opacity: 1; }
      }
      @keyframes slideUp {
        from { opacity: 0; transform: translateY(12px) scale(0.98); }
        to { opacity: 1; transform: translateY(0) scale(1); }
      }
    `;

    const iconUrl = chrome.runtime.getURL('icons/icon-48.png');

    shadow.innerHTML = `
      <style>${inlineCss}</style>
      <div class="overlay" id="capture-overlay">
        <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title">
          <div class="header">
            <div class="brand">
              <img src="${iconUrl}" alt="Logo" class="brand-icon" id="brand-img">
              <svg class="brand-svg hidden" id="brand-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path>
                <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path>
              </svg>
              <span class="title" id="dialog-title">Info Triage Capture</span>
            </div>
            <button type="button" class="close-btn" id="close-btn" title="Close (Cancel)">&times;</button>
          </div>

          <div class="body" id="dialog-body">
            <div id="status-area"></div>

            <div class="field">
              <label for="route-select">Route</label>
              <select id="route-select">
                ${routesList.map(r => `<option value="${escapeHtml(r)}" ${r === defaultRoute ? 'selected' : ''}>${escapeHtml(r)}</option>`).join('')}
              </select>
            </div>

            <div class="field">
              <label for="text-editor">Text Content</label>
              <textarea id="text-editor" spellcheck="false" placeholder="Captured text or URL...">${escapeHtml(initialText)}</textarea>
              <span class="field-hint">Contains current web page URL and active text selection.</span>
            </div>

            <div class="field">
              <label for="intent-input">Intent (Optional)</label>
              <input type="text" id="intent-input" placeholder="e.g. read later, summarize, follow up..." spellcheck="false">
              <span class="field-hint">If specified, will append "Intent: &lt;intent&gt;" to the captured text.</span>
            </div>
          </div>

          <div class="footer" id="dialog-footer">
            <button type="button" class="btn btn-secondary" id="cancel-btn">Cancel</button>
            <button type="button" class="btn btn-primary" id="send-btn">
              <span class="spinner hidden" id="send-spinner"></span>
              <span id="send-btn-text">Send Capture</span>
            </button>
          </div>
        </div>
      </div>
    `;

    document.body.appendChild(host);

    // Fallback for image load error
    const brandImg = shadow.getElementById('brand-img');
    const brandSvg = shadow.getElementById('brand-svg');
    brandImg.addEventListener('error', () => {
      brandImg.classList.add('hidden');
      brandSvg.classList.remove('hidden');
    });

    const intentInput = shadow.getElementById('intent-input');
    const textEditor = shadow.getElementById('text-editor');
    const routeSelect = shadow.getElementById('route-select');
    const sendBtn = shadow.getElementById('send-btn');
    const cancelBtn = shadow.getElementById('cancel-btn');
    const closeBtn = shadow.getElementById('close-btn');
    const overlay = shadow.getElementById('capture-overlay');
    const statusArea = shadow.getElementById('status-area');
    const sendSpinner = shadow.getElementById('send-spinner');
    const sendBtnText = shadow.getElementById('send-btn-text');

    intentInput.focus();

    // Stop all key events inside shadow DOM modal from propagating out to host page (e.g. GitHub shortcuts)
    function stopEventLeakage(e) {
      if (e.key === 'Escape') return; // Allow Escape key to be handled by closeDialog
      e.stopPropagation();
      if (e.stopImmediatePropagation) {
        e.stopImmediatePropagation();
      }
    }

    ['keydown', 'keyup', 'keypress'].forEach((evt) => {
      shadow.addEventListener(evt, stopEventLeakage, true);
    });

    // Global capture phase listener to block external site key handlers when modal is open
    function handleGlobalKeyCapture(e) {
      const currentHost = document.getElementById('info-triage-capture-host');
      if (currentHost && currentHost.shadowRoot) {
        if (e.key === 'Escape') {
          closeDialog();
          return;
        }
        e.stopPropagation();
        if (e.stopImmediatePropagation) {
          e.stopImmediatePropagation();
        }
      }
    }

    window.addEventListener('keydown', handleGlobalKeyCapture, true);
    window.addEventListener('keyup', handleGlobalKeyCapture, true);
    window.addEventListener('keypress', handleGlobalKeyCapture, true);

    // Close logic
    function closeDialog() {
      window.removeEventListener('keydown', handleGlobalKeyCapture, true);
      window.removeEventListener('keyup', handleGlobalKeyCapture, true);
      window.removeEventListener('keypress', handleGlobalKeyCapture, true);
      host.remove();
    }

    cancelBtn.addEventListener('click', closeDialog);
    closeBtn.addEventListener('click', closeDialog);

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) {
        closeDialog();
      }
    });

    // Send Logic
    sendBtn.addEventListener('click', async () => {
      const selectedRoute = routeSelect.value;
      const textContent = textEditor.value;
      const intentText = intentInput.value.trim();

      let finalText = textContent;
      if (intentText) {
        finalText = `${textContent}\n\nIntent: ${intentText}`;
      }

      // Format ISO timestamp with local UTC offset
      const capturedAt = getIsoTimestampWithOffset();

      const payload = {
        route: selectedRoute,
        text: finalText,
        source: 'chrome-extension',
        captured_at: capturedAt
      };

      setLoading(true);
      statusArea.innerHTML = '';

      try {
        const response = await chrome.runtime.sendMessage({
          action: 'CAPTURE_API',
          payload: payload
        });

        setLoading(false);

        if (response && response.success) {
          showSuccessResult(response.data);
        } else {
          const err = response ? response.error : 'Network error or no response received';
          showErrorResult(err, response ? response.status : 0);
        }
      } catch (err) {
        setLoading(false);
        showErrorResult(err.message || 'Failed to communicate with background service worker', 0);
      }
    });

    function setLoading(isLoading) {
      sendBtn.disabled = isLoading;
      cancelBtn.disabled = isLoading;
      routeSelect.disabled = isLoading;
      textEditor.disabled = isLoading;
      intentInput.disabled = isLoading;

      if (isLoading) {
        sendSpinner.classList.remove('hidden');
        sendBtnText.textContent = 'Sending...';
      } else {
        sendSpinner.classList.add('hidden');
        sendBtnText.textContent = 'Send Capture';
      }
    }

    function showSuccessResult(data) {
      statusArea.innerHTML = `
        <div class="status-card success">
          <div class="status-title">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
              <polyline points="22 4 12 14.01 9 11.01"></polyline>
            </svg>
            Successfully Captured (HTTP 201)
          </div>
          <div class="meta-grid">
            <span class="meta-key">ID:</span>
            <span class="meta-val">${escapeHtml(String(data.id || 'N/A'))}</span>
            <span class="meta-key">Route:</span>
            <span class="meta-val">${escapeHtml(String(data.route || 'N/A'))}</span>
            <span class="meta-key">Status:</span>
            <span class="meta-val">${escapeHtml(String(data.status || 'N/A'))}</span>
            <span class="meta-key">Revision:</span>
            <span class="meta-val">${escapeHtml(String(data.revision ?? '1'))}</span>
          </div>
        </div>
      `;

      shadow.getElementById('dialog-footer').innerHTML = `
        <button type="button" class="btn btn-primary" id="done-btn">Done</button>
      `;
      shadow.getElementById('done-btn').addEventListener('click', closeDialog);
    }

    function showErrorResult(errorMsg, statusCode) {
      statusArea.innerHTML = `
        <div class="status-card error">
          <div class="status-title">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="12" y1="8" x2="12" y2="12"></line>
              <line x1="12" y1="16" x2="12.01" y2="16"></line>
            </svg>
            Capture Failed ${statusCode ? `(HTTP ${statusCode})` : ''}
          </div>
          <div>${escapeHtml(errorMsg)}</div>
        </div>
      `;
    }
  }

  function getIsoTimestampWithOffset() {
    const d = new Date();
    const tzo = -d.getTimezoneOffset();
    const dif = tzo >= 0 ? '+' : '-';
    const pad = (n) => String(Math.floor(Math.abs(n))).padStart(2, '0');
    return d.getFullYear() + '-' +
      pad(d.getMonth() + 1) + '-' +
      pad(d.getDate()) + 'T' +
      pad(d.getHours()) + ':' +
      pad(d.getMinutes()) + ':' +
      pad(d.getSeconds()) +
      dif + pad(tzo / 60) + ':' + pad(tzo % 60);
  }

  function escapeHtml(str) {
    if (typeof str !== 'string') return str;
    return str.replace(/[&<>"']/g, (m) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#039;'
    }[m]));
  }
})();
