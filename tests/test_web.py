import tempfile
import unittest
from pathlib import Path

from info_triage.models import ProcessingIssue, ProcessingProblem, ProcessingResult
from info_triage.storage import CaptureStore
from info_triage.web import WebHandler


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
            item = store.capture(10, 1, "a note", received_at="2026-08-09T10:00:00+00:00")
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
