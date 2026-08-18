const DEFAULTS = {
  url: '',
  port: '',
  token: '',
  transport: 'https',
  routes: ['info', 'job', 'clip', 'lang'],
  defaultRoute: 'info'
};

// Listen for action icon click in toolbar
chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id || !tab.url || tab.url.startsWith('chrome://') || tab.url.startsWith('chrome-extension://')) {
    return;
  }

  try {
    // Try sending message to existing content script
    await chrome.tabs.sendMessage(tab.id, { action: 'OPEN_CAPTURE_DIALOG' });
  } catch (err) {
    // Content script not ready; dynamically inject script and retry
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ['content/content.js']
      });
      await chrome.tabs.sendMessage(tab.id, { action: 'OPEN_CAPTURE_DIALOG' });
    } catch (injectErr) {
      console.error('Failed to inject capture script:', injectErr);
    }
  }
});

// Helper to construct API endpoint cleanly
function buildEndpoint(url, port, transport) {
  let cleanHost = (url || '').trim();
  cleanHost = cleanHost.replace(/^https?:\/\//i, '');
  cleanHost = cleanHost.replace(/\/+$/, '');
  cleanHost = cleanHost.replace(/:\d+$/, '');
  const cleanPort = (port || '8443').trim();
  const cleanTransport = (transport || 'https').trim();
  return `${cleanTransport}://${cleanHost}:${cleanPort}/capture`;
}

// Handle runtime messages from content script & options page
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === 'CAPTURE_API') {
    handleCaptureApi(message.payload, sendResponse);
    return true; // Keep message channel open for async response
  }

  if (message.action === 'TEST_API_CONNECTION') {
    handleTestConnection(message.config, sendResponse);
    return true; // Keep message channel open for async response
  }
});

async function handleCaptureApi(payload, sendResponse) {
  try {
    const config = await chrome.storage.sync.get(DEFAULTS);
    if (!config.url || !config.port || !config.token) {
      sendResponse({
        success: false,
        status: 401,
        error: 'Missing API configuration. Please configure URL, Port, and Bearer Token in the extension Options page.'
      });
      return;
    }

    const endpoint = buildEndpoint(config.url, config.port, config.transport);

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 20000);

    const res = await fetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${config.token.trim()}`
      },
      body: JSON.stringify(payload),
      signal: controller.signal
    });

    clearTimeout(timeoutId);

    let data = {};
    try {
      data = await res.json();
    } catch (e) {
      data = {};
    }

    if (res.ok && res.status === 201) {
      sendResponse({
        success: true,
        status: res.status,
        data: data
      });
    } else {
      const errorMsg = data.error || `HTTP ${res.status} ${res.statusText || 'Server Error'}`;
      sendResponse({
        success: false,
        status: res.status,
        error: errorMsg,
        data: data
      });
    }
  } catch (err) {
    let msg = err.message;
    if (err.name === 'AbortError') {
      msg = 'Request timed out after 20 seconds.';
    }
    sendResponse({
      success: false,
      status: 0,
      error: msg
    });
  }
}

async function handleTestConnection(providedConfig, sendResponse) {
  try {
    const stored = await chrome.storage.sync.get(DEFAULTS);
    const config = providedConfig || stored;
    const endpoint = buildEndpoint(config.url, config.port, config.transport);

    // Send a test ping payload with route "info"
    const now = new Date();
    const tzo = -now.getTimezoneOffset();
    const dif = tzo >= 0 ? '+' : '-';
    const pad = (n) => String(Math.floor(Math.abs(n))).padStart(2, '0');
    const isoTimestamp = now.getFullYear() + '-' +
      pad(now.getMonth() + 1) + '-' +
      pad(now.getDate()) + 'T' +
      pad(now.getHours()) + ':' +
      pad(now.getMinutes()) + ':' +
      pad(now.getSeconds()) +
      dif + pad(tzo / 60) + ':' + pad(tzo % 60);

    const testPayload = {
      route: config.defaultRoute || 'info',
      text: 'Test connection from Info Triage Capture options page',
      source: 'chrome-extension',
      captured_at: isoTimestamp
    };

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);

    const res = await fetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${config.token}`
      },
      body: JSON.stringify(testPayload),
      signal: controller.signal
    });

    clearTimeout(timeoutId);

    let data = {};
    try {
      data = await res.json();
    } catch (e) {}

    if (res.ok && res.status === 201) {
      sendResponse({ success: true, status: res.status, data });
    } else {
      sendResponse({
        success: false,
        status: res.status,
        error: data.error || `HTTP ${res.status} ${res.statusText}`
      });
    }
  } catch (err) {
    sendResponse({
      success: false,
      status: 0,
      error: err.message || 'Network error'
    });
  }
}
