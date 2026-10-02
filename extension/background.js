// Settings live in chrome.storage.sync, so they follow the browser profile.
// The route list the server last reported lives in chrome.storage.local: it is
// a cache, refreshed in the background, and the dialog never waits for it.
const SETTINGS_DEFAULTS = {
  serverUrl: '',
  token: '',
  // Empty means "whatever the server calls its default".
  defaultRoute: ''
};
const ROUTES_CACHE_KEY = 'routesCache';

chrome.runtime.onInstalled.addListener(() => { refreshRoutes(); });
chrome.runtime.onStartup.addListener(() => { refreshRoutes(); });

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

// The server's base address, as typed on the options page, without a trailing
// slash. Null when it is missing or not an http(s) URL.
function baseUrl(serverUrl) {
  const value = (serverUrl || '').trim().replace(/\/+$/, '');
  return /^https?:\/\/[^/\s]+/i.test(value) ? value : null;
}

// One request to the server. Resolves to {success, status, data, error}; never throws.
async function callServer(settings, method, path, body, timeoutMs) {
  const base = baseUrl(settings.serverUrl);
  if (!base || !(settings.token || '').trim()) {
    return {
      success: false,
      status: 0,
      error: 'Set the server URL (with http:// or https://) and the capture token on the extension\'s options page.'
    };
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const headers = { 'Authorization': `Bearer ${settings.token.trim()}` };
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
    }
    const res = await fetch(`${base}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal
    });
    let data = {};
    try {
      data = await res.json();
    } catch (e) {
      data = {};
    }
    if (res.ok) {
      return { success: true, status: res.status, data };
    }
    return {
      success: false,
      status: res.status,
      error: data.error || `HTTP ${res.status} ${res.statusText || 'Server Error'}`,
      data
    };
  } catch (err) {
    return {
      success: false,
      status: 0,
      error: err.name === 'AbortError'
        ? `Request timed out after ${timeoutMs / 1000} seconds.`
        : (err.message || 'Network error')
    };
  } finally {
    clearTimeout(timeoutId);
  }
}

// Ask the server which routes it has and remember the answer. SETTINGS lets the
// options page try values it has not saved yet; a trial run is not cached.
async function refreshRoutes(settings) {
  const trial = settings !== undefined;
  const effective = trial ? settings : await chrome.storage.sync.get(SETTINGS_DEFAULTS);
  const response = await callServer(effective, 'GET', '/routes', undefined, 10000);
  if (response.success && Array.isArray(response.data.routes) && response.data.routes.length > 0) {
    const routes = {
      routes: response.data.routes.map(String),
      default: String(response.data.default || response.data.routes[0]),
      fetchedAt: new Date().toISOString()
    };
    if (!trial) {
      await chrome.storage.local.set({ [ROUTES_CACHE_KEY]: routes });
    }
    return { success: true, status: response.status, routes };
  }
  if (response.success) {
    return { success: false, status: response.status, error: 'The server answered without a route list.' };
  }
  return response;
}

// Handle runtime messages from content script & options page
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === 'CAPTURE_API') {
    chrome.storage.sync.get(SETTINGS_DEFAULTS)
      .then((settings) => callServer(settings, 'POST', '/capture', message.payload, 20000))
      .then(sendResponse);
    return true; // Keep message channel open for async response
  }

  if (message.action === 'REFRESH_ROUTES') {
    refreshRoutes(message.settings).then(sendResponse);
    return true;
  }
});
