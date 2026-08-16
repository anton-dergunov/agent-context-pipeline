import json
import re
import shutil
import subprocess
import tempfile
import unittest
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

            self.assertIn("* 1  2026-08-09  paper  Strong Model Collapse\n", result)
            self.assertIn("  :DIR:      2026-08-09_1\n", result)
            self.assertIn("  :URL:      https://arxiv.org/abs/2410.04840\n", result)
            self.assertIn("  :STATUS:   ok\n", result)
            self.assertIn(
                "  [[file:2026-08-09_1/index.md][index]] · [[file:2026-08-09_1/][directory]]",
                result,
            )
            # org-id owns :ID:; one per item would dangle in .org-id-locations.
            self.assertNotIn(":ID:", result)
            # An empty intent is omitted, never rendered as the literal "null".
            self.assertIn("  :INTENT:\n", result)
            # Navigation only: no body text reaches Org, which is what keeps the
            # items themselves in Markdown and out of an escaping problem.
            self.assertNotIn("a long quoted body", result)

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

            self.assertIn("* 1  2026-08-09  [ [not a link]] and a ragged title\n", result)
            self.assertEqual(result.count("\n* "), 1)

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
            self.assertIn(":DIR:      2026-08-09_2", (local / "triage.org").read_text())
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
