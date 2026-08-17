import base64
import io
import json
import tempfile
import unittest
from pathlib import Path

from info_triage.capture_api import MAX_CAPTURE_FILE_BYTES, MAX_CAPTURE_REQUEST_BYTES
from info_triage.models import ProcessingIssue, ProcessingProblem, ProcessingResult
from info_triage.storage import CaptureStore
from info_triage.web import WebHandler


class ImmediateCoordinator:
    """Stands in for the worker: promote on submit, so an item lands at once."""

    def __init__(self, store):
        self.store = store

    def submit(self, item):
        self.store.promote_if_current(item)


class WebTests(unittest.TestCase):
    def test_health_and_dashboard(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))

            class RecordingHandler(WebHandler):
                def _send_text(self, content):
                    self.sent_text = content

            handler = object.__new__(RecordingHandler)
            handler.store = store
            handler.path = "/health"
            handler.do_GET()
            self.assertEqual(handler.sent_text, "Info Triage is running\n")
            self.assertIn("Info Triage", handler._dashboard("ready"))

    def test_processor_dashboard_shows_totals_reasons_and_grep_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))
            store.register_processors(
                ("text-cleaning", "url-resolution", "voice-<transcription>")
            )
            store.record_processor_run("text-cleaning", "succeeded", ())
            store.record_processor_run(
                "url-resolution",
                "partial",
                (
                    ProcessingIssue("request-error", "offline", "https://t.co/a"),
                    ProcessingIssue("request-error", "offline", "https://t.co/b"),
                ),
            )

            handler = object.__new__(WebHandler)
            handler.store = store
            page = handler._dashboard("processors")

            self.assertIn("Processors", page)
            self.assertIn("data/logs/processor-runs.jsonl", page)
            self.assertIn("url-resolution:request-error", page)
            self.assertIn("Partial — 2", page)
            self.assertIn("text-cleaning", page)
            self.assertIn("voice-&lt;transcription&gt;", page)
            zero_row = next(
                row
                for row in store.processor_statistics()
                if row["processor"] == "voice-<transcription>"
            )
            self.assertEqual(zero_row["runs"], 0)


    def test_a_delivered_item_shows_the_problems_it_carried(self):
        """Items ship even when enrichment fails, so the dashboard has to say so."""
        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))
            item = store.capture("info", 10, 1, "a note", received_at="2026-08-09T10:00:00+00:00")
            store.promote_if_current(
                item,
                ProcessingResult(
                    message_markdown="a note",
                    problems=[
                        ProcessingProblem(
                            "url-resolution",
                            "partial",
                            ProcessingIssue("redirect-limit", "too many hops", "https://t.co/<a>"),
                        )
                    ],
                ),
            )

            handler = object.__new__(WebHandler)
            handler.store = store
            page = handler._dashboard("ready")

            self.assertIn("url-resolution partial — redirect-limit", page)
            self.assertIn("https://t.co/&lt;a&gt;", page)


if __name__ == "__main__":
    unittest.main()


class CaptureEndpointTests(unittest.TestCase):
    """POST /capture: the ingest contract Telegram is only one client of."""

    def handler(self, store, body, *, token="secret-token", content_type="application/json"):
        class RecordingHandler(WebHandler):
            def __init__(self):
                self.responses = []
                self.headers = {}
                self.wfile = io.BytesIO()

            def send_response(self, status):
                self.status = status

            def send_header(self, *args):
                return

            def end_headers(self):
                return

        handler = RecordingHandler()
        handler.store = store
        handler.coordinator = ImmediateCoordinator(store)
        handler.capture_token = "secret-token"
        handler.routes = store.routes
        handler.path = "/capture"
        handler.rfile = io.BytesIO(body)
        handler.headers = {"Content-Length": str(len(body))}
        if token is not None:
            handler.headers["Authorization"] = f"Bearer {token}"
        if content_type is not None:
            handler.headers["Content-Type"] = content_type
        return handler

    def post(self, store, payload, **kwargs):
        handler = self.handler(store, json.dumps(payload).encode("utf-8"), **kwargs)
        handler.do_POST()
        return handler.status, json.loads(handler.wfile.getvalue())

    def test_a_text_capture_lands_as_an_item_in_its_route(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))
            status, body = self.post(
                store, {"route": "job", "source": "cli", "text": "https://example.com/posting"}
            )

            self.assertEqual(status, 201)
            self.assertEqual(body["route"], "job")
            item = Path(temporary) / "inbox" / "job" / body["id"]
            self.assertEqual(
                (item / "capture" / "message.md").read_text(), "https://example.com/posting"
            )
            # Telegram's own payload shape, so nothing downstream needs a second
            # code path to read it.
            payload = json.loads((item / "capture" / "payload.json").read_text())
            self.assertEqual(payload["text"], "https://example.com/posting")
            self.assertEqual(payload["source"], "cli")
            self.assertEqual(payload["entities"], [])

    def test_an_attached_file_lands_beside_a_telegram_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))
            status, body = self.post(
                store,
                {
                    "route": "info",
                    "text": "the spec",
                    "files": [
                        {
                            "name": "spec.pdf",
                            "mime_type": "application/pdf",
                            "data": base64.b64encode(b"%PDF-1.4").decode("ascii"),
                        }
                    ],
                },
            )

            self.assertEqual(status, 201)
            item = Path(temporary) / "inbox" / "info" / body["id"]
            metadata = json.loads((item / "metadata.json").read_text())
            attachment = metadata["attachments"][0]
            self.assertEqual(attachment["path"], "capture/attachments/01-document.pdf")
            self.assertEqual(attachment["original_name"], "spec.pdf")
            self.assertEqual((item / attachment["path"]).read_bytes(), b"%PDF-1.4")

    def test_a_wrong_bearer_of_the_same_length_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))
            for token in (None, "secret-tokeN", "", "wrong"):
                status, body = self.post(
                    store, {"route": "info", "text": "x"}, token=token
                )
                self.assertEqual(status, 401, token)
                self.assertNotIn("secret-token", json.dumps(body))

    def test_bad_requests_are_named_rather_than_guessed(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))
            for payload, status, message in (
                ({"route": "invented", "text": "x"}, 400, "route must be one of"),
                ({"route": "info", "text": "   "}, 400, "text is required"),
                ({"route": "info", "text": "x", "surprise": 1}, 400, "unknown field"),
                (
                    {"route": "info", "text": "x", "files": [{"name": "a", "data": "!!"}]},
                    400,
                    "not valid base64",
                ),
                (
                    {"route": "info", "text": "x", "captured_at": "2026-08-09T10:00:00"},
                    400,
                    "must include a timezone",
                ),
            ):
                got_status, body = self.post(store, payload)
                self.assertEqual(got_status, status, payload)
                self.assertIn(message, body["error"])

    def test_an_oversized_or_unbounded_body_is_refused_before_it_is_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))

            handler = self.handler(store, b"{}")
            handler.headers["Content-Length"] = str(MAX_CAPTURE_REQUEST_BYTES + 1)
            handler.do_POST()
            self.assertEqual(handler.status, 413)

            handler = self.handler(store, b"{}")
            del handler.headers["Content-Length"]
            handler.do_POST()
            self.assertEqual(handler.status, 411)

            handler = self.handler(store, b"{}", content_type="text/plain")
            handler.do_POST()
            self.assertEqual(handler.status, 415)

    def test_a_file_over_the_limit_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))
            oversized = base64.b64encode(b"x" * (MAX_CAPTURE_FILE_BYTES + 1)).decode("ascii")
            status, body = self.post(
                store,
                {"route": "info", "text": "x", "files": [{"name": "a.bin", "data": oversized}]},
            )
            self.assertEqual(status, 413)
            self.assertIn("over the", body["error"])

    def test_only_capture_accepts_a_post(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))
            handler = self.handler(store, b"{}")
            handler.path = "/"
            handler.do_POST()
            self.assertEqual(handler.status, 404)
