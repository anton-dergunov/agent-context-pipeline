import unittest

from info_triage.capture_cli import build_payload


class BuildPayloadTests(unittest.TestCase):
    def test_the_handle_is_sent_only_when_one_was_given(self):
        self.assertNotIn("id", build_payload("info", "a note", [], "cli", None))
        payload = build_payload("info", "a note", [], "cli", None, "info/2026-08-18_1")
        self.assertEqual(payload["id"], "info/2026-08-18_1")

    def test_the_route_is_sent_only_when_one_was_asked_for(self):
        """Left out, the server decides: a replacement stays put, a new item goes
        to the default route. Sending a guess would re-file every item it corrected."""
        self.assertNotIn("route", build_payload(None, "a note", [], "cli", None))
        self.assertEqual(build_payload("job", "a note", [], "cli", None)["route"], "job")


if __name__ == "__main__":
    unittest.main()
