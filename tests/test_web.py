import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
