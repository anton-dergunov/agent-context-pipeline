import base64
import io
import json
import tempfile
import unittest
from pathlib import Path

from conftest import make_store

from info_triage.capture_api import MAX_CAPTURE_FILE_BYTES, MAX_CAPTURE_REQUEST_BYTES
from info_triage.models import ProcessingIssue, ProcessingProblem, ProcessingResult
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
            store = make_store(Path(temporary))

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
            store = make_store(Path(temporary))
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
            store = make_store(Path(temporary))
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


class CaptureRequests:
    """Drives POST /capture without a socket, for the two suites below."""

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


class CaptureEndpointTests(CaptureRequests, unittest.TestCase):
    """POST /capture: the ingest contract Telegram is only one client of."""

    def test_a_text_capture_lands_as_an_item_in_its_route(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(Path(temporary))
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

    def test_a_capture_that_names_no_route_goes_to_the_first_declared_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(Path(temporary))
            status, body = self.post(store, {"text": "a thought"})

            self.assertEqual(status, 201)
            self.assertEqual(body["route"], store.routes[0])

    def test_a_replacement_that_names_no_route_stays_where_it_is(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(Path(temporary))
            _, created = self.post(store, {"route": "job", "text": "a posting"})
            handle = f"job/{created['id']}"

            status, body = self.post(store, {"id": handle, "text": "the posting, corrected"})

            self.assertEqual(status, 200)
            self.assertEqual((body["route"], body["id"]), ("job", created["id"]))

    def test_the_routes_are_listed_for_a_client_that_holds_the_token(self):
        """What a capture client asks instead of hard-coding the routes, and how it
        checks its address and token without leaving a test item behind."""
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(Path(temporary))
            handler = self.handler(store, b"")
            handler.path = "/routes"
            handler.do_GET()
            self.assertEqual(handler.status, 200)
            self.assertEqual(
                json.loads(handler.wfile.getvalue()),
                {"routes": list(store.routes), "default": store.routes[0]},
            )

            refused = self.handler(store, b"", token="wrong")
            refused.path = "/routes"
            refused.do_GET()
            self.assertEqual(refused.status, 401)
            self.assertEqual(store.status_counts()["ready"], 0)

    def test_an_attached_file_lands_beside_a_telegram_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(Path(temporary))
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
            store = make_store(Path(temporary))
            for token in (None, "secret-tokeN", "", "wrong"):
                status, body = self.post(
                    store, {"route": "info", "text": "x"}, token=token
                )
                self.assertEqual(status, 401, token)
                self.assertNotIn("secret-token", json.dumps(body))

    def test_bad_requests_are_named_rather_than_guessed(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(Path(temporary))
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
            store = make_store(Path(temporary))

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
            store = make_store(Path(temporary))
            oversized = base64.b64encode(b"x" * (MAX_CAPTURE_FILE_BYTES + 1)).decode("ascii")
            status, body = self.post(
                store,
                {"route": "info", "text": "x", "files": [{"name": "a.bin", "data": oversized}]},
            )
            self.assertEqual(status, 413)
            self.assertIn("over the", body["error"])

    def test_only_capture_accepts_a_post(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(Path(temporary))
            handler = self.handler(store, b"{}")
            handler.path = "/"
            handler.do_POST()
            self.assertEqual(handler.status, 404)


class CaptureReplacementTests(CaptureRequests, unittest.TestCase):
    """`id` on a capture rewrites the item that handle names, whole."""

    def capture(self, store, route, text, **extra):
        status, body = self.post(store, {"route": route, "text": text, **extra})
        self.assertIn(status, (200, 201), body)
        return body

    def test_a_replacement_rewrites_the_item_in_place(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(Path(temporary))
            first = self.capture(store, "job", "first text")

            status, second = self.post(
                store, {"route": "job", "id": f"job/{first['id']}", "text": "second text"}
            )

            # 200, not 201: the handle the caller held still names the item.
            self.assertEqual(status, 200)
            self.assertEqual(second["id"], first["id"])
            self.assertEqual(second["route"], "job")
            item = Path(temporary) / "inbox" / "job" / second["id"]
            self.assertEqual((item / "capture" / "message.md").read_text(), "second text")
            payload = json.loads((item / "capture" / "payload.json").read_text())
            self.assertEqual(payload["text"], "second text")
            # The revision has to move, or the laptop keeps the copy it deleted.
            self.assertEqual(second["revision"], 2)
            metadata = json.loads((item / "metadata.json").read_text())
            self.assertEqual(metadata["revision"], 2)
            self.assertFalse((Path(temporary) / "staging" / "job" / second["id"]).exists())

    def test_a_replacement_drops_the_files_it_does_not_repeat(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(Path(temporary))
            first = self.capture(
                store,
                "info",
                "the spec",
                files=[{"name": "spec.pdf", "data": base64.b64encode(b"%PDF-1.4").decode()}],
            )

            self.post(
                store, {"route": "info", "id": f"info/{first['id']}", "text": "no spec after all"}
            )

            item = Path(temporary) / "inbox" / "info" / first["id"]
            metadata = json.loads((item / "metadata.json").read_text())
            self.assertEqual(metadata["attachments"], [])
            self.assertFalse((item / "capture" / "attachments").exists())

    def test_a_replacement_discards_what_the_last_revision_generated(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(Path(temporary))
            first = self.capture(store, "info", "https://example.com/paper")
            item = Path(temporary) / "inbox" / "info" / first["id"]
            (item / "index.md").write_text("---\nkind: link\n---\n")
            (item / "links.json").write_text("[]")
            (item / "extracted" / "01-document-abcd").mkdir(parents=True)
            (item / "extracted" / "01-document-abcd" / "content.md").write_text("old body")

            self.post(store, {"route": "info", "id": f"info/{first['id']}", "text": "never mind"})

            # The old index and extraction describe text that is gone.
            self.assertFalse((item / "links.json").exists())
            self.assertFalse((item / "extracted").exists())
            self.assertEqual((item / "index.md").exists(), False)
            # Provenance is not generated, and survives.
            self.assertEqual((item / "capture" / "message.md").read_text(), "never mind")
            self.assertTrue((item / "metadata.json").is_file())

    def test_a_replacement_may_move_the_item_to_another_route(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(Path(temporary))
            first = self.capture(store, "job", "a posting")

            status, moved = self.post(
                store, {"route": "clip", "id": f"job/{first['id']}", "text": "a posting"}
            )

            self.assertEqual(status, 200)
            self.assertEqual(moved["route"], "clip")
            self.assertFalse((Path(temporary) / "inbox" / "job" / first["id"]).exists())
            self.assertTrue((Path(temporary) / "inbox" / "clip" / moved["id"]).is_dir())

            # Identity stayed on the route that first received it, so the new
            # handle keeps resolving to the same item.
            status, again = self.post(
                store, {"route": "clip", "id": f"clip/{moved['id']}", "text": "still a posting"}
            )
            self.assertEqual(status, 200)
            self.assertEqual(again["revision"], 3)
            self.assertEqual(again["id"], moved["id"])

    def test_a_handle_that_names_nothing_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(Path(temporary))
            first = self.capture(store, "info", "a note")
            wrong_date = f"info/1999-01-01_{first['id'].split('_')[1]}"
            for handle, status, message in (
                ("info/2026-08-18_99", 404, "no item named"),
                (wrong_date, 404, "no item named"),
                (f"job/{first['id']}", 404, "no item named"),
                ("nonsense", 400, "item handle"),
                ("2026-08-18_1", 400, "item handle"),
                ("invented/2026-08-18_1", 400, "unknown route"),
            ):
                got_status, body = self.post(store, {"route": "info", "id": handle, "text": "x"})
                self.assertEqual(got_status, status, handle)
                self.assertIn(message, body["error"], handle)

    def test_a_new_capture_time_is_refused_because_the_handle_carries_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(Path(temporary))
            first = self.capture(store, "info", "a note")

            status, body = self.post(
                store,
                {
                    "route": "info",
                    "id": f"info/{first['id']}",
                    "text": "a note",
                    "captured_at": "2026-08-09T10:00:00+00:00",
                },
            )

            self.assertEqual(status, 400)
            self.assertIn("part of the item's id", body["error"])

    def test_a_telegram_capture_is_not_rewritable_over_http(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = make_store(Path(temporary))
            # A real Telegram message still exists upstream, and editing it would
            # fight this rewrite over the same directory.
            item = store.capture("info", 4242, 17, "sent from a phone", route="info")

            status, body = self.post(
                store, {"route": "info", "id": f"info/{item.path.name}", "text": "rewritten"}
            )

            self.assertEqual(status, 409)
            self.assertIn("edit the message instead", body["error"])
