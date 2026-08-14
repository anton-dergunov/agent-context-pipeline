import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from telegram import Message
from telegram.ext import CallbackQueryHandler

from info_triage.models import AttachmentSpec, DownloadedAttachment
from info_triage.storage import CaptureStore
from info_triage.telegram_bot import (
    _finalize_batch,
    _pending_batches,
    attachment_specs_from_payload,
    build_application,
    capture_content,
    group_new_messages,
    render_capture_payloads,
)


def promote(store, item):
    store.promote_if_current(item)
    return item


def message(payload):
    return Message.de_json(
        {
            "message_id": 1,
            "date": 1,
            "chat": {"id": 10, "type": "private"},
            **payload,
        }
    )


def telegram_payload(message_id, date, **values):
    return {
        "message_id": message_id,
        "date": date,
        "chat": {"id": 10, "type": "private"},
        "from": {"id": 20, "first_name": "Owner", "is_bot": False},
        **values,
    }


def pending_row(message_id, received_at, media_group_id=None):
    payload = telegram_payload(message_id, message_id, text=str(message_id))
    return {
        "chat_id": 10,
        "message_id": message_id,
        "media_group_id": media_group_id,
        "received_at": received_at,
        "edited_at": None,
        "content": str(message_id),
        "payload": payload,
        "specs": [],
    }


class FakeFile:
    def __init__(self, data):
        self.data = data

    async def download_as_bytearray(self):
        return bytearray(self.data)


class FakeBot:
    def __init__(self, files=None):
        self.files = files or {}
        self.sent_messages = []

    async def get_file(self, file_id):
        value = self.files[file_id]
        if isinstance(value, Exception):
            raise value
        return FakeFile(value)

    async def send_message(self, chat_id, text):
        self.sent_messages.append((chat_id, text))


class ImmediateCoordinator:
    def __init__(self, store):
        self.store = store

    def submit(self, item):
        self.store.promote_if_current(item)


def fake_application(store, bot=None):
    return SimpleNamespace(
        bot=bot or FakeBot(),
        bot_data={"store": store, "coordinator": ImmediateCoordinator(store)},
    )


class CaptureContentTests(unittest.TestCase):
    def test_all_supported_file_types_have_attachment_specs(self):
        media = {
            "photo": [{"file_id": "photo", "file_unique_id": "photo-unique"}],
            "document": {"file_id": "document", "file_unique_id": "document-unique"},
            "video": {"file_id": "video", "file_unique_id": "video-unique"},
            "animation": {
                "file_id": "animation",
                "file_unique_id": "animation-unique",
            },
            "audio": {"file_id": "audio", "file_unique_id": "audio-unique"},
            "voice": {"file_id": "voice", "file_unique_id": "voice-unique"},
            "video_note": {
                "file_id": "video_note",
                "file_unique_id": "video_note-unique",
            },
        }
        for kind, value in media.items():
            specs = attachment_specs_from_payload({"message_id": 1, kind: value})
            self.assertEqual([spec.kind for spec in specs], [kind])

    def test_supported_and_ignored_message_types(self):
        self.assertEqual(
            capture_content(message({"text": "https://example.com"})),
            "https://example.com",
        )
        self.assertEqual(
            capture_content(
                message(
                    {"voice": {"file_id": "v", "file_unique_id": "uv", "duration": 1}}
                )
            ),
            "",
        )
        self.assertIsNone(
            capture_content(message({"dice": {"emoji": "🎲", "value": 6}}))
        )
        self.assertIsNone(
            capture_content(
                message({"contact": {"phone_number": "1", "first_name": "Nope"}})
            )
        )

    def test_location_is_readable_markdown(self):
        content = capture_content(
            message(
                {
                    "venue": {
                        "location": {"latitude": 51.5, "longitude": -0.1},
                        "title": "Library",
                        "address": "Main St",
                    }
                }
            )
        )
        self.assertIn("## Library", content)
        self.assertIn("51.5,-0.1", content)
        self.assertIn("Open in maps", content)


class CaptureStoreTests(unittest.TestCase):
    def test_new_capture_defaults_to_other_without_a_category_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))
            promote(
                store,
                store.capture(
                    10,
                    1,
                    "plain",
                    received_at="2026-08-09T10:00:00+00:00",
                ),
            )
            item = Path(temporary) / "inbox" / "2026-08-09_1"
            metadata = json.loads((item / "metadata.json").read_text())
            self.assertEqual(metadata["category"], "Other")
            self.assertEqual(metadata["revision"], 1)
            self.assertEqual(
                (item / "capture" / "message.md").read_text(),
                "---\ncategory: Other\n---\n\nplain",
            )
            self.assertEqual((item / "capture" / "source.md").read_text(), "plain")

    def test_capture_artifacts_live_under_capture_beside_root_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))
            voice = AttachmentSpec(
                "voice", "voice", "unique-voice", 3, "audio/ogg", None, ".ogg", 1
            )
            promote(
                store,
                store.capture(
                    10,
                    1,
                    "spoken",
                    received_at="2026-08-09T10:00:00+00:00",
                    telegram_payload={"message_id": 1, "voice": {"file_id": "voice"}},
                    attachments=[DownloadedAttachment(voice, b"ogg")],
                ),
            )
            item = Path(temporary) / "inbox" / "2026-08-09_1"

            self.assertEqual(
                sorted(entry.name for entry in item.iterdir()),
                ["capture", "metadata.json"],
            )
            self.assertEqual(
                sorted(entry.name for entry in (item / "capture").iterdir()),
                ["attachments", "message.md", "source.md", "telegram.json"],
            )
            metadata = json.loads((item / "metadata.json").read_text())
            self.assertEqual(
                metadata["attachments"][0]["path"],
                "capture/attachments/01-voice.ogg",
            )

    def test_partial_attachment_update_preserves_other_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))
            forwarded = telegram_payload(
                1,
                100,
                caption="Shared source",
                forward_origin={
                    "type": "channel",
                    "date": 50,
                    "chat": {"id": -1001, "type": "channel", "title": "Source"},
                    "message_id": 9,
                },
                photo=[
                    {
                        "file_id": "photo-1",
                        "file_unique_id": "unique-1",
                        "width": 100,
                        "height": 100,
                    }
                ],
            )
            note = telegram_payload(2, 101, text="Initial note")
            for payload, received_at in (
                (forwarded, "2026-08-09T10:00:00+00:00"),
                (note, "2026-08-09T10:00:01+00:00"),
            ):
                store.stage_pending_message(
                    10,
                    payload["message_id"],
                    None,
                    payload.get("text") or payload.get("caption") or "",
                    received_at,
                    None,
                    payload,
                    attachment_specs_from_payload(payload),
                )
            app = fake_application(store, FakeBot({"photo-1": b"first-photo"}))
            asyncio.run(_finalize_batch(app, 10, None, store.pending_messages(10)))

            item_path = Path(temporary) / "inbox" / "2026-08-09_1"
            metadata = json.loads((item_path / "metadata.json").read_text())
            attachment_path = item_path / metadata["attachments"][0]["path"]
            self.assertEqual(attachment_path.read_bytes(), b"first-photo")
            self.assertEqual(metadata["source_message_ids"], [1, 2])
            self.assertEqual(store.item_for_source_message(10, 2)["message_id"], 1)

            edited_note = telegram_payload(2, 101, text="Updated note")
            store.stage_pending_message(
                10,
                2,
                None,
                "Updated note",
                "2026-08-09T10:00:01+00:00",
                "2026-08-09T10:05:00+00:00",
                edited_note,
                [],
            )
            target, rows = _pending_batches(store, 10)[0]
            self.assertEqual(target, 1)
            asyncio.run(_finalize_batch(app, 10, target, rows))
            metadata = json.loads((item_path / "metadata.json").read_text())
            self.assertEqual(metadata["revision"], 2)
            self.assertEqual(
                (item_path / metadata["attachments"][0]["path"]).read_bytes(),
                b"first-photo",
            )
            message_text = (item_path / "capture" / "message.md").read_text()
            self.assertIn("## Segment 1 — forwarded caption", message_text)
            self.assertIn("## Segment 2 — text", message_text)
            self.assertIn("Updated note", message_text)

            edited_forward = telegram_payload(
                1,
                100,
                caption="Updated source",
                forward_origin=forwarded["forward_origin"],
                photo=[
                    {
                        "file_id": "photo-2",
                        "file_unique_id": "unique-2",
                        "width": 100,
                        "height": 100,
                    }
                ],
            )
            store.stage_pending_message(
                10,
                1,
                None,
                "Updated source",
                "2026-08-09T10:00:00+00:00",
                "2026-08-09T10:06:00+00:00",
                edited_forward,
                attachment_specs_from_payload(edited_forward),
            )
            app.bot.files["photo-2"] = b"second-photo"
            target, rows = _pending_batches(store, 10)[0]
            asyncio.run(_finalize_batch(app, 10, target, rows))
            metadata = json.loads((item_path / "metadata.json").read_text())
            self.assertEqual(metadata["revision"], 3)
            self.assertEqual(
                (item_path / metadata["attachments"][0]["path"]).read_bytes(),
                b"second-photo",
            )
            self.assertIn("Updated source", (item_path / "capture" / "message.md").read_text())

    def test_pending_messages_survive_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            payload = telegram_payload(1, 100, text="pending")
            CaptureStore(data_dir).stage_pending_message(
                10,
                1,
                None,
                "pending",
                "2026-08-09T10:00:00+00:00",
                None,
                payload,
                [],
            )
            recovered = CaptureStore(data_dir)
            self.assertEqual(recovered.pending_chat_ids(), [10])
            self.assertEqual(recovered.pending_messages(10)[0]["payload"], payload)

    def test_legacy_pending_album_is_migrated(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            database_path = data_dir / "info-triage.sqlite3"
            payload = telegram_payload(1, 100, text="album caption")
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE pending_media_group_members (
                        chat_id INTEGER NOT NULL,
                        media_group_id TEXT NOT NULL,
                        message_id INTEGER NOT NULL,
                        received_at TEXT NOT NULL,
                        edited_at TEXT,
                        content TEXT NOT NULL,
                        raw_json TEXT NOT NULL,
                        attachments_json TEXT NOT NULL,
                        PRIMARY KEY (chat_id, media_group_id, message_id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE pending_media_groups (
                        chat_id INTEGER NOT NULL,
                        media_group_id TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (chat_id, media_group_id)
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO pending_media_group_members VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        10,
                        "album",
                        1,
                        "2026-08-09T10:00:00+00:00",
                        None,
                        "album caption",
                        json.dumps(payload),
                        "[]",
                    ),
                )
                connection.execute(
                    "INSERT INTO pending_media_groups VALUES (?, ?, ?)",
                    (10, "album", "2026-08-09T10:00:01+00:00"),
                )

            store = CaptureStore(data_dir)
            migrated = store.pending_messages(10)
            self.assertEqual(migrated[0]["media_group_id"], "album")
            self.assertEqual(migrated[0]["payload"], payload)
            with sqlite3.connect(database_path) as connection:
                old_tables = connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name LIKE 'pending_media_group%'
                    """
                ).fetchall()
            self.assertEqual(old_tables, [])

    def test_failed_finalization_keeps_pending_messages(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))
            payload = telegram_payload(
                1,
                100,
                photo=[
                    {
                        "file_id": "broken",
                        "file_unique_id": "unique",
                        "width": 100,
                        "height": 100,
                    }
                ],
            )
            store.stage_pending_message(
                10,
                1,
                None,
                "",
                "2026-08-09T10:00:00+00:00",
                None,
                payload,
                attachment_specs_from_payload(payload),
            )
            app = fake_application(store, FakeBot({"broken": RuntimeError("offline")}))
            with self.assertRaisesRegex(RuntimeError, "offline"):
                asyncio.run(_finalize_batch(app, 10, None, store.pending_messages(10)))
            self.assertEqual([row["message_id"] for row in store.pending_messages(10)], [1])

    def test_existing_category_update_still_preserves_attachments(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))
            voice = AttachmentSpec(
                "voice", "voice", "unique-voice", 3, "audio/ogg", None, ".ogg", 1
            )
            promote(
                store,
                store.capture(
                    10,
                    1,
                    "voice note",
                    received_at="2026-08-09T10:00:00+00:00",
                    telegram_payload={
                        "message_id": 1,
                        "voice": {"file_id": "voice"},
                    },
                    attachments=[DownloadedAttachment(voice, b"ogg")],
                ),
            )
            item = Path(temporary) / "inbox" / "2026-08-09_1"
            self.assertEqual(
                (item / "capture" / "attachments" / "01-voice.ogg").read_bytes(), b"ogg"
            )
            self.assertEqual(
                json.loads((item / "capture" / "telegram.json").read_text())["message_id"], 1
            )
            promote(store, store.categorize(10, 1, "Life"))
            self.assertEqual(
                (item / "capture" / "attachments" / "01-voice.ogg").read_bytes(), b"ogg"
            )


class CaptureGroupingTests(unittest.TestCase):
    def test_three_second_consecutive_gaps_chain_and_four_seconds_split(self):
        rows = [
            pending_row(1, "2026-08-09T10:00:00+00:00"),
            pending_row(2, "2026-08-09T10:00:03+00:00"),
            pending_row(3, "2026-08-09T10:00:06+00:00"),
            pending_row(4, "2026-08-09T10:00:10+00:00"),
        ]
        self.assertEqual(
            [[row["message_id"] for row in batch] for batch in group_new_messages(rows)],
            [[1, 2, 3], [4]],
        )

    def test_album_is_atomic_and_uses_its_last_timestamp_for_the_next_gap(self):
        rows = [
            pending_row(1, "2026-08-09T10:00:00+00:00", "album"),
            pending_row(2, "2026-08-09T10:00:05+00:00", "album"),
            pending_row(3, "2026-08-09T10:00:08+00:00"),
        ]
        self.assertEqual(
            [[row["message_id"] for row in batch] for batch in group_new_messages(rows)],
            [[1, 2, 3]],
        )

    def test_forwarded_source_is_labeled_without_guessing_about_the_note(self):
        note = telegram_payload(1, 100, text="My note")
        forwarded = telegram_payload(
            2,
            101,
            text="Shared post",
            forward_origin={
                "type": "channel",
                "date": 50,
                "chat": {"id": -1001, "type": "channel", "title": "Source"},
                "message_id": 9,
            },
        )
        self.assertEqual(
            render_capture_payloads([note, forwarded]),
            "## Segment 1 — text\n\nMy note\n\n"
            "## Segment 2 — forwarded text\n\nShared post",
        )

    def test_unmarked_url_and_note_remain_chronological(self):
        url = telegram_payload(1, 100, text="https://example.com")
        note = telegram_payload(2, 100, text="Read this later")
        self.assertEqual(
            render_capture_payloads([url, note]),
            "## Segment 1 — text\n\nhttps://example.com\n\n"
            "## Segment 2 — text\n\nRead this later",
        )

    def test_application_has_no_category_callback_handler(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CaptureStore(Path(temporary))
            application = build_application(
                "123:token",
                20,
                store,
                ImmediateCoordinator(store),
                grouping_max_gap_seconds=1.5,
                grouping_settle_seconds=2.5,
            )
            self.assertEqual(
                application.bot_data["capture_group_max_gap_seconds"], 1.5
            )
            self.assertEqual(
                application.bot_data["capture_group_settle_seconds"], 2.5
            )
            handlers = [
                handler for values in application.handlers.values() for handler in values
            ]
            self.assertFalse(
                any(isinstance(handler, CallbackQueryHandler) for handler in handlers)
            )


if __name__ == "__main__":
    unittest.main()
