# Info Triage Capture — browser extension

Sends the page you are on, any text you have selected and a note to your Info Triage server, where
it becomes an item like one shared to a Telegram bot. Works in Chrome and other Chromium browsers.

## Install

1. Download `info-triage-capture-<version>.zip` from the repository's
   [Releases](https://github.com/anton-dergunov/agent-context-pipeline/releases) page and unpack it
   somewhere it can stay. Working from a clone instead? Use this `extension/` directory as it is.
2. Open `chrome://extensions` and switch on **Developer mode**.
3. Choose **Load unpacked** and select the unpacked `info-triage-capture` folder.
4. Open the extension's **Options** and fill in:
   - **Server URL**: where the dashboard opens, such as `http://192.168.1.10:8000`, or the HTTPS
     address when you reach the server over a private network such as Tailscale.
   - **Capture token**: the value of `INFO_TRIAGE_CAPTURE_TOKEN` in the server's `.env`.
5. Press **Test Connection**. It lists the server's routes and captures nothing. Then **Save Changes**.

Chrome does not install packaged extensions from outside the Chrome Web Store, which is why this is
loaded unpacked. To update, replace the folder's contents with a newer release and press the reload
arrow on the extension's card.

## Use

Press `Ctrl+Shift+K` (`Command+Shift+K` on a Mac) or click the toolbar icon. The dialog opens with
the page address, and the selected text if there is any:

```text
https://example.com/article

Selection:
"the text you had selected"
```

Pick a route, add a note under **Intent** if you want one, and send. The dialog stays open
afterwards: edit anything and **Update Capture** rewrites the same item on the server, including
moving it to another route. The shortcut can be changed at `chrome://extensions/shortcuts`.

## Routes

The routes are the server's, as defined in its `config.yaml`. The extension asks for them when the
browser starts and when the options are saved, and keeps the answer, so the dialog opens at once and
never waits for the network. It asks again behind the open dialog and updates the list if the server's
has changed. **Preselect** on the options page chooses which route the dialog starts on; left on
*server default*, it follows the first route the server declares.

## What it sends

```http
GET  <server>/routes     Authorization: Bearer <token>
POST <server>/capture    Authorization: Bearer <token>

{"route": "info", "text": "https://example.com/article\n\nIntent: read later",
 "source": "chrome-extension", "captured_at": "2026-08-18T17:10:00+01:00"}
```

An update sends the same body with the item's `id` in place of `captured_at`. The contract is
described in [`docs/architecture/overview.md`](../docs/architecture/overview.md#post-capture). The
token is stored in the browser's extension storage and sent only to the server URL you entered.

## Credits

The icon is from Flaticon: [Reading icons created by Magnific - Flaticon](https://www.flaticon.com/free-icons/reading).
