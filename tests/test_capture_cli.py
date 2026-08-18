import unittest

from info_triage.capture_cli import build_payload, resolve_route


class ResolveRouteTests(unittest.TestCase):
    """Which route a request names, when --route was not given."""

    def test_a_replacement_stays_in_the_route_its_handle_names(self):
        self.assertEqual(resolve_route(None, "clip/2026-08-18_4"), "clip")

    def test_an_explicit_route_moves_the_item(self):
        self.assertEqual(resolve_route("job", "clip/2026-08-18_4"), "job")

    def test_a_new_capture_still_defaults_to_info(self):
        self.assertEqual(resolve_route(None, None), "info")


class BuildPayloadTests(unittest.TestCase):
    def test_the_handle_is_sent_only_when_one_was_given(self):
        self.assertNotIn("id", build_payload("info", "a note", [], "cli", None))
        payload = build_payload("info", "a note", [], "cli", None, "info/2026-08-18_1")
        self.assertEqual(payload["id"], "info/2026-08-18_1")


if __name__ == "__main__":
    unittest.main()
