# Info Triage Capture - Chrome Extension

A professional, minimalistic, and modern Chrome Extension (Manifest V3) for capturing web page URLs, highlighted text selections, and user intent, and submitting them directly to the **Info Triage Capture API**.

---

## 🌟 Key Features

- **One-Click Page & Selection Capture**: Click the extension icon on any active tab to capture the page URL and any highlighted text selection instantly.
- **Injected Shadow DOM Modal**: Renders a clean, calm, modern modal dialog overlay centered on screen over a blurred backdrop (`backdrop-filter`).
- **Editable Fields**:
  - **Route**: Drop-down selector populated from your configured route list.
  - **Text**: Multi-line editor pre-filled with the web page URL and active text selection.
  - **Intent**: Optional intent input field (e.g. `summarize`, `read later`, `follow up`).
- **Smart Intent Formatting**: If an intent is provided, it is automatically appended to the text parameter as `\n\nIntent: <intent_text>`. If empty, nothing is appended.
- **Strict API Schema Compliance**: Only sends the required API fields (`route`, `text`, `source`, `captured_at`) with ISO-8601 UTC offset timestamps.
- **Options Management (`chrome.storage.sync`)**:
  - `INFO_TRIAGE_CAPTURE_URL`
  - `INFO_TRIAGE_CAPTURE_PORT`
  - `INFO_TRIAGE_CAPTURE_TOKEN` (Uses password input `<input type="password">`)
  - Transport protocol (`https` / `http`)
  - Dynamic Routes Manager (`info`, `job`, `clip`, `lang`, with custom route addition/deletion and default route selection)
  - Interactive "Test Connection" tool on options page

---

## 📁 File Structure

```
info-triage-capture-extension/
├── manifest.json         # Manifest V3 specification
├── background.js          # Service worker for API calls & tab event handling
├── options/
│   ├── options.html      # Configuration page UI
│   ├── options.css       # Options styling
│   └── options.js        # Syncs options & manages route list
├── content/
│   ├── content.js        # Content script injecting Shadow DOM modal dialog
│   └── dialog.css        # Modal overlay styles (isolated in Shadow DOM)
├── icons/
│   ├── book.png          # Original user icon
│   ├── icon-16.png       # 16x16 icon
│   ├── icon-32.png       # 32x32 icon
│   ├── icon-48.png       # 48x48 icon
│   └── icon-128.png      # 128x128 icon
└── README.md             # Plug-in documentation
```

---

## 🚀 Installation

1. Clone or download this repository to your local computer.
2. Open **Google Chrome** and navigate to `chrome://extensions`.
3. Enable **Developer mode** in the upper-right corner.
4. Click **Load unpacked** in the top-left menu.
5. Select the `info-triage-capture-extension` project folder.

---

## ⚙️ Configuration & Options Page

Right-click the extension icon in Chrome and click **Options** (or go to `chrome://extensions` → Details → Extension options).

Enter your server credentials:
- **INFO_TRIAGE_CAPTURE_URL**: e.g., `https:// <your-server-domain>` (or ` <your-server-domain>`)
- **INFO_TRIAGE_CAPTURE_PORT**: e.g., `8443`
- **INFO_TRIAGE_CAPTURE_TOKEN**: e.g., ` <your-token-here>=`
- **Transport**: `https` or `http`
- **Routes**: Manage allowed capture routes (`info`, `job`, `clip`, `lang`).

---

## 📡 API Specification

### Endpoint
```http
POST <transport>://<INFO_TRIAGE_CAPTURE_URL>:<INFO_TRIAGE_CAPTURE_PORT>/capture
Content-Type: application/json
Authorization: Bearer <INFO_TRIAGE_CAPTURE_TOKEN>
```

### JSON Request Payload
> **Note:** Unrecognized fields are rejected by the server.

```json
{
  "route": "info",
  "text": "https://example.com/article\n\nHighlighted text sample\n\nIntent: summarize",
  "source": "chrome-extension",
  "captured_at": "2026-08-18T15:21:30+01:00"
}
```

### Response Schema

#### Success (201 Created)
```json
{
  "route": "info",
  "id": "cap_123456789",
  "revision": 1,
  "status": "success"
}
```

#### Error Responses (4xx / 5xx)
```json
{
  "error": "human-readable message"
}
```
