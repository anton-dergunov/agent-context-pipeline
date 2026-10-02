const SETTINGS_DEFAULTS = {
  serverUrl: '',
  token: '',
  // Empty means "whatever the server calls its default".
  defaultRoute: ''
};
const ROUTES_CACHE_KEY = 'routesCache';
// What an earlier version stored; removed on the next save.
const OBSOLETE_SETTINGS = ['url', 'port', 'transport', 'routes'];

document.addEventListener('DOMContentLoaded', async () => {
  const form = document.getElementById('options-form');
  const urlInput = document.getElementById('server-url');
  const tokenInput = document.getElementById('token');
  const defaultRouteSelect = document.getElementById('default-route');
  const routesList = document.getElementById('routes-list');
  const testBtn = document.getElementById('test-btn');
  const toggleTokenBtn = document.getElementById('toggle-token-visibility');
  const statusToast = document.getElementById('status-toast');

  const stored = await chrome.storage.sync.get(SETTINGS_DEFAULTS);
  const cached = await chrome.storage.local.get(ROUTES_CACHE_KEY);
  urlInput.value = stored.serverUrl || '';
  tokenInput.value = stored.token || '';
  renderRoutes(cached[ROUTES_CACHE_KEY] || null, stored.defaultRoute || '');

  toggleTokenBtn.addEventListener('click', () => {
    tokenInput.type = tokenInput.type === 'password' ? 'text' : 'password';
  });

  function currentSettings() {
    return {
      serverUrl: urlInput.value.trim().replace(/\/+$/, ''),
      token: tokenInput.value.trim(),
      defaultRoute: defaultRouteSelect.value
    };
  }

  // The route list is the server's; this page only shows it and picks which one
  // the dialog preselects.
  function renderRoutes(known, selected) {
    const routes = known && Array.isArray(known.routes) ? known.routes : [];
    defaultRouteSelect.innerHTML = '';
    const serverDefault = document.createElement('option');
    serverDefault.value = '';
    serverDefault.textContent = routes.length ? `server default (${known.default})` : 'server default';
    defaultRouteSelect.appendChild(serverDefault);
    routes.forEach((route) => {
      const option = document.createElement('option');
      option.value = route;
      option.textContent = route;
      defaultRouteSelect.appendChild(option);
    });
    defaultRouteSelect.value = routes.includes(selected) ? selected : '';

    routesList.innerHTML = '';
    routes.forEach((route) => {
      const chip = document.createElement('div');
      chip.className = `route-chip ${route === known.default ? 'is-default' : ''}`;
      chip.textContent = route;
      routesList.appendChild(chip);
    });
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const settings = currentSettings();
    if (!/^https?:\/\/[^/\s]+/i.test(settings.serverUrl)) {
      showToast('The server URL must start with http:// or https://', 'error');
      return;
    }
    await chrome.storage.sync.set(settings);
    await chrome.storage.sync.remove(OBSOLETE_SETTINGS);
    // Load the routes with what was just saved, so the dialog has them at once.
    const response = await chrome.runtime.sendMessage({ action: 'REFRESH_ROUTES' });
    if (response && response.success) {
      renderRoutes(response.routes, settings.defaultRoute);
      showToast('Saved. Routes loaded from the server.', 'success');
    } else {
      showToast(`Saved, but the server could not be reached: ${response?.error || 'no response'}`, 'error');
    }
  });

  // Asks the server for its routes with the values on screen, saved or not. This
  // checks the address and the token without capturing anything.
  testBtn.addEventListener('click', async () => {
    const testSpinner = document.getElementById('test-spinner');
    testSpinner.classList.remove('hidden');
    testBtn.disabled = true;

    try {
      const response = await chrome.runtime.sendMessage({
        action: 'REFRESH_ROUTES',
        settings: currentSettings()
      });

      if (response && response.success) {
        renderRoutes(response.routes, defaultRouteSelect.value);
        showToast(`Connected. Routes: ${response.routes.routes.join(', ')}.`, 'success');
      } else {
        showToast(`Test failed: ${response?.error || 'Connection failed'}`, 'error');
      }
    } catch (err) {
      showToast(`Test error: ${err.message}`, 'error');
    } finally {
      testSpinner.classList.add('hidden');
      testBtn.disabled = false;
    }
  });

  const shortcutsLink = document.getElementById('shortcuts-link');
  if (shortcutsLink) {
    shortcutsLink.style.cursor = 'pointer';
    shortcutsLink.addEventListener('click', () => {
      chrome.tabs.create({ url: 'chrome://extensions/shortcuts' });
    });
  }

  function showToast(msg, type) {
    statusToast.textContent = msg;
    statusToast.className = `toast ${type}`;
    setTimeout(() => {
      if (statusToast.textContent === msg) {
        statusToast.className = 'toast hidden';
      }
    }, 5000);
  }
});
