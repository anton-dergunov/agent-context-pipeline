import json
import re
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from datetime import UTC, datetime, timedelta
from pathlib import Path

from info_triage.sync import (
    RemoteItem,
    SyncConfig,
    SyncError,
    _sorted_items,
    atomic_write_text,
    deletion_decision,
    generate_inbox,
    main,
    read_manifest,
    render_inbox,
    render_org,
    synchronize,
    valid_item_name,
)


def index_text(name: str, captured_at: str, body: str = "> a note") -> str:
    return (
        f"---\nid: {name}\ncaptured_at: {captured_at}\nintent: null\n---\n\n## Captured\n\n{body}\n"
    )


def write_item(
    inbox: Path,
    name: str,
    *,
    received_at: str,
    revision: int = 1,
    message: str = "## Segment 1 — text\n\nmessage",
    index: str | None = None,
) -> Path:
    item = inbox / name
    item.mkdir(parents=True)
    (item / "metadata.json").write_text(
        json.dumps(
            {
                "message_id": int(name.rsplit("_", 1)[1]),
                "revision": revision,
                "received_at": received_at,
            }
        ),
        encoding="utf-8",
    )
    (item / "index.md").write_text(
        index_text(name, received_at) if index is None else index,
        encoding="utf-8",
    )
    capture = item / "capture"
    capture.mkdir()
    (capture / "message.md").write_text(message, encoding="utf-8")
    return item


class FakeSyncCommands:
    def __init__(self, remote_inbox: Path):
        self.remote_inbox = remote_inbox
        self.commands: list[list[str]] = []
        self.fail_on: str | None = None

    def __call__(self, command: list[str]) -> None:
        self.commands.append(command)
        if self.fail_on == command[0]:
            raise subprocess.CalledProcessError(23, command)
        if command[0] == "rsync" and "--include" in command:
            destination = Path(command[-1])
            for remote_item in self.remote_inbox.iterdir():
                target = destination / remote_item.name
                target.mkdir()
                shutil.copy2(remote_item / "metadata.json", target / "metadata.json")
        elif command[0] == "rsync":
            destination = Path(command[-1])
            for remote_item in self.remote_inbox.iterdir():
                shutil.copytree(
                    remote_item,
                    destination / remote_item.name,
                    dirs_exist_ok=True,
                )
        elif command[0] == "ssh" and command[2].startswith("rm -rf"):
            name = command[2].rsplit("/", 1)[1].rstrip("'")
            shutil.rmtree(self.remote_inbox / name)


class SyncUnitTests(unittest.TestCase):
    def test_item_name_validation(self):
        self.assertTrue(valid_item_name("2026-08-09_123"))
        self.assertFalse(valid_item_name("-100_123"))
        self.assertFalse(valid_item_name("inbox.md"))
        self.assertFalse(valid_item_name("../2026-08-09_123"))

    def test_atomic_write_replaces_content_without_leaving_temporary_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "state"
            target.write_text("old", encoding="utf-8")

            atomic_write_text(target, "new")

            self.assertEqual(target.read_text(), "new")
            self.assertEqual(list(root.iterdir()), [target])

    def test_manifest_keeps_highest_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "delivered-items"
            manifest.write_text("2026-08-09_7 2\n2026-08-09_7 1\n")
            self.assertEqual(read_manifest(manifest), {"2026-08-09_7": 2})

    def test_manifest_rejects_invalid_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "delivered-items"
            manifest.write_text("2026-08-09_7 nope\n")
            with self.assertRaisesRegex(SyncError, "Invalid delivered revision"):
                read_manifest(manifest)

    def test_revision_decides_between_delete_and_restore(self):
        with tempfile.TemporaryDirectory() as temporary:
            local_inbox = Path(temporary)
            item = RemoteItem("2026-08-09_7", 2, 7)
            self.assertEqual(deletion_decision(item, {item.name: 2}, local_inbox), "delete")
            self.assertEqual(deletion_decision(item, {item.name: 1}, local_inbox), "restore")
            self.assertIsNone(deletion_decision(item, {}, local_inbox))
            (local_inbox / item.name).mkdir()
            self.assertIsNone(deletion_decision(item, {item.name: 2}, local_inbox))

    def test_rendered_inbox_concatenates_index_files_oldest_first(self):
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            write_item(
                inbox,
                "2026-08-10_2",
                received_at="2026-08-10T11:30:00+01:00",
                index=(
                    '---\nid: 2026-08-10_2\nintent: "later one"\n---\n\n'
                    "## Captured\n\n> later\n\n## Links\n\n| # | link |\n|---|------|\n"
                ),
            )
            write_item(inbox, "2026-08-09_1", received_at="2026-08-09T10:00:00+00:00")

            result = render_inbox(inbox)

            self.assertLess(
                result.index("## 1 — 2026-08-09_1"), result.index("## 2 — 2026-08-10_2")
            )
            # Frontmatter is only unambiguous at the top of a file, so it is fenced here.
            self.assertIn(
                "## 2 — 2026-08-10_2\n\n"
                "[📁 2026-08-10_2/](2026-08-10_2/) · [index.md](2026-08-10_2/index.md)\n\n"
                '```yaml\nid: 2026-08-10_2\nintent: "later one"\n```\n\n'
                "### Captured\n\n> later\n\n### Links",
                result,
            )
            self.assertNotIn("\n## Captured", result)
            self.assertNotIn("chat_id", result)

    def test_numbering_is_positional_and_the_id_stays_beside_it(self):
        """The number is what the reader selects by; the id outlives the numbering."""
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            for day, message_id in ((11, 3), (9, 1), (10, 2)):
                write_item(
                    inbox,
                    f"2026-08-{day:02d}_{message_id}",
                    received_at=f"2026-08-{day:02d}T10:00:00+00:00",
                )

            headings = re.findall(r"^## .*$", render_inbox(inbox), flags=re.MULTILINE)

            self.assertEqual(
                headings,
                ["## 1 — 2026-08-09_1", "## 2 — 2026-08-10_2", "## 3 — 2026-08-11_3"],
            )

    def test_relative_links_are_rebased_onto_the_item_directory(self):
        """index.md writes them relative to itself; one level up they resolve to nothing."""
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            write_item(
                inbox,
                "2026-08-09_1",
                received_at="2026-08-09T10:00:00+00:00",
                index=(
                    "---\nid: 2026-08-09_1\n---\n\n## Sources\n\n"
                    "1. [extracted/01-research-x/content.md](extracted/01-research-x/content.md)"
                    " — complete · 12 words\n\n"
                    "## Links\n\n| 1 | [A page](https://example.com/a) |\n"
                ),
            )

            result = render_inbox(inbox)

            self.assertIn("(2026-08-09_1/extracted/01-research-x/content.md)", result)
            # The label keeps the item-relative path; only the destination moves.
            self.assertIn("[extracted/01-research-x/content.md](2026-08-09_1/", result)
            # An absolute destination is already correct from anywhere.
            self.assertIn("[A page](https://example.com/a)", result)

    def test_header_counts_the_waiting_items_and_the_oldest(self):
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            oldest = datetime.now(UTC) - timedelta(days=3, hours=2)
            write_item(inbox, "2026-08-09_1", received_at=oldest.isoformat())
            write_item(inbox, "2026-08-10_2", received_at=datetime.now(UTC).isoformat())

            result = render_inbox(inbox)

            self.assertTrue(result.startswith("# Inbox — 2 items, oldest 3 days\n"))

    def test_empty_inbox_contains_only_generated_header(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = render_inbox(Path(temporary))
            self.assertTrue(result.startswith("# Inbox — 0 items\n"))
            self.assertIn("overwritten on every sync", result)
            self.assertNotIn("\n## ", result)

    def test_org_view_is_navigation_and_carries_no_extracted_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            write_item(
                inbox,
                "2026-08-09_1",
                received_at="2026-08-09T10:00:00+00:00",
                index=(
                    "---\nid: 2026-08-09_1\nintent: null\nkind: paper\n"
                    'title: "Strong Model Collapse"\n'
                    "canonical_url: https://arxiv.org/abs/2410.04840\n"
                    "extraction: ok\n---\n\n## Lead\n\n> a long quoted body\n"
                ),
            )

            generate_inbox(inbox)
            result = (inbox / "triage.org").read_text()

            self.assertIn("* 1 · 2026-08-09 · paper · Strong Model Collapse\n", result)
            # Emacs reads the item's directory back out of this link to know what
            # a drop deletes: it is the only place the id still appears.
            self.assertIn(
                "  [[file:2026-08-09_1/index.md][index]] · "
                "[[file:2026-08-09_1/][directory]] · "
                "[[https://arxiv.org/abs/2410.04840][source]]\n",
                result,
            )
            # org-id owns :ID:; one per item would dangle in .org-id-locations.
            self.assertNotIn(":ID:", result)
            # No drawer at all: everything one held is in the heading or one link
            # away, and eight lines of restatement per item is what stopped two
            # dozen items fitting on a screen.
            self.assertNotIn(":PROPERTIES:", result)
            # An empty intent is omitted, never rendered as the literal "null".
            self.assertNotIn("null", result)
            # A healthy extraction is worth no ink; only a short one is tagged.
            self.assertNotIn(":ok:", result)
            # Navigation only: no body text reaches Org, which is what keeps the
            # items themselves in Markdown and out of an escaping problem. The
            # Lead is read for a label, but only when there is no title.
            self.assertNotIn("a long quoted body", result)

    def test_org_view_warns_an_agent_off_itself(self):
        """An editor integration advertises whatever is on screen, and this is.

        The file holds strictly less than `triage.md`, so reading it spends
        tokens to arrive somewhere worse.
        """
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)

            result = render_org(_sorted_items(inbox))

            self.assertIn("read triage.md in this directory instead", result)

    def test_an_untitled_item_is_labelled_from_its_headline(self):
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            write_item(
                inbox,
                "2026-08-09_1",
                received_at="2026-08-09T10:00:00+00:00",
                index=(
                    "---\nid: 2026-08-09_1\nintent: null\nkind: post\n"
                    'headline: "Why the trees wear coloured tags"\n'
                    "extraction: partial\nreason: ocr-unavailable\n---\n\n## Captured\n\n> x\n"
                ),
            )

            result = render_org(_sorted_items(inbox))

            self.assertIn(
                "* 1 · 2026-08-09 · post · Why the trees wear coloured tags  :partial:\n", result
            )

    def test_an_item_captured_before_headlines_falls_back_to_its_lead(self):
        """Transitional: items already in the inbox have no `headline:` field.

        Only the index is in reach here, so this is a weaker chain than
        `index.py`'s on purpose — and the inbox drains daily.
        """
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            write_item(
                inbox,
                "2026-08-09_1",
                received_at="2026-08-09T10:00:00+00:00",
                index=(
                    "---\nid: 2026-08-09_1\nintent: null\nkind: post\nextraction: ok\n---\n\n"
                    "## Captured\n\n> [An account name • Instagram](https://example.com/x)\n\n"
                    "## Lead\n\n> **On-screen text**\n>\n> Asia Odyssey Travel\n>\n"
                    "> **Caption**\n>\n> Hidden right next to Chongqing.\n"
                ),
            )

            result = render_org(_sorted_items(inbox))

            # Not the first stream: on-screen text opens with the poster's own
            # watermark, and OCR of burned-in subtitles names nothing.
            self.assertIn("* 1 · 2026-08-09 · post · Hidden right next to Chongqing.\n", result)

    def test_an_item_with_only_a_captured_link_is_labelled_from_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            write_item(
                inbox,
                "2026-08-09_1",
                received_at="2026-08-09T10:00:00+00:00",
                index=(
                    "---\nid: 2026-08-09_1\nintent: null\nextraction: none\n---\n\n"
                    "## Captured\n\n> [An account name • Instagram](https://example.com/x)\n"
                ),
            )

            result = render_org(_sorted_items(inbox))

            self.assertIn("* 1 · 2026-08-09 · An account name • Instagram  :none:\n", result)

    def test_an_intent_is_kept_when_it_is_not_already_the_label(self):
        """The user's own words are the one thing the heading cannot carry."""
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            write_item(
                inbox,
                "2026-08-09_1",
                received_at="2026-08-09T10:00:00+00:00",
                index=(
                    "---\nid: 2026-08-09_1\nkind: paper\n"
                    'intent: "read before the Friday review"\n'
                    'title: "Strong Model Collapse"\nextraction: ok\n---\n\n## Captured\n\n> x\n'
                ),
            )
            write_item(
                inbox,
                "2026-08-09_2",
                received_at="2026-08-09T11:00:00+00:00",
                index=(
                    "---\nid: 2026-08-09_2\n"
                    'intent: "translate this"\nheadline: "translate this"\n'
                    "extraction: ok\n---\n\n## Captured\n\n> x\n"
                ),
            )

            result = render_org(_sorted_items(inbox))

            self.assertIn("  /read before the Friday review/\n", result)
            # The second item's intent is already its label; saying it twice is noise.
            self.assertEqual(result.count("translate this"), 1)

    def test_org_headings_neutralize_markup_in_extracted_titles(self):
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            write_item(
                inbox,
                "2026-08-09_1",
                received_at="2026-08-09T10:00:00+00:00",
                index=(
                    "---\nid: 2026-08-09_1\nintent: null\n"
                    'title: "[[not a link]] and a  ragged   title"\n'
                    "extraction: ok\n---\n\n## Captured\n\n> x\n"
                ),
            )

            result = render_org(_sorted_items(inbox))

            self.assertIn("* 1 · 2026-08-09 · [ [not a link]] and a ragged title\n", result)
            self.assertEqual(result.count("\n* "), 1)

    def test_a_bracketed_url_is_dropped_rather_than_closing_its_link_early(self):
        """A bracket in the target would swallow the rest of the line, and the
        line after it — which in a generated file is the next item's heading."""
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            write_item(
                inbox,
                "2026-08-09_1",
                received_at="2026-08-09T10:00:00+00:00",
                index=(
                    "---\nid: 2026-08-09_1\nintent: null\n"
                    'headline: "A page"\ncanonical_url: https://example.com/a]b\n'
                    "extraction: ok\n---\n\n## Captured\n\n> x\n"
                ),
            )

            result = render_org(_sorted_items(inbox))

            self.assertIn("[[file:2026-08-09_1/][directory]]\n", result)
            self.assertNotIn("source", result)

    def test_generation_failure_preserves_previous_inbox(self):
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            (inbox / "triage.md").write_text("previous", encoding="utf-8")
            item = inbox / "2026-08-09_1"
            item.mkdir()
            (item / "metadata.json").write_text(
                json.dumps({"received_at": "not-a-time"}), encoding="utf-8"
            )
            (item / "index.md").write_text("---\nid: x\n---\n", encoding="utf-8")
            (item / "capture").mkdir()
            (item / "capture" / "message.md").write_text("message", encoding="utf-8")

            with self.assertRaisesRegex(SyncError, "Invalid received_at"):
                generate_inbox(inbox)

            self.assertEqual((inbox / "triage.md").read_text(), "previous")

    def test_missing_message_preserves_previous_inbox(self):
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            (inbox / "triage.md").write_text("previous", encoding="utf-8")
            item = inbox / "2026-08-09_1"
            item.mkdir()
            (item / "metadata.json").write_text(
                json.dumps({"received_at": "2026-08-09T10:00:00+00:00"}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SyncError, "Missing capture/message.md"):
                generate_inbox(inbox)

            self.assertEqual((inbox / "triage.md").read_text(), "previous")

    def test_message_outside_capture_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            item = inbox / "2026-08-09_1"
            item.mkdir()
            (item / "metadata.json").write_text(
                json.dumps({"received_at": "2026-08-09T10:00:00+00:00"}),
                encoding="utf-8",
            )
            (item / "message.md").write_text("message", encoding="utf-8")

            with self.assertRaisesRegex(SyncError, "Missing capture/message.md"):
                render_inbox(inbox)

    def test_missing_index_preserves_previous_inbox(self):
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            (inbox / "triage.md").write_text("previous", encoding="utf-8")
            item = write_item(inbox, "2026-08-09_1", received_at="2026-08-09T10:00:00+00:00")
            (item / "index.md").unlink()

            with self.assertRaisesRegex(SyncError, "Missing index.md"):
                generate_inbox(inbox)

            self.assertEqual((inbox / "triage.md").read_text(), "previous")


class SynchronizeTests(unittest.TestCase):
    def test_sync_deletes_delivered_revision_and_restores_newer_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote = root / "remote"
            local = root / "local"
            state = root / "state"
            remote.mkdir()
            local.mkdir()
            state.mkdir()
            write_item(
                remote,
                "2026-08-08_1",
                received_at="2026-08-08T09:00:00+00:00",
            )
            write_item(
                remote,
                "2026-08-09_2",
                received_at="2026-08-09T09:00:00+00:00",
                revision=2,
                index=index_text("2026-08-09_2", "2026-08-09T09:00:00Z", body="> edited"),
            )
            write_item(
                remote,
                "2026-08-10_3",
                received_at="2026-08-10T09:00:00+00:00",
            )
            (state / "delivered-items").write_text(
                "2026-08-08_1\t1\n2026-08-09_2\t1\n", encoding="utf-8"
            )
            commands = FakeSyncCommands(remote)
            config = SyncConfig("server", "/remote/inbox", local, state)

            synchronize(config, commands)

            self.assertFalse((remote / "2026-08-08_1").exists())
            self.assertFalse((local / "2026-08-08_1").exists())
            self.assertTrue((local / "2026-08-09_2").is_dir())
            self.assertTrue((local / "2026-08-10_3").is_dir())
            self.assertEqual(
                (state / "delivered-items").read_text(),
                "2026-08-08_1\t1\n2026-08-09_2\t2\n2026-08-10_3\t1\n",
            )
            aggregate = (local / "triage.md").read_text()
            self.assertEqual(aggregate.count("— 2026-08-09"), 1)
            self.assertIn("edited", aggregate)
            self.assertIn("[[file:2026-08-09_2/][directory]]", (local / "triage.org").read_text())
            self.assertTrue(
                any(command[0] == "ssh" and "rm -rf" in command[2] for command in commands.commands)
            )

    def test_missing_local_inbox_safety_check_runs_before_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            state.mkdir()
            (state / "delivered-items").write_text("2026-08-08_1\t1\n")
            commands = FakeSyncCommands(root / "unused")

            with self.assertRaisesRegex(SyncError, "Local inbox is missing"):
                synchronize(
                    SyncConfig("server", "/remote/inbox", root / "missing", state),
                    commands,
                )

            self.assertEqual(commands.commands, [])

    def test_subprocess_failure_stops_sync(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote = root / "remote"
            remote.mkdir()
            commands = FakeSyncCommands(remote)
            commands.fail_on = "ssh"

            with self.assertRaises(subprocess.CalledProcessError):
                synchronize(
                    SyncConfig("server", "/remote/inbox", root / "local", root / "state"),
                    commands,
                )

            self.assertEqual(len(commands.commands), 1)


if __name__ == "__main__":
    unittest.main()


class RegenerateTests(unittest.TestCase):
    def test_regenerate_rewrites_both_views_without_touching_the_nas(self):
        """Emacs runs this after a drop, and both views must renumber together.

        The numbers a routing request quotes come from `triage.org` and are
        consumed from `triage.md`; if only one of them renumbers, they name
        different items.
        """
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary) / "inbox"
            inbox.mkdir()
            write_item(inbox, "2026-08-09_1", received_at="2026-08-09T10:00:00+00:00")
            write_item(inbox, "2026-08-10_2", received_at="2026-08-10T10:00:00+00:00")
            generate_inbox(inbox)
            shutil.rmtree(inbox / "2026-08-09_1")

            with unittest.mock.patch(
                "info_triage.sync.default_config",
                return_value=SyncConfig("server", "/remote/inbox", inbox, Path(temporary) / "state"),
            ), unittest.mock.patch("info_triage.sync.run_command") as runner:
                self.assertEqual(main(["--regenerate"]), 0)

            runner.assert_not_called()
            self.assertIn("* 1 · 2026-08-10 ·", (inbox / "triage.org").read_text())
            self.assertIn("## 1 — 2026-08-10_2", (inbox / "triage.md").read_text())
            self.assertNotIn("2026-08-09_1", (inbox / "triage.md").read_text())
