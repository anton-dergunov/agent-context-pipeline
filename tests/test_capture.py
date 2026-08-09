import json
import tempfile
import unittest
from pathlib import Path

from telegram import Message

from info_triage.models import AttachmentSpec, DownloadedAttachment
from info_triage.storage import CaptureStore
from info_triage.telegram_bot import attachment_specs_from_payload, capture_content


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
    def test_attachments_warnings_category_and_album(self):
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
                (item / "attachments" / "01-voice.ogg").read_bytes(), b"ogg"
            )
            self.assertEqual(
                json.loads((item / "telegram.json").read_text())["message_id"], 1
            )
            promote(store, store.categorize(10, 1, "Life"))
            self.assertEqual(
                (item / "attachments" / "01-voice.ogg").read_bytes(), b"ogg"
            )

            photo_payload = {
                "message_id": 2,
                "photo": [{"file_id": "p", "file_unique_id": "up"}],
            }
            video_payload = {
                "message_id": 3,
                "video": {
                    "file_id": "v",
                    "file_unique_id": "uv",
                    "mime_type": "video/mp4",
                },
            }
            store.stage_media_group_member(
                10,
                "album",
                2,
                "caption",
                "2026-08-09T10:00:01+00:00",
                None,
                photo_payload,
                attachment_specs_from_payload(photo_payload),
            )
            store.stage_media_group_member(
                10,
                "album",
                3,
                "",
                "2026-08-09T10:00:02+00:00",
                None,
                video_payload,
                attachment_specs_from_payload(video_payload),
            )
            bundle = store.media_group_capture(10, "album")
            self.assertEqual(bundle["source_message_ids"], [2, 3])
            self.assertEqual(bundle["content"], "caption")
            self.assertEqual(
                [spec.kind for spec in bundle["specs"]], ["photo", "video"]
            )

            unavailable = DownloadedAttachment(
                bundle["specs"][0], None, "photo exceeds limit"
            )
            downloaded = DownloadedAttachment(bundle["specs"][1], b"video")
            promote(
                store,
                store.capture(
                    10,
                    bundle["message_id"],
                    bundle["content"],
                    received_at=bundle["received_at"],
                    telegram_payload=bundle["payload"],
                    attachments=[unavailable, downloaded],
                    media_group_id="album",
                    source_message_ids=bundle["source_message_ids"],
                ),
            )
            album = Path(temporary) / "inbox" / "2026-08-09_2"
            metadata = json.loads((album / "metadata.json").read_text())
            self.assertEqual(metadata["source_message_ids"], [2, 3])
            self.assertEqual(
                metadata["attachments"][0]["download_status"], "unavailable"
            )
            self.assertTrue((album / "attachments" / "02-video.mp4").exists())


if __name__ == "__main__":
    unittest.main()
