# Info Triage Capture - Chrome Extension

A professional, minimalistic, and modern Chrome Extension (Manifest V3) for capturing web page URLs, highlighted text selections, and user intent, and submitting or updating them directly in the **Info Triage Capture API**.

---

## 🌟 Key Features

- **One-Click Page & Selection Capture**: Click the extension icon or use `Command+Shift+K` (Mac) / `Ctrl+Shift+K` (Win) on any active tab.
- **Selection Prefixing & Formatting**: Selection text is automatically formatted as:
  ```
  https://example.com/article

  Selection:
  "Highlighted text sample"
  ```
- **Live Update & Rewrite Support**:
  - After initial submission, the modal stays open with editable fields.
  - The primary button changes to **"Update Capture"**.
  - Editing fields and clicking **"Update Capture"** sends the previous `id` in the API payload, rewriting the item on the server.
- **Isolated Shadow DOM Overlay**: Renders an isolated modal overlay on top of pages (intercepting key leakage to host sites like GitHub).
- **Options Management (`chrome.storage.sync`)**:
  - `INFO_TRIAGE_CAPTURE_URL`
  - `INFO_TRIAGE_CAPTURE_PORT`
  - `INFO_TRIAGE_CAPTURE_TOKEN` (`<input type="password">`)
  - Transport protocol (`https` / `http`)
  - Dynamic Routes Manager (`info`, `job`, `clip`, `lang`, with custom routes)

---

## 📡 API Payload Format

### Initial Capture
```json
{
  "route": "info",
  "text": "https://github.com/rasbt/mini-coding-agent\n\nSelection:\n\"Minimal coding agent harness\"\n\nIntent: summarize",
  "source": "chrome-extension",
  "captured_at": "2026-08-18T17:10:00+01:00"
}
```

### Update Request (Passes `id`)
```json
{
  "id": "job/2026-08-18_4",
  "route": "job",
  "text": "https://github.com/rasbt/mini-coding-agent\n\nSelection:\n\"Minimal coding agent harness\"\n\nIntent: follow up",
  "source": "chrome-extension",
  "captured_at": "2026-08-18T17:10:30+01:00"
}
```
