const DEFAULTS = {
  url: '',
  port: '',
  token: '',
  transport: 'https',
  routes: ['info', 'job', 'clip', 'lang'],
  defaultRoute: 'info'
};

let currentRoutes = [...DEFAULTS.routes];

document.addEventListener('DOMContentLoaded', async () => {
  const form = document.getElementById('options-form');
  const urlInput = document.getElementById('url');
  const portInput = document.getElementById('port');
  const tokenInput = document.getElementById('token');
  const transportInput = document.getElementById('transport');
  const defaultRouteSelect = document.getElementById('default-route');
  const routesList = document.getElementById('routes-list');
  const newRouteInput = document.getElementById('new-route-input');
  const addRouteBtn = document.getElementById('add-route-btn');
  const resetBtn = document.getElementById('reset-btn');
  const testBtn = document.getElementById('test-btn');
  const toggleTokenBtn = document.getElementById('toggle-token-visibility');
  const statusToast = document.getElementById('status-toast');

  // Load options from storage
  const stored = await chrome.storage.sync.get(DEFAULTS);
  urlInput.value = stored.url || '';
  portInput.value = stored.port || '';
  tokenInput.value = stored.token || '';
  transportInput.value = stored.transport || 'https';
  currentRoutes = Array.isArray(stored.routes) && stored.routes.length > 0
    ? [...stored.routes]
    : [...DEFAULTS.routes];

  renderRoutes(stored.defaultRoute || DEFAULTS.defaultRoute);

  // Toggle password visibility
  toggleTokenBtn.addEventListener('click', () => {
    const isPassword = tokenInput.type === 'password';
    tokenInput.type = isPassword ? 'text' : 'password';
  });

  // Add route listener
  addRouteBtn.addEventListener('click', () => addRoute());
  newRouteInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      addRoute();
    }
  });

  function addRoute() {
    const route = newRouteInput.value.trim().toLowerCase();
    if (!route) return;
    if (!/^[a-z0-9_-]+$/.test(route)) {
      showToast('Route name can only contain letters, numbers, hyphens, and underscores.', 'error');
      return;
    }
    if (currentRoutes.includes(route)) {
      showToast(`Route "${route}" already exists.`, 'error');
      return;
    }
    currentRoutes.push(route);
    newRouteInput.value = '';
    renderRoutes(defaultRouteSelect.value || route);
  }

  function renderRoutes(selectedDefault) {
    routesList.innerHTML = '';
    defaultRouteSelect.innerHTML = '';

    if (!currentRoutes.includes(selectedDefault)) {
      selectedDefault = currentRoutes[0] || 'info';
    }

    currentRoutes.forEach((route) => {
      // Option for select
      const opt = document.createElement('option');
      opt.value = route;
      opt.textContent = route;
      if (route === selectedDefault) opt.selected = true;
      defaultRouteSelect.appendChild(opt);

      // Chip for route list
      const chip = document.createElement('div');
      chip.className = `route-chip ${route === selectedDefault ? 'is-default' : ''}`;
      chip.innerHTML = `
        <span>${escapeHtml(route)}</span>
        ${currentRoutes.length > 1 ? `<button type="button" class="remove-route" data-route="${escapeHtml(route)}" title="Remove route">&times;</button>` : ''}
      `;
      routesList.appendChild(chip);
    });

    // Remove route event delegation
    routesList.querySelectorAll('.remove-route').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        const toRemove = e.currentTarget.getAttribute('data-route');
        currentRoutes = currentRoutes.filter((r) => r !== toRemove);
        const newDefault = defaultRouteSelect.value === toRemove ? currentRoutes[0] : defaultRouteSelect.value;
        renderRoutes(newDefault);
      });
    });
  }

  defaultRouteSelect.addEventListener('change', () => {
    renderRoutes(defaultRouteSelect.value);
  });

  // Save options
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const settings = {
      url: urlInput.value.trim(),
      port: portInput.value.trim(),
      token: tokenInput.value.trim(),
      transport: transportInput.value,
      routes: currentRoutes,
      defaultRoute: defaultRouteSelect.value || currentRoutes[0] || 'info'
    };

    await chrome.storage.sync.set(settings);
    showToast('Options saved successfully!', 'success');
  });

  // Reset routes
  resetBtn.addEventListener('click', async () => {
    currentRoutes = [...DEFAULTS.routes];
    renderRoutes(DEFAULTS.defaultRoute);
    showToast('Reset routes to initial default categories.', 'success');
  });

  // Test API Connection
  testBtn.addEventListener('click', async () => {
    const testSpinner = document.getElementById('test-spinner');
    testSpinner.classList.remove('hidden');
    testBtn.disabled = true;

    const testConfig = {
      url: urlInput.value.trim(),
      port: portInput.value.trim(),
      token: tokenInput.value.trim(),
      transport: transportInput.value,
      routes: currentRoutes,
      defaultRoute: defaultRouteSelect.value || currentRoutes[0] || 'info'
    };

    try {
      const response = await chrome.runtime.sendMessage({
        action: 'TEST_API_CONNECTION',
        config: testConfig
      });

      if (response && response.success) {
        showToast(`Connection successful! Server responded with HTTP ${response.status}.`, 'success');
      } else {
        const errorMsg = response?.error || 'Connection failed';
        showToast(`Test failed: ${errorMsg}`, 'error');
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
    }, 4000);
  }

  function escapeHtml(str) {
    return str.replace(/[&<>"']/g, (m) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#039;'
    }[m]));
  }
});
