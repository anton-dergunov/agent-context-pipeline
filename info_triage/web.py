"""Operational dashboard, health endpoint, and the transport-independent ingest."""

import hmac
import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .capture_api import MAX_CAPTURE_REQUEST_BYTES, CaptureError, capture
from .processing import ProcessingCoordinator
from .storage import STATUSES, CaptureStore

_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_FILES = {
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
}


class WebHandler(BaseHTTPRequestHandler):
    store: CaptureStore
    coordinator: ProcessingCoordinator
    capture_token: str
    routes: tuple[str, ...]

    def do_GET(self) -> None:
        request = urlsplit(self.path)
        if request.path == "/health":
            self._send_text("Info Triage is running\n")
            return
        if request.path in _STATIC_FILES:
            self._send_static(*_STATIC_FILES[request.path])
            return
        if request.path != "/":
            self.send_error(404)
            return

        query = parse_qs(request.query)
        if query.get("view", [None])[0] == "processors":
            selected_view = "processors"
        else:
            selected_view = query.get("status", ["ready"])[0]
            if selected_view not in STATUSES:
                selected_view = "ready"
        body = self._dashboard(selected_view).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/capture":
            self._send_json(404, {"error": "not found"})
            return
        try:
            body = self._capture_body()
            status, response = capture(self.store, self.coordinator, body, self.routes)
        except CaptureError as error:
            self._send_json(error.status, {"error": str(error)})
        except Exception as error:
            self.log_error("capture failed: %s", type(error).__name__)
            self._send_json(500, {"error": f"capture failed: {type(error).__name__}"})
        else:
            self._send_json(status, response)

    def _capture_body(self) -> bytes:
        """Authorize and read one request, refusing anything unbounded."""
        if not self._authorized():
            raise CaptureError("a valid bearer token is required", status=401)
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if content_type != "application/json":
            raise CaptureError("Content-Type must be application/json", status=415)
        length = self.headers.get("Content-Length")
        # BaseHTTPRequestHandler will not de-chunk, and reading an unbounded
        # length is the denial of service. Refuse both before touching rfile.
        if length is None or not length.isdigit():
            raise CaptureError("Content-Length is required", status=411)
        if int(length) > MAX_CAPTURE_REQUEST_BYTES:
            raise CaptureError(
                f"body is over the {MAX_CAPTURE_REQUEST_BYTES} byte limit", status=413
            )
        return self.rfile.read(int(length))

    def _authorized(self) -> bool:
        scheme, _, presented = (self.headers.get("Authorization") or "").partition(" ")
        if scheme != "Bearer":
            return False
        # Bytes, because compare_digest raises on a non-ASCII str.
        return hmac.compare_digest(
            presented.strip().encode("utf-8"), self.capture_token.encode("utf-8")
        )

    def _send_json(self, status: int, payload: dict) -> None:
        body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, filename: str, content_type: str) -> None:
        body = (_STATIC_DIR / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, content: str) -> None:
        body = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dashboard(self, selected_view: str) -> str:
        counts = self.store.status_counts()
        tabs = "".join(
            (
                f'<a class="tab{" active" if status == selected_view else ""}" '
                f'href="/?status={status}">{status.title()} {counts[status]}</a>'
            )
            for status in STATUSES
        )
        tabs += (
            f'<a class="tab{" active" if selected_view == "processors" else ""}" '
            'href="/?view=processors">Processors</a>'
        )

        if selected_view == "processors":
            heading, rows = self._processor_table()
            log_hint = (
                '<p class="log-hint">Failure details: '
                '<code>data/logs/processor-runs.jsonl</code>. Copy a log key and use '
                '<code>grep -F</code>.</p>'
            )
        else:
            heading, rows = self._item_table(selected_view)
            log_hint = ""

        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="10">
  <title>Info Triage</title>
  <link rel="icon" href="/favicon.ico">
  <link rel="apple-touch-icon" href="/apple-touch-icon.png">
  <style>
    body {{ font: 15px system-ui, sans-serif; margin: 2rem; color: #202124; }}
    h1 {{ margin: 0 0 1.5rem; }}
    .tabs {{ display: flex; gap: .5rem; margin-bottom: 1.25rem; flex-wrap: wrap; }}
    .tab {{ padding: .55rem .8rem; border: 1px solid #ccc; border-radius: .4rem;
            color: inherit; text-decoration: none; }}
    .tab.active {{ background: #202124; color: white; border-color: #202124; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ padding: .65rem; border-bottom: 1px solid #ddd; text-align: left;
              vertical-align: top; }}
    th {{ white-space: nowrap; }}
    .detail {{ color: #555; margin-top: .25rem; }}
    .error, .failed {{ color: #b00020; margin-top: .25rem; }}
    .partial {{ color: #8a5700; margin-top: .25rem; }}
    .reason {{ margin-bottom: .4rem; }}
    code {{ font-size: .9em; white-space: nowrap; }}
    .log-hint {{ color: #555; margin: 0 0 1rem; }}
    .empty {{ color: #777; text-align: center; padding: 2rem; }}
  </style>
</head>
<body>
  <h1>Info Triage</h1>
  <nav class="tabs">{tabs}</nav>
  {log_hint}
  <table>
    <thead>{heading}</thead>
    <tbody>{"".join(rows)}</tbody>
  </table>
</body>
</html>
"""

    def _item_table(self, selected_status: str) -> tuple[str, list[str]]:
        heading = (
            "<tr><th>Route</th><th>ID</th><th>Created</th><th>Updated</th>"
            "<th>Category</th><th>Rev</th><th>Message</th></tr>"
        )

        rows = []
        for item in self.store.items_with_status(selected_status):
            # Item names restart per route, so the route is part of the name.
            item_id = self.store.item_name(item["created_at"], item["local_id"])
            message = html.escape(item["short_text"] or "")
            if item["processing_step"]:
                message += (
                    '<div class="detail">Step: '
                    f"{html.escape(item['processing_step'])}</div>"
                )
            if item["error"]:
                message += f'<div class="error">{html.escape(item["error"])}</div>'
            message += self._problem_details(item["problems"])
            rows.append(
                "<tr>"
                f"<td>{html.escape(item['route'])}</td>"
                f"<td>{html.escape(item_id)}</td>"
                f"<td>{html.escape(self._display_time(item['created_at']))}</td>"
                f"<td>{html.escape(self._display_time(item['updated_at']))}</td>"
                f"<td>{html.escape(item['category'] or '—')}</td>"
                f"<td>{item['revision']}</td>"
                f"<td>{message}</td>"
                "</tr>"
            )
        if not rows:
            rows.append('<tr><td colspan="7" class="empty">No items</td></tr>')
        return heading, rows

    @staticmethod
    def _problem_details(problems: str | None) -> str:
        """Show what enrichment could not do for an item that was delivered anyway."""
        if not problems:
            return ""
        try:
            records = json.loads(problems)
        except json.JSONDecodeError:
            return ""
        if not isinstance(records, list):
            return ""
        rows = []
        for record in records:
            if not isinstance(record, dict):
                continue
            outcome = str(record.get("outcome", "partial"))
            css = "failed" if outcome == "failed" else "partial"
            text = f"{record.get('step', '?')} {outcome} — {record.get('reason', '?')}"
            target = record.get("target")
            if target:
                text += f" — {target}"
            rows.append(f'<div class="{html.escape(css)}">{html.escape(text)}</div>')
        return "".join(rows)

    def _processor_table(self) -> tuple[str, list[str]]:
        heading = (
            "<tr><th>Processor</th><th>Runs</th><th>Succeeded</th>"
            "<th>Partial</th><th>Failed</th><th>Reasons and log keys</th></tr>"
        )
        rows = []
        for processor in self.store.processor_statistics():
            name = processor["processor"]
            reasons = []
            for reason in processor["reasons"]:
                lookup_key = f"{name}:{reason['reason']}"
                outcome = reason["outcome"]
                reasons.append(
                    f'<div class="reason {html.escape(outcome)}">'
                    f"{html.escape(outcome.title())} — "
                    f"{reason['occurrences']} — "
                    f"<code>{html.escape(lookup_key)}</code></div>"
                )
            reason_content = "".join(reasons) or "—"
            rows.append(
                "<tr>"
                f"<td>{html.escape(name)}</td>"
                f"<td>{processor['runs']}</td>"
                f"<td>{processor['succeeded']}</td>"
                f"<td>{processor['partial']}</td>"
                f"<td>{processor['failed']}</td>"
                f"<td>{reason_content}</td>"
                "</tr>"
            )
        if not rows:
            rows.append(
                '<tr><td colspan="6" class="empty">No processors configured</td></tr>'
            )
        return heading, rows

    @staticmethod
    def _display_time(timestamp: str) -> str:
        return timestamp.replace("T", " ")[:16]

    def log_message(self, format, *args) -> None:
        return


class _WebServer(ThreadingHTTPServer):
    # Threaded so that a slow capture — a 20 MiB attachment, or a half-open
    # client — cannot block GET /health, which is the container healthcheck and
    # the deployment gate.
    daemon_threads = True


def start_web_server(
    store: CaptureStore,
    coordinator: ProcessingCoordinator,
    port: int,
    *,
    capture_token: str,
    routes: tuple[str, ...],
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    handler = type(
        "ConfiguredWebHandler",
        (WebHandler,),
        {
            "store": store,
            "coordinator": coordinator,
            "capture_token": capture_token,
            "routes": routes,
        },
    )
    server = _WebServer(("0.0.0.0", port), handler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="info-triage-web",
        daemon=True,
    )
    thread.start()
    return server, thread
