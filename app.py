"""Minimal Telegram-to-Markdown capture service."""

import asyncio
import html
import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

CATEGORIES = ("ML", "Career", "Life", "Other")
BASE_DIR = Path(__file__).resolve().parent
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
MEDIA_GROUP_SETTLE_SECONDS = 1.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("info_triage")
# Telegram's request URL contains the bot token. Keep transport logging quiet even
# when the application itself is running at INFO level.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


@dataclass(frozen=True)
class AttachmentSpec:
    kind: str
    file_id: str
    file_unique_id: str
    file_size: int | None
    mime_type: str | None
    original_name: str | None
    extension: str
    source_message_id: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "file_id": self.file_id,
            "file_unique_id": self.file_unique_id,
            "file_size": self.file_size,
            "mime_type": self.mime_type,
            "original_name": self.original_name,
            "extension": self.extension,
            "source_message_id": self.source_message_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AttachmentSpec":
        return cls(
            kind=value["kind"],
            file_id=value["file_id"],
            file_unique_id=value["file_unique_id"],
            file_size=value.get("file_size"),
            mime_type=value.get("mime_type"),
            original_name=value.get("original_name"),
            extension=value["extension"],
            source_message_id=value["source_message_id"],
        )


@dataclass
class DownloadedAttachment:
    spec: AttachmentSpec
    data: bytes | None
    warning: str | None = None


def _extension_for(kind: str, mime_type: str | None, original_name: str | None) -> str:
    suffix = Path(original_name or "").suffix.lower()
    if suffix and len(suffix) <= 12 and suffix[1:].isalnum():
        return suffix
    if mime_type:
        extensions = {
            "audio/mpeg": ".mp3",
            "audio/ogg": ".ogg",
            "audio/opus": ".ogg",
            "video/mp4": ".mp4",
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
        }
        if mime_type in extensions:
            return extensions[mime_type]
    return {
        "photo": ".jpg",
        "video": ".mp4",
        "animation": ".mp4",
        "voice": ".ogg",
        "video_note": ".mp4",
        "audio": ".mp3",
    }.get(kind, "")


def attachment_specs(message: Message) -> list[AttachmentSpec]:
    """Return the useful downloadable media in one Telegram message."""
    candidates: list[tuple[str, Any]] = []
    if message.photo:
        candidates.append(("photo", message.photo[-1]))
    for kind in ("document", "video", "animation", "audio", "voice", "video_note"):
        value = getattr(message, kind)
        if value is not None:
            candidates.append((kind, value))

    specs = []
    for kind, value in candidates:
        specs.append(
            AttachmentSpec(
                kind=kind,
                file_id=value.file_id,
                file_unique_id=value.file_unique_id,
                file_size=value.file_size,
                mime_type=getattr(value, "mime_type", None),
                original_name=getattr(value, "file_name", None),
                extension=_extension_for(
                    kind,
                    getattr(value, "mime_type", None),
                    getattr(value, "file_name", None),
                ),
                source_message_id=message.message_id,
            )
        )
    return specs


def attachment_specs_from_payload(payload: dict[str, Any]) -> list[AttachmentSpec]:
    """Rebuild attachment specs from a stored Telegram message payload."""
    message_id = payload["message_id"]
    candidates: list[tuple[str, dict[str, Any]]] = []
    if payload.get("photo"):
        candidates.append(("photo", payload["photo"][-1]))
    for kind in ("document", "video", "animation", "audio", "voice", "video_note"):
        if payload.get(kind):
            candidates.append((kind, payload[kind]))
    return [
        AttachmentSpec(
            kind=kind,
            file_id=value["file_id"],
            file_unique_id=value["file_unique_id"],
            file_size=value.get("file_size"),
            mime_type=value.get("mime_type"),
            original_name=value.get("file_name"),
            extension=_extension_for(
                kind, value.get("mime_type"), value.get("file_name")
            ),
            source_message_id=message_id,
        )
        for kind, value in candidates
    ]


def location_markdown(message: Message) -> str:
    location = message.location or (message.venue.location if message.venue else None)
    if location is None:
        return ""
    if message.venue:
        heading = message.venue.title
        details = [message.venue.address]
    else:
        heading = "Location"
        details = []
    coordinates = f"{location.latitude},{location.longitude}"
    details.extend(
        [
            f"Coordinates: {coordinates}",
            f"[Open in maps](https://www.google.com/maps/search/?api=1&query={coordinates})",
        ]
    )
    return "\n".join([f"## {heading}", *details])


def capture_content(message: Message) -> str | None:
    """Return useful Markdown content, or None for deliberately ignored messages."""
    text = message.text if message.text is not None else message.caption
    location = location_markdown(message)
    if text is not None or location or attachment_specs(message):
        return "\n\n".join(part for part in (text or "", location) if part)
    return None


def render_message(category: str | None, content: str) -> str:
    if category is None:
        return content
    return f"---\ncategory: {category}\n---\n\n{content}"


def original_content(category: str | None, message: str) -> str:
    if category is None:
        return message
    prefix = render_message(category, "")
    return message.removeprefix(prefix)


class CaptureStore:
    """Store captured content in files and operational state in SQLite."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.staging_dir = data_dir / "staging"
        self.inbox_dir = data_dir / "inbox"
        self.database_path = data_dir / "info-triage.sqlite3"

        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self._initialize_database()
        self._migrate_item_directories()
        self._upgrade_metadata()
        self._recover_staging_items()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    category TEXT,
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    short_text TEXT,
                    error TEXT,
                    PRIMARY KEY (chat_id, message_id)
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(items)").fetchall()
            }
            if "revision" not in columns:
                connection.execute(
                    "ALTER TABLE items ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS item_messages (
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    item_message_id INTEGER NOT NULL,
                    media_group_id TEXT,
                    PRIMARY KEY (chat_id, message_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_media_group_members (
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
                CREATE TABLE IF NOT EXISTS pending_media_groups (
                    chat_id INTEGER NOT NULL,
                    media_group_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, media_group_id)
                )
                """
            )

    @staticmethod
    def item_name(created_at: str, message_id: int) -> str:
        created_date = datetime.fromisoformat(created_at).date().isoformat()
        return f"{created_date}_{message_id}"

    def get_item(self, chat_id: int, message_id: int):
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM items WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id),
            ).fetchone()

    def item_for_source_message(self, chat_id: int, message_id: int):
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT items.* FROM item_messages
                JOIN items ON items.chat_id = item_messages.chat_id
                    AND items.message_id = item_messages.item_message_id
                WHERE item_messages.chat_id = ? AND item_messages.message_id = ?
                """,
                (chat_id, message_id),
            ).fetchone()

    def status_counts(self) -> dict[str, int]:
        counts = {status: 0 for status in ("received", "processing", "ready", "failed")}
        with self._connect() as connection:
            for row in connection.execute(
                "SELECT status, COUNT(*) AS count FROM items GROUP BY status"
            ):
                counts[row["status"]] = row["count"]
        return counts

    def items_with_status(self, status: str):
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT message_id, status, category, revision, created_at,
                       updated_at, short_text, error
                FROM items
                WHERE status = ?
                ORDER BY updated_at DESC
                """,
                (status,),
            ).fetchall()

    def _save_state(
        self,
        chat_id: int,
        message_id: int,
        status: str,
        category: str | None,
        revision: int,
        created_at: str,
        content: str,
        error: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO items (
                    chat_id, message_id, status, category, revision, created_at,
                    updated_at, short_text, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    status = excluded.status,
                    category = excluded.category,
                    revision = excluded.revision,
                    updated_at = excluded.updated_at,
                    short_text = excluded.short_text,
                    error = excluded.error
                """,
                (
                    chat_id,
                    message_id,
                    status,
                    category,
                    revision,
                    created_at,
                    now_iso(),
                    " ".join(content.split())[:160],
                    error,
                ),
            )

    def _write_item(
        self,
        item_path: Path,
        chat_id: int,
        message_id: int,
        content: str,
        category: str | None,
        revision: int,
        received_at: str,
        edited_at: str | None,
        telegram_payload: dict[str, Any] | list[dict[str, Any]] | None = None,
        attachments: list[DownloadedAttachment] | None = None,
        media_group_id: str | None = None,
        source_message_ids: list[int] | None = None,
    ) -> None:
        atomic_write(item_path / "message.md", render_message(category, content))
        existing_metadata = self._read_metadata(item_path)
        attachment_manifest = existing_metadata.get("attachments", [])
        warnings = existing_metadata.get("download_warnings", [])
        if attachments is not None:
            attachment_manifest, warnings = self._write_attachments(
                item_path, attachments
            )
        if telegram_payload is not None:
            atomic_write(
                item_path / "telegram.json",
                json.dumps(telegram_payload, ensure_ascii=False, indent=2) + "\n",
            )
        metadata = {
            "chat_id": chat_id,
            "message_id": message_id,
            "received_at": received_at,
            "edited_at": edited_at,
            "category": category,
            "revision": revision,
            "media_group_id": media_group_id
            if media_group_id is not None
            else existing_metadata.get("media_group_id"),
            "source_message_ids": source_message_ids
            if source_message_ids is not None
            else existing_metadata.get("source_message_ids", [message_id]),
            "attachments": attachment_manifest,
            "download_warnings": warnings,
        }
        atomic_write(
            item_path / "metadata.json",
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        )

    def _write_attachments(
        self, item_path: Path, attachments: list[DownloadedAttachment]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        attachments_dir = item_path / "attachments"
        temporary_dir = item_path / ".attachments.tmp"
        if temporary_dir.exists():
            for path in temporary_dir.iterdir():
                path.unlink()
            temporary_dir.rmdir()
        temporary_dir.mkdir()
        manifest = []
        warnings = []
        for index, attachment in enumerate(attachments, start=1):
            record = attachment.spec.to_dict()
            record["path"] = None
            if attachment.data is None:
                record["download_status"] = "unavailable"
                record["warning"] = attachment.warning
                if attachment.warning:
                    warnings.append(attachment.warning)
            else:
                filename = (
                    f"{index:02d}-{attachment.spec.kind}{attachment.spec.extension}"
                )
                atomic_write_bytes(temporary_dir / filename, attachment.data)
                record["path"] = f"attachments/{filename}"
                record["download_status"] = "downloaded"
            manifest.append(record)
        if attachments_dir.exists():
            for path in attachments_dir.iterdir():
                path.unlink()
            attachments_dir.rmdir()
        if manifest:
            temporary_dir.rename(attachments_dir)
        else:
            temporary_dir.rmdir()
        return manifest, warnings

    def _read_metadata(self, item_path: Path) -> dict:
        metadata_path = item_path / "metadata.json"
        if not metadata_path.exists():
            return {}
        return json.loads(metadata_path.read_text(encoding="utf-8"))

    def _migrate_item_directories(self) -> None:
        for parent in (self.staging_dir, self.inbox_dir):
            for item_path in list(parent.iterdir()):
                if not item_path.is_dir():
                    continue
                try:
                    metadata = self._read_metadata(item_path)
                    chat_id = int(metadata["chat_id"])
                    message_id = int(metadata["message_id"])
                    item = self.get_item(chat_id, message_id)
                    if item is None:
                        continue
                    new_path = parent / self.item_name(
                        item["created_at"], item["message_id"]
                    )
                    if new_path == item_path:
                        continue
                    if new_path.exists():
                        raise RuntimeError(f"Item directory already exists: {new_path}")
                    item_path.rename(new_path)
                    logger.info("Renamed item %s to %s", item_path.name, new_path.name)
                except Exception:
                    logger.exception("Could not migrate item directory %s", item_path)

    def _upgrade_metadata(self) -> None:
        for parent in (self.staging_dir, self.inbox_dir):
            for item_path in parent.iterdir():
                if not item_path.is_dir():
                    continue
                try:
                    metadata = self._read_metadata(item_path)
                    chat_id = int(metadata["chat_id"])
                    message_id = int(metadata["message_id"])
                    item = self.get_item(chat_id, message_id)
                    if item is None:
                        continue
                    metadata["category"] = item["category"]
                    metadata["revision"] = item["revision"]
                    atomic_write(
                        item_path / "metadata.json",
                        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    logger.warning("Could not upgrade metadata for %s", item_path)

    def _recover_staging_items(self) -> None:
        for item_path in list(self.staging_dir.iterdir()):
            if not item_path.is_dir():
                continue
            try:
                metadata = self._read_metadata(item_path)
                chat_id = int(metadata["chat_id"])
                message_id = int(metadata["message_id"])
                item = self.get_item(chat_id, message_id)
                if item is None:
                    continue
                content = original_content(
                    item["category"],
                    (item_path / "message.md").read_text(encoding="utf-8"),
                )
                self._promote(
                    chat_id,
                    message_id,
                    item["category"],
                    item["revision"],
                    item["created_at"],
                    content,
                )
                logger.info("Recovered staged item %s", item_path.name)
            except Exception:
                logger.exception("Could not recover staged item %s", item_path)

    def capture(
        self,
        chat_id: int,
        message_id: int,
        content: str,
        edited_at: str | None = None,
        received_at: str | None = None,
        telegram_payload: dict[str, Any] | list[dict[str, Any]] | None = None,
        attachments: list[DownloadedAttachment] | None = None,
        media_group_id: str | None = None,
        source_message_ids: list[int] | None = None,
        force_revision: bool = False,
    ) -> tuple[bool, str | None]:
        """Capture or update a message. Return (already_known, category)."""
        existing = self.get_item(chat_id, message_id)
        already_known = existing is not None
        category = existing["category"] if existing else None
        revision = existing["revision"] if existing else 1
        if existing and (edited_at is not None or force_revision):
            revision += 1
        created_at = existing["created_at"] if existing else received_at or now_iso()
        item_name = self.item_name(created_at, message_id)
        staging_path = self.staging_dir / item_name
        inbox_path = self.inbox_dir / item_name

        current_metadata = {}
        if inbox_path.exists():
            current_metadata = self._read_metadata(inbox_path)
            if staging_path.exists():
                raise RuntimeError(f"Both staging and inbox contain {item_name}")
            inbox_path.rename(staging_path)
        else:
            staging_path.mkdir(exist_ok=True)
            current_metadata = self._read_metadata(staging_path)

        if edited_at is None:
            edited_at = current_metadata.get("edited_at")
        self._write_item(
            staging_path,
            chat_id,
            message_id,
            content,
            category,
            revision,
            created_at,
            edited_at,
            telegram_payload,
            attachments,
            media_group_id,
            source_message_ids,
        )
        self._save_state(
            chat_id,
            message_id,
            "received",
            category,
            revision,
            created_at,
            content,
        )
        self._promote(chat_id, message_id, category, revision, created_at, content)

        with self._connect() as connection:
            for source_message_id in source_message_ids or [message_id]:
                connection.execute(
                    """
                    INSERT INTO item_messages (
                        chat_id, message_id, item_message_id, media_group_id
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(chat_id, message_id) DO UPDATE SET
                        item_message_id = excluded.item_message_id,
                        media_group_id = excluded.media_group_id
                    """,
                    (chat_id, source_message_id, message_id, media_group_id),
                )

        return already_known, category

    def stage_media_group_member(
        self,
        chat_id: int,
        media_group_id: str,
        message_id: int,
        content: str,
        received_at: str,
        edited_at: str | None,
        raw_payload: dict[str, Any],
        specs: list[AttachmentSpec],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO pending_media_group_members (
                    chat_id, media_group_id, message_id, received_at, edited_at,
                    content, raw_json, attachments_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, media_group_id, message_id) DO UPDATE SET
                    received_at = excluded.received_at, edited_at = excluded.edited_at,
                    content = excluded.content, raw_json = excluded.raw_json,
                    attachments_json = excluded.attachments_json
                """,
                (
                    chat_id,
                    media_group_id,
                    message_id,
                    received_at,
                    edited_at,
                    content,
                    json.dumps(raw_payload, ensure_ascii=False),
                    json.dumps([spec.to_dict() for spec in specs], ensure_ascii=False),
                ),
            )
            connection.execute(
                """
                INSERT INTO pending_media_groups (chat_id, media_group_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id, media_group_id) DO UPDATE SET
                    updated_at = excluded.updated_at
                """,
                (chat_id, media_group_id, now_iso()),
            )

    def pending_media_groups(self) -> list[tuple[int, str]]:
        with self._connect() as connection:
            return [
                (row["chat_id"], row["media_group_id"])
                for row in connection.execute(
                    "SELECT chat_id, media_group_id FROM pending_media_groups"
                )
            ]

    def media_group_updated_at(self, chat_id: int, media_group_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT updated_at FROM pending_media_groups
                WHERE chat_id = ? AND media_group_id = ?
                """,
                (chat_id, media_group_id),
            ).fetchone()
        return row["updated_at"] if row else None

    def media_group_capture(
        self, chat_id: int, media_group_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM pending_media_group_members
                WHERE chat_id = ? AND media_group_id = ? ORDER BY message_id
                """,
                (chat_id, media_group_id),
            ).fetchall()
            if not rows:
                return None
            existing = connection.execute(
                """
                SELECT item_message_id FROM item_messages
                WHERE chat_id = ? AND media_group_id = ? LIMIT 1
                """,
                (chat_id, media_group_id),
            ).fetchone()

        existing_payloads: dict[int, dict[str, Any]] = {}
        if existing:
            item = self.get_item(chat_id, existing["item_message_id"])
            if item:
                item_path = self.inbox_dir / self.item_name(
                    item["created_at"], item["message_id"]
                )
                payload_path = item_path / "telegram.json"
                if payload_path.exists():
                    payload = json.loads(payload_path.read_text(encoding="utf-8"))
                    for message in payload.get("messages", []):
                        existing_payloads[message["message_id"]] = message

        staged = {row["message_id"]: row for row in rows}
        payloads = dict(existing_payloads)
        payloads.update(
            {
                message_id: json.loads(row["raw_json"])
                for message_id, row in staged.items()
            }
        )
        ordered_ids = sorted(payloads)
        if not ordered_ids:
            return None
        specs = []
        for message_id in ordered_ids:
            if message_id in staged:
                specs.extend(
                    AttachmentSpec.from_dict(value)
                    for value in json.loads(staged[message_id]["attachments_json"])
                )
            else:
                specs.extend(attachment_specs_from_payload(payloads[message_id]))
        contents = [
            staged[message_id]["content"]
            for message_id in ordered_ids
            if message_id in staged
        ]
        if existing_payloads:
            item = self.get_item(chat_id, existing["item_message_id"])
            item_path = self.inbox_dir / self.item_name(
                item["created_at"], item["message_id"]
            )
            contents = [
                original_content(
                    item["category"],
                    (item_path / "message.md").read_text(encoding="utf-8"),
                )
            ]
            for message_id, row in staged.items():
                if row["content"]:
                    contents = [row["content"]]
                    break
        return {
            "message_id": ordered_ids[0],
            "source_message_ids": ordered_ids,
            "received_at": min((row["received_at"] for row in rows), default=now_iso()),
            "edited_at": max(
                (row["edited_at"] for row in rows if row["edited_at"]), default=None
            ),
            "content": next((content for content in contents if content), ""),
            "payload": {
                "messages": [payloads[message_id] for message_id in ordered_ids]
            },
            "specs": specs,
            "force_revision": existing is not None,
        }

    def clear_media_group(self, chat_id: int, media_group_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM pending_media_group_members WHERE chat_id = ? AND media_group_id = ?",
                (chat_id, media_group_id),
            )
            connection.execute(
                "DELETE FROM pending_media_groups WHERE chat_id = ? AND media_group_id = ?",
                (chat_id, media_group_id),
            )

    def categorize(self, chat_id: int, message_id: int, category: str) -> None:
        if category not in CATEGORIES:
            raise ValueError(f"Unknown category: {category}")

        item = self.get_item(chat_id, message_id)
        if item is None:
            raise FileNotFoundError("Capture no longer exists")

        item_name = self.item_name(item["created_at"], message_id)
        staging_path = self.staging_dir / item_name
        inbox_path = self.inbox_dir / item_name
        if inbox_path.is_dir():
            if staging_path.exists():
                raise RuntimeError(f"Both staging and inbox contain {item_name}")
            inbox_path.rename(staging_path)
        elif not staging_path.is_dir():
            raise FileNotFoundError("Capture no longer exists")

        metadata = self._read_metadata(staging_path)
        content = original_content(
            item["category"],
            (staging_path / "message.md").read_text(encoding="utf-8"),
        )
        revision = item["revision"]
        if item["category"] != category:
            revision += 1
        self._write_item(
            staging_path,
            chat_id,
            message_id,
            content,
            category,
            revision,
            item["created_at"],
            metadata.get("edited_at"),
        )
        self._save_state(
            chat_id,
            message_id,
            "received",
            category,
            revision,
            item["created_at"],
            content,
        )
        self._promote(
            chat_id, message_id, category, revision, item["created_at"], content
        )

    def _promote(
        self,
        chat_id: int,
        message_id: int,
        category: str | None,
        revision: int,
        created_at: str,
        content: str,
    ) -> None:
        item_name = self.item_name(created_at, message_id)
        staging_path = self.staging_dir / item_name
        inbox_path = self.inbox_dir / item_name

        try:
            self._save_state(
                chat_id,
                message_id,
                "processing",
                category,
                revision,
                created_at,
                content,
            )
            if inbox_path.exists():
                raise RuntimeError(f"Inbox already contains {item_name}")
            staging_path.rename(inbox_path)
            self._save_state(
                chat_id,
                message_id,
                "ready",
                category,
                revision,
                created_at,
                content,
            )
        except Exception as error:
            self._save_state(
                chat_id,
                message_id,
                "failed",
                category,
                revision,
                created_at,
                content,
                str(error),
            )
            raise


class WebHandler(BaseHTTPRequestHandler):
    store: CaptureStore

    def do_GET(self):
        request = urlsplit(self.path)
        if request.path == "/health":
            self._send_text("Info Triage is running\n")
            return
        if request.path != "/":
            self.send_error(404)
            return

        status = parse_qs(request.query).get("status", ["ready"])[0]
        if status not in ("received", "processing", "ready", "failed"):
            status = "ready"
        body = self._dashboard(status).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, content: str) -> None:
        body = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dashboard(self, selected_status: str) -> str:
        counts = self.store.status_counts()
        tabs = "".join(
            (
                f'<a class="tab{" active" if status == selected_status else ""}" '
                f'href="/?status={status}">{status.title()} {counts[status]}</a>'
            )
            for status in ("received", "processing", "ready", "failed")
        )

        rows = []
        for item in self.store.items_with_status(selected_status):
            item_id = self.store.item_name(item["created_at"], item["message_id"])
            message = html.escape(item["short_text"] or "")
            if item["error"]:
                message += f'<div class="error">{html.escape(item["error"])}</div>'
            rows.append(
                "<tr>"
                f"<td>{html.escape(item_id)}</td>"
                f"<td>{html.escape(self._display_time(item['created_at']))}</td>"
                f"<td>{html.escape(self._display_time(item['updated_at']))}</td>"
                f"<td>{html.escape(item['category'] or '—')}</td>"
                f"<td>{item['revision']}</td>"
                f"<td>{message}</td>"
                "</tr>"
            )
        if not rows:
            rows.append('<tr><td colspan="6" class="empty">No items</td></tr>')

        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="10">
  <title>Info Triage</title>
  <style>
    body {{ font: 15px system-ui, sans-serif; margin: 2rem; color: #202124; }}
    h1 {{ margin: 0 0 1.5rem; }}
    .tabs {{ display: flex; gap: .5rem; margin-bottom: 1.25rem; flex-wrap: wrap; }}
    .tab {{ padding: .55rem .8rem; border: 1px solid #ccc; border-radius: .4rem;
            color: inherit; text-decoration: none; }}
    .tab.active {{ background: #202124; color: white; border-color: #202124; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ padding: .65rem; border-bottom: 1px solid #ddd; text-align: left;
              vertical-align: top; }}
    th {{ white-space: nowrap; }}
    .error {{ color: #b00020; margin-top: .25rem; }}
    .empty {{ color: #777; text-align: center; padding: 2rem; }}
  </style>
</head>
<body>
  <h1>Info Triage</h1>
  <nav class="tabs">{tabs}</nav>
  <table>
    <thead><tr><th>ID</th><th>Created</th><th>Updated</th><th>Category</th><th>Rev</th><th>Message</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>
</body>
</html>
"""

    @staticmethod
    def _display_time(timestamp: str) -> str:
        return timestamp.replace("T", " ")[:16]

    def log_message(self, format, *args):
        return


def category_keyboard(message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    category,
                    callback_data=f"label|{message_id}|{category}",
                )
            ]
            for category in CATEGORIES
        ]
    )


def is_authorized(update: Update, allowed_user_id: int) -> bool:
    user = update.effective_user
    authorized = user is not None and user.id == allowed_user_id
    if not authorized:
        logger.info(
            "Ignored update from unauthorized user %s",
            user.id if user else "unknown",
        )
    return authorized


async def download_attachments(
    bot, specs: list[AttachmentSpec]
) -> list[DownloadedAttachment]:
    attachments = []
    for spec in specs:
        if spec.file_size is not None and spec.file_size > MAX_DOWNLOAD_BYTES:
            attachments.append(
                DownloadedAttachment(
                    spec,
                    None,
                    f"{spec.kind} is {spec.file_size} bytes and exceeds Telegram's 20 MB download limit.",
                )
            )
            continue
        for attempt in range(3):
            try:
                telegram_file = await bot.get_file(spec.file_id)
                attachments.append(
                    DownloadedAttachment(
                        spec, bytes(await telegram_file.download_as_bytearray())
                    )
                )
                break
            except TelegramError as error:
                if attempt == 2:
                    logger.warning(
                        "Could not download Telegram %s: %s", spec.kind, error
                    )
                    attachments.append(
                        DownloadedAttachment(
                            spec, None, f"Could not download {spec.kind}: {error}"
                        )
                    )
                else:
                    await asyncio.sleep(2**attempt)
    return attachments


def schedule_media_group_finalization(
    application: Application, chat_id: int, media_group_id: str
) -> None:
    tasks: dict[tuple[int, str], asyncio.Task] = application.bot_data.setdefault(
        "media_group_tasks", {}
    )
    key = (chat_id, media_group_id)
    task = tasks.get(key)
    if task and not task.done():
        task.cancel()
    tasks[key] = application.create_task(
        finalize_media_group(application, chat_id, media_group_id),
        name=f"media-group-{chat_id}-{media_group_id}",
    )


async def finalize_media_group(
    application: Application, chat_id: int, media_group_id: str
) -> None:
    try:
        await asyncio.sleep(MEDIA_GROUP_SETTLE_SECONDS)
        store: CaptureStore = application.bot_data["store"]
        bundle = store.media_group_capture(chat_id, media_group_id)
        if bundle is None:
            return
        attachments = await download_attachments(application.bot, bundle["specs"])
        already_known, category = store.capture(
            chat_id,
            bundle["message_id"],
            bundle["content"],
            edited_at=bundle["edited_at"],
            received_at=bundle["received_at"],
            telegram_payload=bundle["payload"],
            attachments=attachments,
            media_group_id=media_group_id,
            source_message_ids=bundle["source_message_ids"],
            force_revision=bundle["force_revision"],
        )
        store.clear_media_group(chat_id, media_group_id)
        if category or already_known:
            return
        warnings = [
            attachment.warning for attachment in attachments if attachment.warning
        ]
        text = "Saved."
        if warnings:
            text += " Some attachments could not be downloaded; details are in metadata.json."
        await application.bot.send_message(
            chat_id,
            f"{text} Optional label:",
            reply_markup=category_keyboard(bundle["message_id"]),
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Failed to capture Telegram media group %s", media_group_id)


async def recover_pending_media_groups(application: Application) -> None:
    store: CaptureStore = application.bot_data["store"]
    for chat_id, media_group_id in store.pending_media_groups():
        schedule_media_group_finalization(application, chat_id, media_group_id)


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    is_edit: bool,
) -> None:
    store: CaptureStore = context.application.bot_data["store"]
    allowed_user_id: int = context.application.bot_data["allowed_user_id"]
    if not is_authorized(update, allowed_user_id):
        return

    message = update.effective_message
    if message is None:
        return

    content = capture_content(message)
    if content is None:
        return

    edited_at = None
    if is_edit:
        edit_date = message.edit_date or datetime.now(UTC)
        edited_at = edit_date.isoformat()

    raw_payload = json.loads(message.to_json())
    specs = attachment_specs(message)
    if message.media_group_id:
        store.stage_media_group_member(
            message.chat_id,
            message.media_group_id,
            message.message_id,
            content,
            message.date.isoformat(),
            edited_at,
            raw_payload,
            specs,
        )
        schedule_media_group_finalization(
            context.application, message.chat_id, message.media_group_id
        )
        return

    try:
        attachments = await download_attachments(context.application.bot, specs)
        already_known, category = store.capture(
            message.chat_id,
            message.message_id,
            content,
            edited_at=edited_at,
            received_at=message.date.isoformat(),
            telegram_payload=raw_payload,
            attachments=attachments,
        )
    except Exception:
        logger.exception("Failed to capture Telegram message")
        if not is_edit:
            await message.reply_text("Could not save this item. Check the server log.")
        return

    if category:
        logger.info(
            "Updated ready Telegram message %s",
            message.message_id,
        )
        return

    if not already_known:
        warnings = [
            attachment.warning for attachment in attachments if attachment.warning
        ]
        prefix = "Saved."
        if warnings:
            prefix += " Some attachments could not be downloaded; details are in metadata.json."
        await message.reply_text(
            f"{prefix} Optional label:",
            reply_markup=category_keyboard(message.message_id),
        )


async def handle_new_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await handle_message(update, context, is_edit=False)


async def handle_edited_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await handle_message(update, context, is_edit=True)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    allowed_user_id: int = context.application.bot_data["allowed_user_id"]
    if not is_authorized(update, allowed_user_id):
        await query.answer()
        return

    try:
        _, message_id_text, category = (query.data or "").split("|", 2)
        message_id = int(message_id_text)
        if category not in CATEGORIES:
            raise ValueError
        if query.message is None:
            raise ValueError
        chat_id = query.message.chat.id
    except ValueError:
        await query.answer("Invalid category button", show_alert=True)
        return

    store: CaptureStore = context.application.bot_data["store"]
    try:
        store.categorize(chat_id, message_id, category)
    except FileNotFoundError:
        await query.answer()
        await query.edit_message_text(
            "This capture no longer exists.", reply_markup=None
        )
        return
    except Exception:
        logger.exception("Failed to categorize Telegram message")
        await query.answer("Could not save this item", show_alert=True)
        return

    await query.answer()
    await query.edit_message_text(f"✅ Updated as: {category}", reply_markup=None)


def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    allowed_user_id = int(os.environ["ALLOWED_USER_ID"])
    data_dir = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
    port = int(os.environ.get("PORT", "8000"))

    store = CaptureStore(data_dir)
    WebHandler.store = store
    health_server = ThreadingHTTPServer(("0.0.0.0", port), WebHandler)
    health_thread = threading.Thread(target=health_server.serve_forever, daemon=True)
    health_thread.start()
    logger.info("Health server listening on port %s", port)

    asyncio.set_event_loop(asyncio.new_event_loop())
    application = (
        Application.builder()
        .token(bot_token)
        .post_init(recover_pending_media_groups)
        .build()
    )
    application.bot_data["store"] = store
    application.bot_data["allowed_user_id"] = allowed_user_id

    application.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & ~filters.COMMAND,
            handle_new_message,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.UpdateType.EDITED_MESSAGE & ~filters.COMMAND,
            handle_edited_message,
        )
    )
    application.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^label\|"))

    logger.info("Telegram bot started, polling...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        health_server.shutdown()
        health_server.server_close()


if __name__ == "__main__":
    main()
