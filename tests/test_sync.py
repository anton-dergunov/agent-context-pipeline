import contextlib
import io
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
    DEFAULT_ORG_EXCLUDE,
    DIGEST_NAME,
    NOTES_DIR,
    ORG_NAME,
    RemoteItem,
    SyncConfig,
    SyncError,
    _remove_stale_local_items,
    _sorted_items,
    annotate_inbox,
    atomic_write_text,
    default_config,
    deletion_decision,
    duplicate_captures,
    generate_inbox,
    main,
    neighbour_query,
    read_manifest,
    read_remote_items,
    render_inbox,
    render_org,
    synchronize,
    valid_item_name,
)


def index_text(name: str, captured_at: str, body: str = "> a note") -> str:
    return (
        f"---\nid: {name}\ncaptured_at: {captured_at}\nintent: null\n---\n\n## Captured\n\n{body}\n"
    )


def linked_index(
    name: str,
    captured_at: str,
    url: str,
    *,
    kind: str = "post",
    sources: bool = True,
    captured: str = "> a note",
    problem: str = "",
) -> str:
    """An index carrying every section a duplicate collapse has to reason about."""
    text = (
        f"---\nid: {name}\ncaptured_at: {captured_at}\nintent: null\nkind: {kind}\n"
        f"canonical_url: {url}\n---\n\n## Captured\n\n{captured}\n"
    )
    if sources:
        text += (
            "\n## Sources\n\n1. [content.md](extracted/01-web-a/content.md) — complete · 900 words"
            "\n\n## Lead\n\n> the opening of the body\n"
        )
    text += f"\n## Links\n\n1. <{url}> — web · resolved\n"
    if problem:
        text += f"\n## Problems\n\n- {problem}\n"
    return text


def write_item(
    inbox: Path,
    name: str,
    *,
    received_at: str,
    revision: int = 1,
    route: str = "info",
    message: str = "## Segment 1 — text\n\nmessage",
    index: str | None = None,
) -> Path:
    """Write one item into its route's subdirectory of an inbox."""
    item = inbox / route / name
    item.mkdir(parents=True)
    (item / "metadata.json").write_text(
        json.dumps(
            {
                "route": route,
                "local_id": int(name.rsplit("_", 1)[1]),
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
            # The metadata-only pass: route directories, then one file each.
            destination = Path(command[-1])
            for route in sorted(self.remote_inbox.iterdir()):
                for remote_item in sorted(route.iterdir()):
                    target = destination / route.name / remote_item.name
                    target.mkdir(parents=True)
                    shutil.copy2(remote_item / "metadata.json", target / "metadata.json")
        elif command[0] == "rsync":
            destination = Path(command[-1])
            for route in sorted(self.remote_inbox.iterdir()):
                for remote_item in sorted(route.iterdir()):
                    shutil.copytree(
                        remote_item,
                        destination / route.name / remote_item.name,
                        dirs_exist_ok=True,
                    )
        elif command[0] == "ssh" and command[2].startswith("rm -rf"):
            # The path is route/name now, so both trailing segments are the key.
            route, name = command[2].rstrip("'").rsplit("/", 2)[-2:]
            shutil.rmtree(self.remote_inbox / route / name)


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
            manifest.write_text("info/2026-08-09_7 2\ninfo/2026-08-09_7 1\n")
            self.assertEqual(read_manifest(manifest), {"info/2026-08-09_7": 2})

    def test_manifest_rejects_invalid_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "delivered-items"
            manifest.write_text("info/2026-08-09_7 nope\n")
            with self.assertRaisesRegex(SyncError, "Invalid delivered revision"):
                read_manifest(manifest)

    def test_revision_decides_between_delete_and_restore(self):
        with tempfile.TemporaryDirectory() as temporary:
            local_inbox = Path(temporary)
            item = RemoteItem("info", "2026-08-09_7", 2, 7)
            self.assertEqual(deletion_decision(item, {item.key: 2}, local_inbox), "delete")
            self.assertEqual(deletion_decision(item, {item.key: 1}, local_inbox), "restore")
            self.assertIsNone(deletion_decision(item, {}, local_inbox))
            (local_inbox / item.key).mkdir(parents=True)
            self.assertIsNone(deletion_decision(item, {item.key: 2}, local_inbox))

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

            result = render_inbox(inbox / "info")

            self.assertLess(
                result.index("### 1 — 2026-08-09_1"), result.index("### 2 — 2026-08-10_2")
            )
            # Frontmatter is only unambiguous at the top of a file, so it is fenced here.
            self.assertIn(
                "## 2026-08-10\n\n"
                "### 2 — 2026-08-10_2\n\n"
                "[📁 2026-08-10_2/](2026-08-10_2/) · [index.md](2026-08-10_2/index.md)\n\n"
                '```yaml\nid: 2026-08-10_2\nintent: "later one"\n```\n\n'
                "#### Captured\n\n> later\n\n#### Links",
                result,
            )
            self.assertNotIn("\n## Captured", result)
            self.assertNotIn("\n### Captured", result)
            self.assertNotIn("chat_id", result)

    def test_digest_groups_items_under_a_heading_per_day(self):
        """Both views group by day, so the two describe the same shape — and the
        numbering stays global across the groups, since that is the whole
        interface between them."""
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            for name, received_at in (
                ("2026-08-09_1", "2026-08-09T10:00:00+00:00"),
                ("2026-08-09_2", "2026-08-09T18:00:00+00:00"),
                ("2026-08-10_3", "2026-08-10T09:00:00+00:00"),
            ):
                write_item(inbox, name, received_at=received_at)

            headings = re.findall(r"^#{2,3} .*$", render_inbox(inbox / "info"), flags=re.MULTILINE)

            self.assertEqual(
                headings,
                [
                    "## 2026-08-09",
                    "### 1 — 2026-08-09_1",
                    "### 2 — 2026-08-09_2",
                    "## 2026-08-10",
                    "### 3 — 2026-08-10_3",
                ],
            )

    def test_a_deep_index_heading_stays_a_heading(self):
        """Demoting by two would take a fifth-level heading past Markdown's sixth,
        and literal `#`s in the body would read as text rather than structure."""
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            write_item(
                inbox,
                "2026-08-09_1",
                received_at="2026-08-09T10:00:00+00:00",
                index="---\nid: 2026-08-09_1\n---\n\n##### Deep\n\ntext\n",
            )

            result = render_inbox(inbox / "info")

            self.assertIn("\n###### Deep\n", result)
            self.assertNotIn("#######", result)

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

            headings = re.findall(r"^### .*$", render_inbox(inbox / "info"), flags=re.MULTILINE)

            self.assertEqual(
                headings,
                ["### 1 — 2026-08-09_1", "### 2 — 2026-08-10_2", "### 3 — 2026-08-11_3"],
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

            result = render_inbox(inbox / "info")

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

            result = render_inbox(inbox / "info")

            self.assertTrue(result.startswith("# Inbox — 2 items, oldest 3 days\n"))

    def test_empty_inbox_contains_only_generated_header(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = render_inbox(Path(temporary) / "info")
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
            result = (inbox / "info" / "triage.org").read_text()

            # The day is a heading of its own; the item hangs under it, its label
            # is the link to its index, and its kind is a tag.
            self.assertIn("\n* 2026-08-09\n\n", result)
            self.assertIn(
                "** 1 · [[file:2026-08-09_1/index.md][Strong Model Collapse]]  :paper:\n", result
            )
            # Emacs reads the item's directory back out of this link to know what
            # a drop deletes: it is the only place the id still appears.
            self.assertIn(
                "   [[file:2026-08-09_1/][directory]] · "
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

            result = render_org(_sorted_items(inbox / "info"))

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

            result = render_org(_sorted_items(inbox / "info"))

            self.assertIn(
                "** 1 · [[file:2026-08-09_1/index.md]"
                "[Why the trees wear coloured tags]]  :post:\n",
                result,
            )
            # The extraction status is carried by the item's own frontmatter and
            # by triage.md. In a queue read at a glance it was a second tag per
            # line saying nothing about what the item is.
            self.assertNotIn(":partial:", result)

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

            result = render_org(_sorted_items(inbox / "info"))

            # Not the first stream: on-screen text opens with the poster's own
            # watermark, and OCR of burned-in subtitles names nothing.
            self.assertIn(
                "** 1 · [[file:2026-08-09_1/index.md]"
                "[Hidden right next to Chongqing.]]  :post:\n",
                result,
            )

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

            result = render_org(_sorted_items(inbox / "info"))

            # No kind, so no tag at all — a heading that ends at its title.
            self.assertIn(
                "** 1 · [[file:2026-08-09_1/index.md][An account name • Instagram]]\n", result
            )

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

            result = render_org(_sorted_items(inbox / "info"))

            self.assertIn("   /read before the Friday review/\n", result)
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

            result = render_org(_sorted_items(inbox / "info"))

            # The title is now a link description, so a `]` in it would close the
            # description early and leave the rest of the heading as loose text.
            self.assertIn(
                "** 1 · [[file:2026-08-09_1/index.md]"
                "[( (not a link)) and a ragged title]]\n",
                result,
            )
            self.assertEqual(result.count("\n* "), 1)

    def test_an_unusual_kind_still_makes_a_tag_org_can_read(self):
        """`kind` comes from an extractor, not from a fixed list, and Org tags
        admit only [[:alnum:]_@#%] — anything else is not a tag at all."""
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            write_item(
                inbox,
                "2026-08-09_1",
                received_at="2026-08-09T10:00:00+00:00",
                index=(
                    "---\nid: 2026-08-09_1\nintent: null\nkind: long-form read\n"
                    'title: "A title"\nextraction: ok\n---\n\n## Captured\n\n> x\n'
                ),
            )

            result = render_org(_sorted_items(inbox / "info"))

            self.assertIn(":long_form_read:\n", result)

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

            result = render_org(_sorted_items(inbox / "info"))

            self.assertIn("[[file:2026-08-09_1/][directory]]\n", result)
            self.assertNotIn("source", result)

    def test_generation_failure_preserves_previous_inbox(self):
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            (inbox / "info").mkdir(parents=True)
            (inbox / "info" / "triage.md").write_text("previous", encoding="utf-8")
            item = inbox / "info" / "2026-08-09_1"
            item.mkdir()
            (item / "metadata.json").write_text(
                json.dumps({"received_at": "not-a-time"}), encoding="utf-8"
            )
            (item / "index.md").write_text("---\nid: x\n---\n", encoding="utf-8")
            (item / "capture").mkdir()
            (item / "capture" / "message.md").write_text("message", encoding="utf-8")

            with self.assertRaisesRegex(SyncError, "Invalid received_at"):
                generate_inbox(inbox)

            self.assertEqual((inbox / "info" / "triage.md").read_text(), "previous")

    def test_missing_message_preserves_previous_inbox(self):
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            (inbox / "info").mkdir(parents=True)
            (inbox / "info" / "triage.md").write_text("previous", encoding="utf-8")
            item = inbox / "info" / "2026-08-09_1"
            item.mkdir()
            (item / "metadata.json").write_text(
                json.dumps({"received_at": "2026-08-09T10:00:00+00:00"}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SyncError, "Missing capture/message.md"):
                generate_inbox(inbox)

            self.assertEqual((inbox / "info" / "triage.md").read_text(), "previous")

    def test_message_outside_capture_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            item = inbox / "info" / "2026-08-09_1"
            item.mkdir(parents=True)
            (item / "metadata.json").write_text(
                json.dumps({"received_at": "2026-08-09T10:00:00+00:00"}),
                encoding="utf-8",
            )
            (item / "message.md").write_text("message", encoding="utf-8")

            with self.assertRaisesRegex(SyncError, "Missing capture/message.md"):
                render_inbox(inbox / "info")

    def test_missing_index_preserves_previous_inbox(self):
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            (inbox / "info").mkdir(parents=True, exist_ok=True)
            (inbox / "info" / "triage.md").write_text("previous", encoding="utf-8")
            item = write_item(inbox, "2026-08-09_1", received_at="2026-08-09T10:00:00+00:00")
            (item / "index.md").unlink()

            with self.assertRaisesRegex(SyncError, "Missing index.md"):
                generate_inbox(inbox)

            self.assertEqual((inbox / "info" / "triage.md").read_text(), "previous")


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
                "info/2026-08-08_1\t1\ninfo/2026-08-09_2\t1\n", encoding="utf-8"
            )
            commands = FakeSyncCommands(remote)
            config = SyncConfig("server", "/remote/inbox", local, state)

            synchronize(config, commands)

            self.assertFalse((remote / "info" / "2026-08-08_1").exists())
            self.assertFalse((local / "info" / "2026-08-08_1").exists())
            self.assertTrue((local / "info" / "2026-08-09_2").is_dir())
            self.assertTrue((local / "info" / "2026-08-10_3").is_dir())
            self.assertEqual(
                (state / "delivered-items").read_text(),
                "info/2026-08-08_1\t1\ninfo/2026-08-09_2\t2\ninfo/2026-08-10_3\t1\n",
            )
            aggregate = (local / "info" / "triage.md").read_text()
            self.assertEqual(aggregate.count("— 2026-08-09"), 1)
            self.assertIn("edited", aggregate)
            self.assertIn(
                "[[file:2026-08-09_2/][directory]]", (local / "info" / "triage.org").read_text()
            )
            self.assertTrue(
                any(command[0] == "ssh" and "rm -rf" in command[2] for command in commands.commands)
            )

    def test_missing_local_inbox_safety_check_runs_before_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            state.mkdir()
            (state / "delivered-items").write_text("info/2026-08-08_1\t1\n")
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
            shutil.rmtree(inbox / "info" / "2026-08-09_1")

            with unittest.mock.patch(
                "info_triage.sync.default_config",
                return_value=SyncConfig("server", "/remote/inbox", inbox, Path(temporary) / "state"),
            ), unittest.mock.patch("info_triage.sync.run_command") as runner:
                self.assertEqual(main(["--regenerate"]), 0)

            runner.assert_not_called()
            self.assertIn(
                "** 1 · [[file:2026-08-10_2/", (inbox / "info" / "triage.org").read_text()
            )
            self.assertIn("### 1 — 2026-08-10_2", (inbox / "info" / "triage.md").read_text())
            self.assertNotIn("2026-08-09_1", (inbox / "info" / "triage.md").read_text())


class RouteTests(unittest.TestCase):
    """Each route is its own queue: its own two views, its own numbering."""

    def test_every_route_gets_its_own_views_numbered_from_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            write_item(inbox, "2026-08-09_1", received_at="2026-08-09T10:00:00+00:00")
            write_item(inbox, "2026-08-10_2", received_at="2026-08-10T10:00:00+00:00")
            write_item(
                inbox, "2026-08-09_1", received_at="2026-08-09T11:00:00+00:00", route="job"
            )

            generate_inbox(inbox)

            info_org = (inbox / "info" / "triage.org").read_text()
            job_org = (inbox / "job" / "triage.org").read_text()
            self.assertIn("** 1 · ", info_org)
            self.assertIn("** 2 · ", info_org)
            # Its own numbering, not a continuation of info's.
            self.assertIn("** 1 · ", job_org)
            self.assertNotIn("** 2 · ", job_org)
            # The directory link is still one segment: the views sit beside the
            # items they list, which is what keeps the Emacs side unchanged.
            self.assertIn("[[file:2026-08-09_1/][directory]]", job_org)

    def test_a_route_with_nothing_in_it_still_gets_both_views(self):
        """Absent files cannot be told apart from a sync that never ran."""
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            generate_inbox(inbox)

            for route in ("info", "job", "clip", "lang"):
                self.assertIn("0 items", (inbox / route / "triage.md").read_text())
                self.assertIn("#+STARTUP:", (inbox / route / "triage.org").read_text())

    def test_manifest_keys_carry_the_route(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "delivered-items"
            manifest.write_text("job/2026-08-09_7\t2\n")
            self.assertEqual(read_manifest(manifest), {"job/2026-08-09_7": 2})

            manifest.write_text("2026-08-09_7\t2\n")
            with self.assertRaisesRegex(SyncError, "Invalid delivered-items entry"):
                read_manifest(manifest)

    def test_a_re_routed_item_loses_its_copy_in_the_route_it_left(self):
        """The NAS renamed it, so deletion_decision never sees the old name."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote, local, state = root / "remote", root / "inbox", root / "state"
            remote.mkdir()
            local.mkdir()
            state.mkdir()
            # Delivered under info last time; the NAS now holds it under job.
            write_item(local, "2026-08-09_1", received_at="2026-08-09T10:00:00+00:00")
            write_item(
                remote, "2026-08-09_4", received_at="2026-08-09T10:00:00+00:00",
                revision=2, route="job",
            )
            (state / "delivered-items").write_text("info/2026-08-09_1\t1\n", encoding="utf-8")

            synchronize(SyncConfig("server", "/remote/inbox", local, state), FakeSyncCommands(remote))

            self.assertFalse((local / "info" / "2026-08-09_1").exists())
            self.assertTrue((local / "job" / "2026-08-09_4").is_dir())
            self.assertEqual(
                (state / "delivered-items").read_text(), "job/2026-08-09_4\t2\n"
            )
            self.assertNotIn("2026-08-09_1", (local / "info" / "triage.md").read_text())

    def test_an_item_the_laptop_deleted_is_not_removed_a_second_time(self):
        """Absent locally means processed, which the deletion pass must not touch."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local, state = root / "inbox", root / "state"
            local.mkdir()
            state.mkdir()
            delivered = {"info/2026-08-09_1": 1}

            _remove_stale_local_items(
                SyncConfig("server", "/remote/inbox", local, state),
                [RemoteItem("job", "2026-08-09_2", 1, 2)],
                delivered,
            )

            self.assertEqual(delivered, {"info/2026-08-09_1": 1})

    def test_an_empty_remote_listing_never_wipes_the_inbox(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local, state = root / "inbox", root / "state"
            local.mkdir()
            state.mkdir()
            write_item(local, "2026-08-09_1", received_at="2026-08-09T10:00:00+00:00")

            with self.assertRaisesRegex(SyncError, "listed no items"):
                _remove_stale_local_items(
                    SyncConfig("server", "/remote/inbox", local, state),
                    [],
                    {"info/2026-08-09_1": 1},
                )

            self.assertTrue((local / "info" / "2026-08-09_1").is_dir())

    def test_the_metadata_pass_descends_two_levels(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote, local, state = root / "remote", root / "inbox", root / "state"
            remote.mkdir()
            local.mkdir()
            state.mkdir()
            write_item(remote, "2026-08-09_1", received_at="2026-08-09T10:00:00+00:00")
            commands = FakeSyncCommands(remote)

            synchronize(SyncConfig("server", "/remote/inbox", local, state), commands)

            metadata_pass = next(
                command for command in commands.commands
                if command[0] == "rsync" and "--include" in command
            )
            self.assertEqual(
                [value for value in metadata_pass if value.startswith("/*")],
                ["/*/", "/*/*/", "/*/*/metadata.json"],
            )

    def test_an_unknown_route_directory_on_the_nas_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            metadata = Path(temporary)
            (metadata / "invented").mkdir()
            with self.assertRaisesRegex(SyncError, "Unexpected NAS inbox route directory"):
                read_remote_items(metadata)


BLOCK = (
    "<!-- neighbours:begin -->\n\n## Possible neighbours — unverified\n\n<!-- neighbours:end -->"
)


class FakeNeighbours:
    """Stands in for `info_triage.neighbours`: no corpora, no model, no network."""

    def __init__(self, blocks=None, error=None, during=None):
        self.blocks = blocks or {}
        self.error = error
        self.during = during
        self.queries: list[tuple[str, str]] = []
        self.reported: list[tuple[str, int, int]] = []

    def annotate(
        self, queries, org_root, obsidian_root, *, org_exclude=(), scorer=None, progress=None
    ):
        self.queries = list(queries)
        self.org_exclude = tuple(org_exclude)
        for position, (key, _) in enumerate(self.queries, start=1):
            if progress is not None:
                progress(key, position, len(self.queries))
        if self.during is not None:
            self.during()
        if self.error is not None:
            raise self.error
        return {key: self.blocks[key] for key, _ in self.queries if key in self.blocks}

    @staticmethod
    def apply_block(index, block):
        """Deliberately the real one: inserting a block is string surgery, not a model."""
        from info_triage.neighbours import apply_block

        return apply_block(index, block)


class NeighbourAnnotationTests(unittest.TestCase):
    """The annotation runs after delivery, and may never cost an item or a sync."""

    @contextlib.contextmanager
    def inbox(self, *, notes=True):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote, local, state = root / "remote", root / "local", root / "state"
            for directory in (remote, local, state):
                directory.mkdir()
            write_item(remote, "2026-08-09_1", received_at="2026-08-09T09:00:00+00:00")
            write_item(remote, "2026-08-10_2", received_at="2026-08-10T09:00:00+00:00")
            corpus = root / "notes"
            corpus.mkdir()
            config = SyncConfig(
                "server",
                "/remote/inbox",
                local,
                state,
                org_root=corpus if notes else None,
                obsidian_root=corpus if notes else None,
            )
            yield config, FakeSyncCommands(remote)

    def run_sync(self, config, commands, neighbours):
        output = io.StringIO()
        with (
            unittest.mock.patch("info_triage.sync._neighbours_module", return_value=neighbours),
            contextlib.redirect_stdout(output),
        ):
            synchronize(config, commands)
        return output.getvalue()

    def test_a_collapsed_duplicate_is_not_annotated(self):
        """Its block would be written into an index whose section `triage.md` does
        not print, so the pass would spend its time on nothing."""
        url = "https://example.com/post"
        with self.inbox() as (config, commands):
            for name, day in (("2026-08-11_3", 11), ("2026-08-12_4", 12)):
                received_at = f"2026-08-{day}T09:00:00+00:00"
                write_item(
                    commands.remote_inbox,
                    name,
                    received_at=received_at,
                    index=linked_index(name, received_at, url),
                )
            neighbours = FakeNeighbours()
            self.run_sync(config, commands, neighbours)

        self.assertEqual(
            [key for key, _ in neighbours.queries],
            ["2026-08-09_1", "2026-08-10_2", "2026-08-11_3"],
        )

    def test_the_queue_is_complete_before_the_annotation_starts(self):
        """The whole point of running late: both views are usable while this works."""
        seen = {}
        with self.inbox() as (config, commands):
            digest = config.local_inbox / "info" / DIGEST_NAME
            neighbours = FakeNeighbours(
                blocks={"2026-08-09_1": BLOCK},
                during=lambda: seen.update(
                    digest=digest.read_text(),
                    org=(config.local_inbox / "info" / ORG_NAME).read_text(),
                ),
            )
            self.run_sync(config, commands, neighbours)

        self.assertIn("### 1 — 2026-08-09_1", seen["digest"])
        self.assertIn("### 2 — 2026-08-10_2", seen["digest"])
        self.assertIn("** 2 · ", seen["org"])
        self.assertNotIn("neighbours:begin", seen["digest"])
        self.assertEqual([key for key, _ in neighbours.queries], ["2026-08-09_1", "2026-08-10_2"])

    def test_the_block_reaches_the_item_and_the_digest(self):
        with self.inbox() as (config, commands):
            neighbours = FakeNeighbours(blocks={"2026-08-10_2": BLOCK})
            self.run_sync(config, commands, neighbours)

            info = config.local_inbox / "info"
            self.assertIn("neighbours:begin", (info / "2026-08-10_2" / "index.md").read_text())
            self.assertNotIn("neighbours:begin", (info / "2026-08-09_1" / "index.md").read_text())
            digest = (info / DIGEST_NAME).read_text()
            self.assertEqual(digest.count("neighbours:begin"), 1)
            # The item's own headings are demoted into the digest; the block's are too.
            self.assertIn("#### Possible neighbours — unverified", digest)
            # Navigation only, and no frontmatter changed: the Org view is untouched.
            self.assertNotIn("neighbours", (info / ORG_NAME).read_text())

    def test_an_item_dropped_while_the_pass_runs_is_left_alone(self):
        """The user works the queue meanwhile. A gone item is logged, never recreated."""
        with self.inbox() as (config, commands):
            dropped = config.local_inbox / "info" / "2026-08-09_1"
            neighbours = FakeNeighbours(
                blocks={"2026-08-09_1": BLOCK, "2026-08-10_2": BLOCK},
                during=lambda: shutil.rmtree(dropped),
            )
            output = self.run_sync(config, commands, neighbours)

            self.assertFalse(dropped.exists())
            self.assertIn("no longer here", output)
            info = config.local_inbox / "info"
            self.assertIn("neighbours:begin", (info / "2026-08-10_2" / "index.md").read_text())
            digest = (info / DIGEST_NAME).read_text()
            self.assertNotIn("2026-08-09_1", digest)
            self.assertIn("### 1 — 2026-08-10_2", digest)

    def test_a_failing_annotation_leaves_the_delivered_inbox_intact(self):
        with self.inbox() as (config, commands):
            neighbours = FakeNeighbours(error=RuntimeError("no model"))
            output = self.run_sync(config, commands, neighbours)

            self.assertIn("skipped", output)
            info = config.local_inbox / "info"
            self.assertTrue((info / "2026-08-09_1" / "index.md").is_file())
            self.assertIn("### 2 — 2026-08-10_2", (info / DIGEST_NAME).read_text())

    def test_a_laptop_without_the_extra_still_synchronizes(self):
        with self.inbox() as (config, commands):
            output = io.StringIO()
            with (
                unittest.mock.patch(
                    "info_triage.sync._neighbours_module", side_effect=ImportError("no torch")
                ),
                contextlib.redirect_stdout(output),
            ):
                synchronize(config, commands)

            self.assertIn("uv sync --extra neighbours", output.getvalue())
            self.assertIn(
                "### 1 — 2026-08-09_1", (config.local_inbox / "info" / DIGEST_NAME).read_text()
            )

    def test_without_configured_notes_nothing_is_annotated(self):
        with self.inbox(notes=False) as (config, commands):
            with unittest.mock.patch("info_triage.sync._neighbours_module") as module:
                with contextlib.redirect_stdout(io.StringIO()):
                    synchronize(config, commands)
            module.assert_not_called()

    def test_only_the_info_route_is_annotated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inbox, corpus = root / "inbox", root / "notes"
            inbox.mkdir()
            corpus.mkdir()
            write_item(inbox, "2026-08-09_1", received_at="2026-08-09T09:00:00+00:00", route="job")
            neighbours = FakeNeighbours(blocks={"2026-08-09_1": BLOCK})
            config = SyncConfig(
                "server", "/remote/inbox", inbox, root / "state", org_root=corpus, obsidian_root=corpus
            )
            with (
                unittest.mock.patch("info_triage.sync._neighbours_module", return_value=neighbours),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                annotate_inbox(config)

            self.assertEqual(neighbours.queries, [])
            self.assertNotIn(
                "neighbours", (inbox / "job" / "2026-08-09_1" / "index.md").read_text()
            )

    def test_no_neighbours_turns_the_pass_off_for_the_whole_sync(self):
        with self.inbox() as (config, _commands):
            with unittest.mock.patch("info_triage.sync.default_config", return_value=config):
                with unittest.mock.patch("info_triage.sync.synchronize") as sync:
                    self.assertEqual(main(["--no-neighbours"]), 0)
                    self.assertFalse(sync.call_args.args[0].annotate)
                with unittest.mock.patch("info_triage.sync.synchronize") as sync:
                    self.assertEqual(main([]), 0)
                    self.assertTrue(sync.call_args.args[0].annotate)

    def test_regenerate_reuses_the_sections_already_in_the_items(self):
        """Renumbering after a drop must stay instant, and the blocks are on disk."""
        with self.inbox() as (config, commands):
            neighbours = FakeNeighbours(blocks={"2026-08-09_1": BLOCK})
            self.run_sync(config, commands, neighbours)
            neighbours.queries = []

            with (
                unittest.mock.patch("info_triage.sync._neighbours_module", return_value=neighbours),
                unittest.mock.patch("info_triage.sync.default_config", return_value=config),
                unittest.mock.patch("info_triage.sync.run_command") as runner,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(main(["--regenerate"]), 0)
                self.assertEqual(neighbours.queries, [])
                self.assertEqual(main(["--regenerate", "--neighbours"]), 0)
                self.assertEqual(
                    [key for key, _ in neighbours.queries], ["2026-08-09_1", "2026-08-10_2"]
                )

            runner.assert_not_called()
            digest = (config.local_inbox / "info" / DIGEST_NAME).read_text()
            # One block, whichever path put it there.
            self.assertEqual(digest.count("neighbours:begin"), 1)


class SettingsTests(unittest.TestCase):
    """The laptop settings: defaults, then sync.toml, then the environment."""

    def settings(self, text=None):
        """An environment whose XDG_CONFIG_HOME holds a sync.toml with TEXT, if any."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        if text is not None:
            (base / "info-triage").mkdir()
            (base / "info-triage" / "sync.toml").write_text(text, encoding="utf-8")
        return {"XDG_CONFIG_HOME": str(base)}

    def config(self, environ):
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            config = default_config(environ)
        return config, errors.getvalue()

    def test_without_a_settings_file_the_defaults_apply(self):
        config, errors = self.config(self.settings())
        self.assertEqual(config.org_root, Path.home() / NOTES_DIR / "org")
        self.assertEqual(config.obsidian_root, Path.home() / NOTES_DIR / "obsidian")
        self.assertEqual(config.org_exclude, DEFAULT_ORG_EXCLUDE)
        self.assertEqual(errors, "")

    def test_the_settings_file_sets_the_roots_and_the_exclusions(self):
        environ = self.settings(
            "[neighbours]\n"
            'org_root = "~/notes/org"\n'
            'obsidian_root = "/srv/obsidian"\n'
            'org_exclude = ["Inbox.org", "Unsorted.org"]\n'
        )
        config, _ = self.config(environ)
        self.assertEqual(config.org_root, Path.home() / "notes/org")
        self.assertEqual(config.obsidian_root, Path("/srv/obsidian"))
        # Replaces the default list rather than adding to it.
        self.assertEqual(config.org_exclude, ("Inbox.org", "Unsorted.org"))

    def test_the_environment_overrides_the_settings_file(self):
        environ = self.settings(
            '[neighbours]\norg_root = "/from/file"\norg_exclude = ["Inbox.org"]\n'
        )
        environ |= {
            "INFO_TRIAGE_ORG_ROOT": "/from/environment",
            "INFO_TRIAGE_ORG_EXCLUDE": "Inbox.org, Someday.org,",
        }
        config, _ = self.config(environ)
        self.assertEqual(config.org_root, Path("/from/environment"))
        self.assertEqual(config.org_exclude, ("Inbox.org", "Someday.org"))

    def test_an_empty_value_turns_a_corpus_off(self):
        environ = self.settings('[neighbours]\nobsidian_root = ""\n')
        self.assertIsNone(self.config(environ)[0].obsidian_root)
        environ = self.settings() | {"INFO_TRIAGE_ORG_ROOT": ""}
        self.assertIsNone(self.config(environ)[0].org_root)

    def test_an_empty_exclusion_list_searches_every_file(self):
        environ = self.settings() | {"INFO_TRIAGE_ORG_EXCLUDE": ""}
        self.assertEqual(self.config(environ)[0].org_exclude, ())

    def test_a_bad_settings_file_is_reported_and_ignored(self):
        for text in (
            "[neighbours]\nvault_root = '/old/name'\n",
            "[remote]\nhost = 'server'\n",
            "[neighbours]\norg_exclude = 'Inbox.org'\n",
            "[neighbours]\norg_root = 3\n",
            "not toml at all = = =\n",
        ):
            with self.subTest(text=text):
                config, errors = self.config(self.settings(text))
                self.assertIn("Ignoring the settings in", errors)
                self.assertEqual(config.org_exclude, DEFAULT_ORG_EXCLUDE)
                self.assertEqual(config.org_root, Path.home() / NOTES_DIR / "org")


class NeighbourQueryTests(unittest.TestCase):
    def test_the_query_is_the_title_the_intent_and_the_lead(self):
        index = (
            "---\nid: x\ntitle: KV cache transfer\nintent: read before the interview\n---\n\n"
            "## Lead\n\n> the paper argues something\n\n## Links\n\n- https://example.com\n"
        )
        self.assertEqual(
            neighbour_query(index),
            "KV cache transfer. read before the interview. the paper argues something",
        )

    def test_a_short_form_lead_keeps_its_streams_and_drops_their_labels(self):
        index = (
            "---\nid: x\nheadline: A reel\nintent: null\n---\n\n## Lead\n\n"
            "> **Caption**\n> what the poster wrote\n>\n> **Spoken audio**\n> what was said\n"
        )
        self.assertEqual(neighbour_query(index), "A reel. what the poster wrote what was said")

    def test_an_item_with_no_lead_falls_back_to_what_was_captured(self):
        index = "---\nid: x\nintent: null\n---\n\n## Captured\n\n> [A page](https://e.com)\n"
        self.assertEqual(neighbour_query(index), "[A page](https://e.com)")



class DuplicateCaptureTests(unittest.TestCase):
    """Saving one link twice is an ordinary accident: the views collapse it, and
    nothing on disk is touched."""

    URL = "https://example.com/post"

    def inbox(self, temporary, *items):
        """Write ITEMS as `(name, day, index kwargs or None)`, oldest first."""
        inbox = Path(temporary)
        for name, day, kwargs in items:
            received_at = f"2026-08-{day:02d}T09:00:00+00:00"
            index = None if kwargs is None else linked_index(name, received_at, **kwargs)
            write_item(inbox, name, received_at=received_at, index=index)
        return inbox / "info"

    def test_a_repeat_capture_is_matched_to_the_earliest_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            route = self.inbox(
                temporary,
                ("2026-08-09_1", 9, {"url": self.URL}),
                ("2026-08-10_2", 10, {"url": self.URL}),
                ("2026-08-11_3", 11, {"url": self.URL}),
            )

            duplicates = duplicate_captures(_sorted_items(route))

            # Both later copies point at the earliest, never at each other.
            self.assertEqual(
                {name: value.of for name, value in duplicates.items()},
                {"2026-08-10_2": "2026-08-09_1", "2026-08-11_3": "2026-08-09_1"},
            )
            self.assertTrue(all(value.collapse for value in duplicates.values()))

    def test_a_different_link_a_link_list_and_a_note_are_never_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            route = self.inbox(
                temporary,
                ("2026-08-09_1", 9, {"url": self.URL}),
                ("2026-08-10_2", 10, {"url": "https://example.com/other"}),
                # A link list's canonical_url is only its top-priority row, so two
                # reading lists sharing a first link are still two items.
                ("2026-08-11_3", 11, {"url": self.URL, "kind": "linklist"}),
                ("2026-08-12_4", 12, None),
            )

            self.assertEqual(duplicate_captures(_sorted_items(route)), {})

    def test_the_digest_keeps_only_what_could_differ(self):
        with tempfile.TemporaryDirectory() as temporary:
            route = self.inbox(
                temporary,
                ("2026-08-09_1", 9, {"url": self.URL}),
                ("2026-08-10_2", 10, {"url": self.URL, "captured": "> my second thought"}),
            )

            result = render_inbox(route)
            first, second = result.split("### 2 — 2026-08-10_2")

            # The representative is untouched, and carries what the stub drops.
            self.assertIn("#### Sources", first)
            self.assertIn("#### Links", first)
            # The stub names the item by the number the reader selects by, keeps
            # the frontmatter verbatim, and keeps the one section that can differ.
            self.assertIn(
                "**Duplicate capture** — the same link as item 1 (`2026-08-09_1`)", second
            )
            self.assertIn("```yaml\nid: 2026-08-10_2", second)
            self.assertIn(f"canonical_url: {self.URL}", second)
            self.assertIn("#### Captured\n\n> my second thought", second)
            for dropped in ("#### Sources", "#### Lead", "#### Links"):
                self.assertNotIn(dropped, second)

    def test_problems_are_never_dropped_from_a_stub(self):
        """One of the three places a failure surfaces; all three keep saying it."""
        with tempfile.TemporaryDirectory() as temporary:
            route = self.inbox(
                temporary,
                ("2026-08-09_1", 9, {"url": self.URL}),
                ("2026-08-10_2", 10, {"url": self.URL, "problem": "url-resolution: timed out"}),
            )

            second = render_inbox(route).split("### 2 — 2026-08-10_2")[1]

            self.assertIn("#### Problems\n\n- url-resolution: timed out", second)
            self.assertNotIn("#### Sources", second)

    def test_a_duplicate_is_printed_in_full_when_the_earliest_retrieved_nothing(self):
        """Collapsing may never hide what the representative does not itself carry."""
        with tempfile.TemporaryDirectory() as temporary:
            route = self.inbox(
                temporary,
                ("2026-08-09_1", 9, {"url": self.URL, "sources": False}),
                ("2026-08-10_2", 10, {"url": self.URL}),
            )

            self.assertFalse(duplicate_captures(_sorted_items(route))["2026-08-10_2"].collapse)

            second = render_inbox(route).split("### 2 — 2026-08-10_2")[1]
            # Still marked, so the repeat is never left for the reader to notice.
            self.assertIn("**Duplicate capture** — the same link as item 1", second)
            self.assertIn("which retrieved nothing", second)
            self.assertIn("#### Sources", second)
            self.assertIn("#### Lead", second)

    def test_the_org_view_marks_the_duplicate_and_keeps_its_own_number(self):
        with tempfile.TemporaryDirectory() as temporary:
            route = self.inbox(
                temporary,
                ("2026-08-09_1", 9, {"url": self.URL}),
                ("2026-08-10_2", 10, {"url": self.URL}),
            )

            lines = render_org(_sorted_items(route)).splitlines()
            heading = next(line for line in lines if line.startswith("** 2 · "))
            trail = lines[lines.index(heading) + 1]

            # The kind keeps its tag; `dup` joins it rather than replacing it.
            self.assertTrue(heading.endswith("  :post:dup:"), heading)
            self.assertNotIn(":dup:", next(line for line in lines if line.startswith("** 1 · ")))
            # The directory link stays first: Emacs reads the item's id out of it.
            self.assertTrue(trail.strip().startswith("[[file:2026-08-10_2/][directory]]"), trail)
            self.assertTrue(trail.endswith(" · dup of 1"), trail)
