import tempfile
import unittest
from pathlib import Path

from info_triage.capture_cli import build_payload, unretrieved_handles


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


class UnretrievedHandlesTests(unittest.TestCase):
    def write_index(self, inbox, handle, frontmatter):
        directory = inbox / handle
        directory.mkdir(parents=True)
        (directory / "index.md").write_text(f"---\n{frontmatter}\n---\n\n## Captured\n")

    def test_only_items_whose_own_source_came_back_empty_are_selected(self):
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            self.write_index(inbox, "info/2026-11-20_311", "origin: instagram\nextraction: failed")
            self.write_index(inbox, "info/2026-11-20_312", "origin: instagram\nextraction: blocked")
            self.write_index(
                inbox, "info/2026-11-20_313", "origin: instagram\nextraction: complete"
            )
            self.write_index(inbox, "info/2026-11-20_314", "origin: youtube\nextraction: failed")
            # A body line that looks like a field is not frontmatter.
            self.write_index(inbox, "job/2026-11-20_1", "origin: note")
            (inbox / "job/2026-11-20_1/index.md").write_text(
                "---\norigin: note\n---\n\norigin: instagram\nextraction: failed\n"
            )
            (inbox / "info" / "triage.md").write_text("---\norigin: instagram\n---\n")

            self.assertEqual(
                unretrieved_handles(inbox, "instagram"),
                ["info/2026-11-20_311", "info/2026-11-20_312"],
            )


if __name__ == "__main__":
    unittest.main()
