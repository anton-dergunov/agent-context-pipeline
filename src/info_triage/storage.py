"""Filesystem content storage and durable SQLite processing state."""

import json
import logging
import shutil
import sqlite3
import threading
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import (
    AttachmentSpec,
    CapturedItem,
    DownloadedAttachment,
    ProcessingIssue,
    ProcessingJob,
    ProcessingResult,
)

logger = logging.getLogger("info_triage")
STATUSES = ("received", "processing", "ready", "failed")
# Provenance lives under this directory; metadata.json stays at the item root.
CAPTURE_DIR = "capture"
# The received messages in the transport's own shape. Named for what it holds
# rather than for Telegram, because an HTTP capture writes one too.
PAYLOAD_NAME = "payload.json"
# Paths a processing step may never generate: they belong to the capture layer.
RESERVED_GENERATED_PATHS = (CAPTURE_DIR, "metadata.json")
# Chat id recorded for captures that did not arrive over Telegram. Telegram's
# private-chat ids are the user's own id and always positive, so this cannot
# collide with a real one.
HTTP_CHAT_ID = 0


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


class CaptureStore:
    """Store captured content in files and operational state in SQLite."""

    def __init__(self, data_dir: Path, routes: Iterable[str]):
        self.data_dir = data_dir
        self.routes = tuple(routes)
        self.staging_dir = data_dir / "staging"
        self.inbox_dir = data_dir / "inbox"
        self.database_path = data_dir / "info-triage.sqlite3"
        self._lock = threading.RLock()

        for route in self.routes:
            (self.staging_dir / route).mkdir(parents=True, exist_ok=True)
            (self.inbox_dir / route).mkdir(parents=True, exist_ok=True)
        self._initialize_database()
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
            # CREATE TABLE IF NOT EXISTS is a silent no-op against an older
            # database, and deploy.sh preserves data/ across deployments. This
            # project keeps no migration path, so say so rather than run on a
            # schema that cannot hold a route.
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(items)")}
            if columns and "origin_route" not in columns:
                raise RuntimeError(
                    f"{self.database_path} predates routes and cannot be migrated. "
                    "Stop the daemon and delete its data directory."
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    -- Which bot owns the source message. Fixed for the item's
                    -- lifetime: a re-routed item's edits still arrive here.
                    origin_route TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    -- Where the item is filed. A #hashtag edit moves it.
                    route TEXT NOT NULL,
                    -- The <n> in the item directory name, allocated per route.
                    local_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    short_text TEXT,
                    error TEXT,
                    processing_step TEXT,
                    problems TEXT,
                    PRIMARY KEY (origin_route, chat_id, message_id)
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS items_route_local_id
                ON items (route, local_id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS item_messages (
                    origin_route TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    item_message_id INTEGER NOT NULL,
                    media_group_id TEXT,
                    PRIMARY KEY (origin_route, chat_id, message_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_capture_messages (
                    origin_route TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    media_group_id TEXT,
                    received_at TEXT NOT NULL,
                    edited_at TEXT,
                    content TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    attachments_json TEXT NOT NULL,
                    PRIMARY KEY (origin_route, chat_id, message_id)
                )
                """
            )
            connection.execute(
                """
                -- Serializes the per-route item counter for every transport.
                CREATE TABLE IF NOT EXISTS route_sequences (
                    route TEXT PRIMARY KEY,
                    next_local_id INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS processor_stats (
                    processor TEXT PRIMARY KEY,
                    runs INTEGER NOT NULL DEFAULT 0,
                    succeeded INTEGER NOT NULL DEFAULT 0,
                    partial INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS processor_reason_stats (
                    processor TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    occurrences INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (processor, outcome, reason),
                    FOREIGN KEY (processor) REFERENCES processor_stats(processor)
                )
                """
            )
    @staticmethod
    def item_name(created_at: str, local_id: int) -> str:
        created_date = datetime.fromisoformat(created_at).date().isoformat()
        return f"{created_date}_{local_id}"

    def staging_for(self, route: str, created_at: str, local_id: int) -> Path:
        return self.staging_dir / route / self.item_name(created_at, local_id)

    def inbox_for(self, route: str, created_at: str, local_id: int) -> Path:
        return self.inbox_dir / route / self.item_name(created_at, local_id)

    @property
    def lock(self) -> threading.RLock:
        """Held by a caller that must allocate a number and then use it."""
        return self._lock

    def allocate_local_id(self, route: str) -> int:
        """Claim the next item number for a route ahead of capturing into it.

        An HTTP capture needs its number before it can build a payload, because
        the number is also the synthesized message id.
        """
        if route not in self.routes:
            raise ValueError(f"Unknown route: {route}")
        with self._lock, self._connect() as connection:
            return self._allocate_local_id(connection, route)

    def _allocate_local_id(self, connection: sqlite3.Connection, route: str) -> int:
        """Claim the next item number for a route, for whichever transport asked.

        One counter for every bot and for HTTP: the directory name has to be
        unique within a route, and a Telegram message id is neither unique across
        bots nor available to an HTTP capture.
        """
        connection.execute(
            """
            INSERT INTO route_sequences (route, next_local_id) VALUES (?, 1)
            ON CONFLICT(route) DO UPDATE SET next_local_id = next_local_id + 1
            """,
            (route,),
        )
        return connection.execute(
            "SELECT next_local_id FROM route_sequences WHERE route = ?", (route,)
        ).fetchone()["next_local_id"]

    def get_item(self, origin_route: str, chat_id: int, message_id: int):
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT * FROM items
                WHERE origin_route = ? AND chat_id = ? AND message_id = ?
                """,
                (origin_route, chat_id, message_id),
            ).fetchone()

    def item_by_local_id(self, route: str, local_id: int):
        """Find an item by the name it is filed under, rather than by identity.

        This is how a client that only kept the `<route>/<name>` handle the
        capture answered with reaches its item again. `items_route_local_id`
        makes the pair unique, so a route change moves the handle.
        """
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM items WHERE route = ? AND local_id = ?",
                (route, local_id),
            ).fetchone()

    def item_for_source_message(self, origin_route: str, chat_id: int, message_id: int):
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT items.* FROM item_messages
                JOIN items ON items.origin_route = item_messages.origin_route
                    AND items.chat_id = item_messages.chat_id
                    AND items.message_id = item_messages.item_message_id
                WHERE item_messages.origin_route = ?
                    AND item_messages.chat_id = ? AND item_messages.message_id = ?
                """,
                (origin_route, chat_id, message_id),
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
                SELECT route, local_id, message_id, status, revision,
                       created_at, updated_at, short_text, error, processing_step,
                       problems
                FROM items
                WHERE status = ?
                ORDER BY updated_at DESC
                """,
                (status,),
            ).fetchall()

    def register_processors(self, processors: Iterable[str]) -> None:
        """Make configured processors visible before their first execution."""
        with self._connect() as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO processor_stats (processor) VALUES (?)",
                ((processor,) for processor in processors),
            )

    def record_processor_run(
        self,
        processor: str,
        outcome: str,
        issues: tuple[ProcessingIssue, ...],
    ) -> None:
        """Increment lifetime processor and stable-reason counters."""
        if outcome not in ("succeeded", "partial", "failed"):
            raise ValueError(f"Unknown processor outcome: {outcome}")
        reason_counts = Counter(issue.reason for issue in issues)
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO processor_stats (processor) VALUES (?)",
                (processor,),
            )
            connection.execute(
                f"""
                UPDATE processor_stats
                SET runs = runs + 1, {outcome} = {outcome} + 1
                WHERE processor = ?
                """,
                (processor,),
            )
            connection.executemany(
                """
                INSERT INTO processor_reason_stats (
                    processor, outcome, reason, occurrences
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(processor, outcome, reason) DO UPDATE SET
                    occurrences = occurrences + excluded.occurrences
                """,
                (
                    (processor, outcome, reason, occurrences)
                    for reason, occurrences in reason_counts.items()
                ),
            )

    def processor_statistics(self) -> list[dict[str, Any]]:
        """Return cumulative processor totals with their reason breakdowns."""
        with self._connect() as connection:
            processors = connection.execute(
                """
                SELECT processor, runs, succeeded, partial, failed
                FROM processor_stats
                ORDER BY processor
                """
            ).fetchall()
            reasons = connection.execute(
                """
                SELECT processor, outcome, reason, occurrences
                FROM processor_reason_stats
                ORDER BY processor, outcome, reason
                """
            ).fetchall()
        reasons_by_processor: dict[str, list[dict[str, Any]]] = {}
        for reason in reasons:
            reasons_by_processor.setdefault(reason["processor"], []).append(dict(reason))
        return [
            {
                **dict(processor),
                "reasons": reasons_by_processor.get(processor["processor"], []),
            }
            for processor in processors
        ]

    def get_item_with_status(self, status: str):
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM items WHERE status = ? LIMIT 1", (status,)
            ).fetchone()

    def _save_state(
        self,
        origin_route: str,
        chat_id: int,
        message_id: int,
        route: str,
        local_id: int,
        status: str,
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
                    origin_route, chat_id, message_id, route, local_id, status,
                    revision, created_at, updated_at, short_text, error,
                    processing_step
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(origin_route, chat_id, message_id) DO UPDATE SET
                    route = excluded.route,
                    local_id = excluded.local_id,
                    status = excluded.status,
                    revision = excluded.revision,
                    updated_at = excluded.updated_at,
                    short_text = excluded.short_text,
                    error = excluded.error,
                    processing_step = excluded.processing_step,
                    -- A re-captured revision is processed again from scratch, so
                    -- the previous revision's problems no longer describe it.
                    problems = NULL
                """,
                (
                    origin_route,
                    chat_id,
                    message_id,
                    route,
                    local_id,
                    status,
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
        origin_route: str,
        chat_id: int,
        message_id: int,
        route: str,
        local_id: int,
        content: str,
        revision: int,
        received_at: str,
        edited_at: str | None,
        capture_payload: dict[str, Any] | list[dict[str, Any]] | None = None,
        attachments: list[DownloadedAttachment] | None = None,
        replace_attachment_source_ids: set[int] | None = None,
        media_group_id: str | None = None,
        source_message_ids: list[int] | None = None,
    ) -> None:
        capture_path = item_path / CAPTURE_DIR
        capture_path.mkdir(parents=True, exist_ok=True)
        atomic_write(capture_path / "source.md", content)
        atomic_write(capture_path / "message.md", content)
        existing_metadata = self._read_metadata(item_path)
        attachment_manifest = existing_metadata.get("attachments", [])
        warnings = existing_metadata.get("download_warnings", [])
        if attachments is not None:
            attachment_manifest, warnings = self._write_attachments(
                item_path,
                attachments,
                existing_metadata.get("attachments", []),
                replace_attachment_source_ids,
            )
        if capture_payload is not None:
            atomic_write(
                capture_path / PAYLOAD_NAME,
                json.dumps(capture_payload, ensure_ascii=False, indent=2) + "\n",
            )
        metadata = {
            "origin_route": origin_route,
            "chat_id": chat_id,
            "message_id": message_id,
            "route": route,
            "local_id": local_id,
            "received_at": received_at,
            "edited_at": edited_at,
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
        self,
        item_path: Path,
        attachments: list[DownloadedAttachment],
        existing_manifest: list[dict[str, Any]],
        replace_source_ids: set[int] | None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        attachments_dir = item_path / CAPTURE_DIR / "attachments"
        temporary_dir = item_path / CAPTURE_DIR / ".attachments.tmp"
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir)
        temporary_dir.mkdir()
        manifest = []
        if replace_source_ids is not None:
            for value in existing_manifest:
                if value.get("source_message_id") in replace_source_ids:
                    continue
                record = dict(value)
                relative_path = record.get("path")
                if relative_path:
                    source = item_path / relative_path
                    if source.is_file():
                        shutil.copyfile(source, temporary_dir / source.name)
                    else:
                        record["path"] = None
                        record["download_status"] = "unavailable"
                        record["warning"] = "Previously downloaded attachment is missing."
                manifest.append(record)

        next_index = 1
        existing_indices = []
        for value in manifest:
            relative_path = value.get("path")
            if relative_path:
                prefix = Path(relative_path).name.split("-", 1)[0]
                if prefix.isdigit():
                    existing_indices.append(int(prefix))
        if existing_indices:
            next_index = max(existing_indices) + 1

        for attachment in attachments:
            record = attachment.spec.to_dict()
            record["path"] = None
            if attachment.data is None:
                record["download_status"] = "unavailable"
                record["warning"] = attachment.warning
            else:
                filename = (
                    f"{next_index:02d}-{attachment.spec.kind}{attachment.spec.extension}"
                )
                next_index += 1
                atomic_write_bytes(temporary_dir / filename, attachment.data)
                record["path"] = f"{CAPTURE_DIR}/attachments/{filename}"
                record["download_status"] = "downloaded"
            manifest.append(record)
        warnings = [
            value["warning"]
            for value in manifest
            if value.get("download_status") == "unavailable" and value.get("warning")
        ]
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
        staging_path = self.staging_for(item["route"], item["created_at"], item["local_id"])
        if staging_path.is_dir():
            return staging_path
        return self.inbox_for(item["route"], item["created_at"], item["local_id"])

    def _find_moved_item(self, item) -> tuple[Path, dict[str, Any]] | None:
        """Locate an item whose directory moved before its row could be updated.

        A re-route writes metadata.json and then renames the directory; a crash
        between the rename and the state write leaves the row naming a path that
        no longer exists. metadata.json was written first and atomically, so the
        directory on disk is the authority — find it rather than declare the item
        lost.
        """
        for root in (self.staging_dir, self.inbox_dir):
            for route_path in sorted(root.iterdir()) if root.is_dir() else ():
                if not route_path.is_dir():
                    continue
                for candidate in sorted(route_path.iterdir()):
                    if not candidate.is_dir():
                        continue
                    metadata = self._read_metadata(candidate)
                    if (
                        metadata.get("origin_route") == item["origin_route"]
                        and metadata.get("chat_id") == item["chat_id"]
                        and metadata.get("message_id") == item["message_id"]
                    ):
                        return candidate, metadata
        return None

    def _reconcile_interrupted_items(self) -> None:
        """Repair only transitions that may have been interrupted by shutdown."""
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT * FROM items").fetchall()
            for item in rows:
                key = (item["origin_route"], item["chat_id"], item["message_id"])
                inbox_path = self.inbox_for(item["route"], item["created_at"], item["local_id"])
                staging_path = self.staging_for(
                    item["route"], item["created_at"], item["local_id"]
                )
                if not staging_path.is_dir() and not inbox_path.is_dir():
                    moved = self._find_moved_item(item)
                    if moved is not None:
                        path, metadata = moved
                        connection.execute(
                            """
                            UPDATE items SET route = ?, local_id = ?, revision = ?,
                                status = 'received', processing_step = NULL,
                                error = NULL, updated_at = ?
                            WHERE origin_route = ? AND chat_id = ? AND message_id = ?
                            """,
                            (
                                metadata["route"],
                                metadata["local_id"],
                                metadata.get("revision", item["revision"]),
                                now_iso(),
                                *key,
                            ),
                        )
                        logger.warning(
                            "Repaired interrupted route change for %s: now %s",
                            item["message_id"],
                            path.relative_to(self.data_dir),
                        )
                        continue
                if inbox_path.is_dir() and item["status"] != "ready":
                    connection.execute(
                        """
                        UPDATE items SET status = 'ready', processing_step = NULL,
                            error = NULL, updated_at = ?
                        WHERE origin_route = ? AND chat_id = ? AND message_id = ?
                        """,
                        (now_iso(), *key),
                    )
                elif staging_path.is_dir() and item["status"] == "processing":
                    connection.execute(
                        """
                        UPDATE items SET status = 'received', processing_step = NULL,
                            error = NULL, updated_at = ?
                        WHERE origin_route = ? AND chat_id = ? AND message_id = ?
                        """,
                        (now_iso(), *key),
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
                        WHERE origin_route = ? AND chat_id = ? AND message_id = ?
                        """,
                        ("Item directory is missing", now_iso(), *key),
                    )

    def capture(
        self,
        origin_route: str,
        chat_id: int,
        message_id: int,
        content: str,
        route: str | None = None,
        edited_at: str | None = None,
        received_at: str | None = None,
        capture_payload: dict[str, Any] | list[dict[str, Any]] | None = None,
        attachments: list[DownloadedAttachment] | None = None,
        replace_attachment_source_ids: set[int] | None = None,
        media_group_id: str | None = None,
        source_message_ids: list[int] | None = None,
        force_revision: bool = False,
        reserved_local_id: int | None = None,
    ) -> CapturedItem:
        """Durably capture or update a message in staging.

        `route` is where the item should be filed. Omitted, it keeps the route it
        already has, or files a new item under the bot that received it.
        `reserved_local_id` is for a caller that already claimed a number with
        `allocate_local_id` and must not be given a second one.
        """
        with self._lock:
            existing = self.get_item(origin_route, chat_id, message_id)
            already_known = existing is not None
            revision = existing["revision"] if existing else 1
            if existing and (edited_at is not None or force_revision):
                revision += 1
            created_at = existing["created_at"] if existing else received_at or now_iso()
            current_route = existing["route"] if existing else origin_route
            target_route = route or current_route
            if target_route not in self.routes:
                raise ValueError(f"Unknown route: {target_route}")

            # A new item, and one whose route changed, both need a number in the
            # destination route; an ordinary edit keeps the one it has.
            if existing is not None and target_route == current_route:
                local_id = existing["local_id"]
            elif reserved_local_id is not None:
                local_id = reserved_local_id
            else:
                with self._connect() as connection:
                    local_id = self._allocate_local_id(connection, target_route)

            # Where the item is now, which is not where it belongs when a hashtag
            # has moved it.
            current_local_id = existing["local_id"] if existing else local_id
            staging_path = self.staging_for(current_route, created_at, current_local_id)
            inbox_path = self.inbox_for(current_route, created_at, current_local_id)

            if inbox_path.exists():
                current_metadata = self._read_metadata(inbox_path)
                if staging_path.exists():
                    raise RuntimeError(f"Both staging and inbox contain {inbox_path.name}")
                inbox_path.rename(staging_path)
            else:
                staging_path.mkdir(parents=True, exist_ok=True)
                current_metadata = self._read_metadata(staging_path)

            if edited_at is None:
                edited_at = current_metadata.get("edited_at")
            # metadata.json is written before the move, so a crash between the two
            # leaves the directory self-describing; see _find_moved_item.
            self._write_item(
                staging_path,
                origin_route,
                chat_id,
                message_id,
                target_route,
                local_id,
                content,
                revision,
                created_at,
                edited_at,
                capture_payload,
                attachments,
                replace_attachment_source_ids,
                media_group_id,
                source_message_ids,
            )
            destination = self.staging_for(target_route, created_at, local_id)
            if destination != staging_path:
                if destination.exists():
                    raise RuntimeError(
                        f"Staging already contains {target_route}/{destination.name}"
                    )
                staging_path.rename(destination)
                staging_path = destination
                logger.info(
                    "Moved item %s from route %s to %s", message_id, current_route, target_route
                )

            self._save_state(
                origin_route,
                chat_id,
                message_id,
                target_route,
                local_id,
                "received",
                revision,
                created_at,
                content,
            )
            with self._connect() as connection:
                for source_message_id in source_message_ids or [message_id]:
                    connection.execute(
                        """
                        INSERT INTO item_messages (
                            origin_route, chat_id, message_id, item_message_id,
                            media_group_id
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(origin_route, chat_id, message_id) DO UPDATE SET
                            item_message_id = excluded.item_message_id,
                            media_group_id = excluded.media_group_id
                        """,
                        (origin_route, chat_id, source_message_id, message_id, media_group_id),
                    )
            return CapturedItem(
                origin_route,
                chat_id,
                message_id,
                target_route,
                local_id,
                revision,
                "received",
                staging_path,
                already_known,
            )

    def reset_generated_output(self, item_path: Path) -> None:
        """Discard everything a previous revision generated inside one item.

        A replacement rewrites the item's content, so the last revision's index,
        link table and extractions describe text that is gone. What survives is
        exactly `RESERVED_GENERATED_PATHS`: the paths no step is allowed to
        write are the ones that are not generated.
        """
        for entry in item_path.iterdir():
            if entry.name in RESERVED_GENERATED_PATHS:
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()

    def promote_if_current(
        self, item: CapturedItem | ProcessingJob, result: ProcessingResult | None = None
    ) -> bool:
        """Commit a result and promote only if its revision still owns the item."""
        with self._lock, self._connect() as connection:
            current = connection.execute(
                """
                SELECT * FROM items
                WHERE origin_route = ? AND chat_id = ? AND message_id = ?
                """,
                (item.origin_route, item.chat_id, item.message_id),
            ).fetchone()
            if (
                current is None
                or current["revision"] != item.revision
                or current["status"] not in ("received", "processing")
            ):
                return False

            # The row, not the job: a hashtag may have moved the item since.
            route = current["route"]
            local_id = current["local_id"]
            item_name = f"{route}/{self.item_name(current['created_at'], local_id)}"
            staging_path = self.staging_for(route, current["created_at"], local_id)
            inbox_path = self.inbox_for(route, current["created_at"], local_id)
            if not staging_path.is_dir():
                raise FileNotFoundError(f"Staged item is missing: {item_name}")
            if inbox_path.exists():
                raise RuntimeError(f"Inbox already contains {item_name}")
            if result is not None:
                capture_path = staging_path / CAPTURE_DIR
                capture_path.mkdir(parents=True, exist_ok=True)
                pending_files: list[tuple[Path, Path]] = []
                try:
                    for generated in result.generated_files:
                        relative = generated.relative_path
                        if relative.is_absolute() or ".." in relative.parts:
                            raise ValueError(f"Unsafe generated path: {relative}")
                        if relative.parts[0] in RESERVED_GENERATED_PATHS:
                            raise ValueError(f"Reserved generated path: {relative}")
                        destination = staging_path / relative
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        temporary = destination.with_name(f".{destination.name}.tmp")
                        pending_files.append((temporary, destination))
                        shutil.copyfile(generated.source_path, temporary)
                    source = result.source_markdown
                    if source is None:
                        source = result.message_markdown
                    for name, content in (
                        ("source.md", source),
                        ("message.md", result.message_markdown),
                    ):
                        destination = capture_path / name
                        temporary = destination.with_name(f".{destination.name}.tmp")
                        temporary.write_text(content, encoding="utf-8")
                        pending_files.append((temporary, destination))
                    for temporary, destination in pending_files:
                        temporary.replace(destination)
                except Exception:
                    for temporary, _ in pending_files:
                        temporary.unlink(missing_ok=True)
                    raise

            staging_path.rename(inbox_path)
            problems = result.problems if result is not None else []
            connection.execute(
                """
                UPDATE items SET status = 'ready', processing_step = NULL,
                    error = NULL, problems = ?, updated_at = ?
                WHERE origin_route = ? AND chat_id = ? AND message_id = ?
                    AND revision = ?
                """,
                (
                    json.dumps([problem.to_dict() for problem in problems], ensure_ascii=False)
                    if problems
                    else None,
                    now_iso(),
                    item.origin_route,
                    item.chat_id,
                    item.message_id,
                    item.revision,
                ),
            )
            return True

    def claim_next_received(self) -> ProcessingJob | None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # One queue across every route: one worker, fairness by arrival.
            item = connection.execute(
                """
                SELECT * FROM items WHERE status = 'received'
                ORDER BY updated_at, route, chat_id, message_id LIMIT 1
                """
            ).fetchone()
            if item is None:
                return None
            updated = connection.execute(
                """
                UPDATE items SET status = 'processing', processing_step = NULL,
                    error = NULL, updated_at = ?
                WHERE origin_route = ? AND chat_id = ? AND message_id = ?
                    AND revision = ? AND status = 'received'
                """,
                (
                    now_iso(),
                    item["origin_route"],
                    item["chat_id"],
                    item["message_id"],
                    item["revision"],
                ),
            )
            if updated.rowcount != 1:
                return None
            return ProcessingJob(
                item["origin_route"],
                item["chat_id"],
                item["message_id"],
                item["route"],
                item["local_id"],
                item["revision"],
                self.staging_for(item["route"], item["created_at"], item["local_id"]),
            )

    def set_processing_step(self, job: ProcessingJob, step: str) -> bool:
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE items SET processing_step = ?, updated_at = ?
                WHERE origin_route = ? AND chat_id = ? AND message_id = ?
                    AND revision = ? AND status = 'processing'
                """,
                (
                    step,
                    now_iso(),
                    job.origin_route,
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
                WHERE origin_route = ? AND chat_id = ? AND message_id = ?
                    AND revision = ? AND status = 'processing'
                """,
                (
                    error,
                    processing_step,
                    now_iso(),
                    job.origin_route,
                    job.chat_id,
                    job.message_id,
                    job.revision,
                ),
            )
            return updated.rowcount == 1

    def stage_pending_message(
        self,
        origin_route: str,
        chat_id: int,
        message_id: int,
        media_group_id: str | None,
        content: str,
        received_at: str,
        edited_at: str | None,
        raw_payload: dict[str, Any],
        specs: list[AttachmentSpec],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO pending_capture_messages (
                    origin_route, chat_id, message_id, media_group_id, received_at,
                    edited_at, content, raw_json, attachments_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(origin_route, chat_id, message_id) DO UPDATE SET
                    media_group_id = excluded.media_group_id,
                    received_at = excluded.received_at,
                    edited_at = excluded.edited_at,
                    content = excluded.content,
                    raw_json = excluded.raw_json,
                    attachments_json = excluded.attachments_json
                """,
                (
                    origin_route,
                    chat_id,
                    message_id,
                    media_group_id,
                    received_at,
                    edited_at,
                    content,
                    json.dumps(raw_payload, ensure_ascii=False),
                    json.dumps([spec.to_dict() for spec in specs], ensure_ascii=False),
                ),
            )

    def pending_chat_ids(self, origin_route: str) -> list[int]:
        """Chats with unfinished captures on one bot.

        Route-scoped because a private chat's id is the user's own id and is
        therefore identical on every bot: unscoped, every bot would try to
        finalize every other bot's messages and download their files with the
        wrong token.
        """
        with self._connect() as connection:
            return [
                row["chat_id"]
                for row in connection.execute(
                    """
                    SELECT DISTINCT chat_id FROM pending_capture_messages
                    WHERE origin_route = ?
                    """,
                    (origin_route,),
                )
            ]

    def pending_messages(self, origin_route: str, chat_id: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM pending_capture_messages
                WHERE origin_route = ? AND chat_id = ?
                ORDER BY received_at, message_id
                """,
                (origin_route, chat_id),
            ).fetchall()
        return [
            {
                "chat_id": row["chat_id"],
                "message_id": row["message_id"],
                "media_group_id": row["media_group_id"],
                "received_at": row["received_at"],
                "edited_at": row["edited_at"],
                "content": row["content"],
                "payload": json.loads(row["raw_json"]),
                "specs": [
                    AttachmentSpec.from_dict(value)
                    for value in json.loads(row["attachments_json"])
                ],
            }
            for row in rows
        ]

    def item_bundle(
        self, origin_route: str, chat_id: int, message_id: int
    ) -> dict[str, Any] | None:
        item = self.get_item(origin_route, chat_id, message_id)
        if item is None:
            return None
        item_path = self._path_for_row(item)
        metadata = self._read_metadata(item_path) if item_path.is_dir() else {}
        payloads = []
        payload_path = item_path / CAPTURE_DIR / PAYLOAD_NAME
        if payload_path.is_file():
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
                payloads = payload["messages"]
            elif isinstance(payload, list):
                payloads = payload
            else:
                payloads = [payload]
        with self._connect() as connection:
            source_ids = [
                row["message_id"]
                for row in connection.execute(
                    """
                    SELECT message_id FROM item_messages
                    WHERE origin_route = ? AND chat_id = ? AND item_message_id = ?
                    ORDER BY message_id
                    """,
                    (origin_route, chat_id, message_id),
                )
            ]
        return {
            "item": dict(item),
            "metadata": metadata,
            "payloads": payloads,
            "source_message_ids": source_ids or [message_id],
        }

    def clear_pending_messages(
        self, origin_route: str, chat_id: int, message_ids: list[int]
    ) -> None:
        if not message_ids:
            return
        with self._connect() as connection:
            placeholders = ",".join("?" for _ in message_ids)
            connection.execute(
                f"""
                DELETE FROM pending_capture_messages
                WHERE origin_route = ? AND chat_id = ? AND message_id IN ({placeholders})
                """,
                (origin_route, chat_id, *message_ids),
            )
