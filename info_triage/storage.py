"""Filesystem content storage and durable SQLite processing state."""

import json
import logging
import shutil
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import (
    CATEGORIES,
    AttachmentSpec,
    CapturedItem,
    DownloadedAttachment,
    ProcessingJob,
    ProcessingResult,
)

logger = logging.getLogger("info_triage")
STATUSES = ("received", "processing", "ready", "failed")


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


def render_message(category: str | None, content: str) -> str:
    if category is None:
        return content
    return f"---\ncategory: {category}\n---\n\n{content}"


def original_content(category: str | None, message: str) -> str:
    if category is None:
        return message
    return message.removeprefix(render_message(category, ""))


class CaptureStore:
    """Store captured content in files and operational state in SQLite."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.staging_dir = data_dir / "staging"
        self.inbox_dir = data_dir / "inbox"
        self.database_path = data_dir / "info-triage.sqlite3"
        self._lock = threading.RLock()

        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self._initialize_database()
        self._migrate_item_directories()
        self._upgrade_metadata()
        self._reconcile_interrupted_items()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

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
                    processing_step TEXT,
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
            if "processing_step" not in columns:
                connection.execute("ALTER TABLE items ADD COLUMN processing_step TEXT")
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
        counts = {status: 0 for status in STATUSES}
        with self._connect() as connection:
            for row in connection.execute(
                "SELECT status, COUNT(*) AS count FROM items GROUP BY status"
            ):
                if row["status"] in counts:
                    counts[row["status"]] = row["count"]
        return counts

    def items_with_status(self, status: str):
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT message_id, status, category, revision, created_at,
                       updated_at, short_text, error, processing_step
                FROM items
                WHERE status = ?
                ORDER BY updated_at DESC
                """,
                (status,),
            ).fetchall()

    def get_item_with_status(self, status: str):
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM items WHERE status = ? LIMIT 1", (status,)
            ).fetchone()

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
        processing_step: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO items (
                    chat_id, message_id, status, category, revision, created_at,
                    updated_at, short_text, error, processing_step
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    status = excluded.status,
                    category = excluded.category,
                    revision = excluded.revision,
                    updated_at = excluded.updated_at,
                    short_text = excluded.short_text,
                    error = excluded.error,
                    processing_step = excluded.processing_step
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
                    processing_step,
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
            shutil.rmtree(temporary_dir)
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
            shutil.rmtree(attachments_dir)
        if manifest:
            temporary_dir.rename(attachments_dir)
        else:
            temporary_dir.rmdir()
        return manifest, warnings

    @staticmethod
    def _read_metadata(item_path: Path) -> dict[str, Any]:
        metadata_path = item_path / "metadata.json"
        if not metadata_path.exists():
            return {}
        return json.loads(metadata_path.read_text(encoding="utf-8"))

    def _path_for_row(self, item) -> Path:
        item_name = self.item_name(item["created_at"], item["message_id"])
        staging_path = self.staging_dir / item_name
        if staging_path.is_dir():
            return staging_path
        return self.inbox_dir / item_name

    def _migrate_item_directories(self) -> None:
        for parent in (self.staging_dir, self.inbox_dir):
            for item_path in list(parent.iterdir()):
                if not item_path.is_dir():
                    continue
                try:
                    metadata = self._read_metadata(item_path)
                    item = self.get_item(
                        int(metadata["chat_id"]), int(metadata["message_id"])
                    )
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
                    item = self.get_item(
                        int(metadata["chat_id"]), int(metadata["message_id"])
                    )
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

    def _reconcile_interrupted_items(self) -> None:
        """Repair only transitions that may have been interrupted by shutdown."""
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT * FROM items").fetchall()
            for item in rows:
                name = self.item_name(item["created_at"], item["message_id"])
                inbox_path = self.inbox_dir / name
                staging_path = self.staging_dir / name
                if inbox_path.is_dir() and item["status"] != "ready":
                    connection.execute(
                        """
                        UPDATE items SET status = 'ready', processing_step = NULL,
                            error = NULL, updated_at = ?
                        WHERE chat_id = ? AND message_id = ?
                        """,
                        (now_iso(), item["chat_id"], item["message_id"]),
                    )
                elif staging_path.is_dir() and item["status"] == "processing":
                    connection.execute(
                        """
                        UPDATE items SET status = 'received', processing_step = NULL,
                            error = NULL, updated_at = ?
                        WHERE chat_id = ? AND message_id = ?
                        """,
                        (now_iso(), item["chat_id"], item["message_id"]),
                    )
                elif (
                    item["status"] in ("received", "processing")
                    and not staging_path.is_dir()
                    and not inbox_path.is_dir()
                ):
                    connection.execute(
                        """
                        UPDATE items SET status = 'failed', processing_step = NULL,
                            error = ?, updated_at = ?
                        WHERE chat_id = ? AND message_id = ?
                        """,
                        (
                            "Item directory is missing",
                            now_iso(),
                            item["chat_id"],
                            item["message_id"],
                        ),
                    )

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
    ) -> CapturedItem:
        """Durably capture or update a message in staging."""
        with self._lock:
            existing = self.get_item(chat_id, message_id)
            already_known = existing is not None
            category = existing["category"] if existing else None
            revision = existing["revision"] if existing else 1
            if existing and (edited_at is not None or force_revision):
                revision += 1
            created_at = (
                existing["created_at"] if existing else received_at or now_iso()
            )
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
            return CapturedItem(
                chat_id,
                message_id,
                revision,
                "received",
                category,
                staging_path,
                already_known,
            )

    def categorize(self, chat_id: int, message_id: int, category: str) -> CapturedItem:
        if category not in CATEGORIES:
            raise ValueError(f"Unknown category: {category}")

        with self._lock:
            item = self.get_item(chat_id, message_id)
            if item is None:
                raise FileNotFoundError("Capture no longer exists")
            current_path = self._path_for_row(item)
            if not current_path.is_dir():
                raise FileNotFoundError("Capture no longer exists")
            if item["category"] == category:
                return CapturedItem(
                    chat_id,
                    message_id,
                    item["revision"],
                    item["status"],
                    category,
                    current_path,
                    True,
                )

            item_name = self.item_name(item["created_at"], message_id)
            staging_path = self.staging_dir / item_name
            inbox_path = self.inbox_dir / item_name
            if inbox_path.is_dir():
                if staging_path.exists():
                    raise RuntimeError(f"Both staging and inbox contain {item_name}")
                inbox_path.rename(staging_path)
            metadata = self._read_metadata(staging_path)
            content = original_content(
                item["category"],
                (staging_path / "message.md").read_text(encoding="utf-8"),
            )
            revision = item["revision"] + 1
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
            return CapturedItem(
                chat_id,
                message_id,
                revision,
                "received",
                category,
                staging_path,
                True,
            )

    def promote_if_current(
        self, item: CapturedItem | ProcessingJob, result: ProcessingResult | None = None
    ) -> bool:
        """Commit a result and promote only if its revision still owns the item."""
        with self._lock, self._connect() as connection:
            current = connection.execute(
                "SELECT * FROM items WHERE chat_id = ? AND message_id = ?",
                (item.chat_id, item.message_id),
            ).fetchone()
            if (
                current is None
                or current["revision"] != item.revision
                or current["status"] not in ("received", "processing")
            ):
                return False

            item_name = self.item_name(current["created_at"], item.message_id)
            staging_path = self.staging_dir / item_name
            inbox_path = self.inbox_dir / item_name
            if not staging_path.is_dir():
                raise FileNotFoundError(f"Staged item is missing: {item_name}")
            if inbox_path.exists():
                raise RuntimeError(f"Inbox already contains {item_name}")
            if result is not None:
                pending_files: list[tuple[Path, Path]] = []
                try:
                    for generated in result.generated_files:
                        relative = generated.relative_path
                        if relative.is_absolute() or ".." in relative.parts:
                            raise ValueError(f"Unsafe generated path: {relative}")
                        destination = staging_path / relative
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        temporary = destination.with_name(f".{destination.name}.tmp")
                        pending_files.append((temporary, destination))
                        shutil.copyfile(generated.source_path, temporary)
                    atomic_write(staging_path / "message.md", result.message_markdown)
                    for temporary, destination in pending_files:
                        temporary.replace(destination)
                except Exception:
                    for temporary, _ in pending_files:
                        temporary.unlink(missing_ok=True)
                    raise

            staging_path.rename(inbox_path)
            connection.execute(
                """
                UPDATE items SET status = 'ready', processing_step = NULL,
                    error = NULL, updated_at = ?
                WHERE chat_id = ? AND message_id = ? AND revision = ?
                """,
                (now_iso(), item.chat_id, item.message_id, item.revision),
            )
            return True

    def claim_next_received(self) -> ProcessingJob | None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            item = connection.execute(
                """
                SELECT * FROM items WHERE status = 'received'
                ORDER BY updated_at, chat_id, message_id LIMIT 1
                """
            ).fetchone()
            if item is None:
                return None
            updated = connection.execute(
                """
                UPDATE items SET status = 'processing', processing_step = NULL,
                    error = NULL, updated_at = ?
                WHERE chat_id = ? AND message_id = ? AND revision = ?
                    AND status = 'received'
                """,
                (
                    now_iso(),
                    item["chat_id"],
                    item["message_id"],
                    item["revision"],
                ),
            )
            if updated.rowcount != 1:
                return None
            return ProcessingJob(
                item["chat_id"],
                item["message_id"],
                item["revision"],
                item["category"],
                self.staging_dir
                / self.item_name(item["created_at"], item["message_id"]),
            )

    def set_processing_step(self, job: ProcessingJob, step: str) -> bool:
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE items SET processing_step = ?, updated_at = ?
                WHERE chat_id = ? AND message_id = ? AND revision = ?
                    AND status = 'processing'
                """,
                (
                    step,
                    now_iso(),
                    job.chat_id,
                    job.message_id,
                    job.revision,
                ),
            )
            return updated.rowcount == 1

    def fail_if_current(
        self, job: ProcessingJob, error: str, processing_step: str | None
    ) -> bool:
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE items SET status = 'failed', error = ?,
                    processing_step = ?, updated_at = ?
                WHERE chat_id = ? AND message_id = ? AND revision = ?
                    AND status = 'processing'
                """,
                (
                    error,
                    processing_step,
                    now_iso(),
                    job.chat_id,
                    job.message_id,
                    job.revision,
                ),
            )
            return updated.rowcount == 1

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
                    received_at = excluded.received_at,
                    edited_at = excluded.edited_at,
                    content = excluded.content,
                    raw_json = excluded.raw_json,
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
        existing_specs: dict[int, list[AttachmentSpec]] = {}
        item = None
        item_path = None
        if existing:
            item = self.get_item(chat_id, existing["item_message_id"])
            if item:
                item_path = self._path_for_row(item)
                payload_path = item_path / "telegram.json"
                if payload_path.exists():
                    payload = json.loads(payload_path.read_text(encoding="utf-8"))
                    for message in payload.get("messages", []):
                        existing_payloads[message["message_id"]] = message
                for value in self._read_metadata(item_path).get("attachments", []):
                    spec = AttachmentSpec.from_dict(value)
                    existing_specs.setdefault(spec.source_message_id, []).append(spec)

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
                specs.extend(existing_specs.get(message_id, []))
        contents = [
            staged[message_id]["content"]
            for message_id in ordered_ids
            if message_id in staged
        ]
        if existing_payloads and item is not None and item_path is not None:
            contents = [
                original_content(
                    item["category"],
                    (item_path / "message.md").read_text(encoding="utf-8"),
                )
            ]
            for row in staged.values():
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
            "payload": {"messages": [payloads[value] for value in ordered_ids]},
            "specs": specs,
            "force_revision": existing is not None,
        }

    def clear_media_group(self, chat_id: int, media_group_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM pending_media_group_members
                WHERE chat_id = ? AND media_group_id = ?
                """,
                (chat_id, media_group_id),
            )
            connection.execute(
                """
                DELETE FROM pending_media_groups
                WHERE chat_id = ? AND media_group_id = ?
                """,
                (chat_id, media_group_id),
            )
