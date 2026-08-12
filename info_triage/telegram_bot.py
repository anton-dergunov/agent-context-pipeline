"""Telegram capture, durable message batching, and media download."""

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from telegram import Message, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    filters,
)

from .models import AttachmentSpec, DownloadedAttachment
from .processing import ProcessingCoordinator
from .storage import CaptureStore

MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
# Consecutive logical messages at or below this Telegram timestamp gap become one item.
CAPTURE_GROUP_MAX_GAP_SECONDS = 3.0
CAPTURE_GROUP_SETTLE_SECONDS = CAPTURE_GROUP_MAX_GAP_SECONDS + 1.0
logger = logging.getLogger("info_triage")


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
    candidates: list[tuple[str, Any]] = []
    if message.photo:
        candidates.append(("photo", message.photo[-1]))
    for kind in ("document", "video", "animation", "audio", "voice", "video_note"):
        value = getattr(message, kind)
        if value is not None:
            candidates.append((kind, value))

    return [
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
        for kind, value in candidates
    ]


def attachment_specs_from_payload(payload: dict[str, Any]) -> list[AttachmentSpec]:
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
    """Return useful Markdown, or None for deliberately ignored messages."""
    text = message.text if message.text is not None else message.caption
    location = location_markdown(message)
    if text is not None or location or attachment_specs(message):
        return "\n\n".join(part for part in (text or "", location) if part)
    return None


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
                    f"{spec.kind} is {spec.file_size} bytes and exceeds "
                    "Telegram's 20 MB download limit.",
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


def is_authorized(update: Update, allowed_user_id: int) -> bool:
    user = update.effective_user
    authorized = user is not None and user.id == allowed_user_id
    if not authorized:
        logger.info(
            "Ignored update from unauthorized user %s",
            user.id if user else "unknown",
        )
    return authorized


def _payload_order(payload: dict[str, Any]) -> tuple[int, int]:
    return int(payload.get("date", 0)), int(payload["message_id"])


def _is_forwarded(payload: dict[str, Any]) -> bool:
    return any(
        payload.get(key) is not None
        for key in (
            "forward_origin",
            "forward_from",
            "forward_from_chat",
            "forward_sender_name",
        )
    )


def render_capture_payloads(
    payloads: list[dict[str, Any]],
    *,
    voice_transcripts: Mapping[int, Sequence[str]] | None = None,
) -> str:
    """Render ordered raw Telegram messages, identifying only explicit forwards."""
    ordered = sorted(payloads, key=_payload_order)
    parts = []
    for payload in ordered:
        content = capture_content(Message.de_json(payload, None)) or ""
        transcripts = (voice_transcripts or {}).get(payload["message_id"], ())
        voice_content = "\n\n".join(f"Voice note: {text}" for text in transcripts)
        parts.append(
            (
                payload,
                "\n\n".join(value for value in (voice_content, content) if value),
            )
        )
    forwarded = [content for payload, content in parts if _is_forwarded(payload)]
    notes = [content for payload, content in parts if not _is_forwarded(payload)]
    if forwarded and notes:
        source_text = "\n\n".join(value for value in forwarded if value)
        note_text = "\n\n".join(value for value in notes if value)
        if note_text:
            return "\n\n".join(
                value for value in (source_text, f"## Note\n\n{note_text}") if value
            )
    return "\n\n".join(content for _, content in parts if content)


def _logical_units(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    albums: dict[str, list[dict[str, Any]]] = {}
    units = []
    for row in rows:
        media_group_id = row["media_group_id"]
        if media_group_id:
            albums.setdefault(media_group_id, []).append(row)
        else:
            units.append([row])
    units.extend(albums.values())
    logical_units = []
    for members in units:
        ordered = sorted(
            members,
            key=lambda value: (
                datetime.fromisoformat(value["received_at"]),
                value["message_id"],
            ),
        )
        logical_units.append(
            {
                "rows": ordered,
                "start": datetime.fromisoformat(ordered[0]["received_at"]),
                "end": max(datetime.fromisoformat(row["received_at"]) for row in ordered),
            }
        )
    return sorted(
        logical_units,
        key=lambda unit: (unit["start"], unit["rows"][0]["message_id"]),
    )


def group_new_messages(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group logical messages while every consecutive gap stays within the limit."""
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous_end = None
    for unit in _logical_units(rows):
        gap = (
            (unit["start"] - previous_end).total_seconds()
            if previous_end is not None
            else None
        )
        if current and gap is not None and gap > CAPTURE_GROUP_MAX_GAP_SECONDS:
            batches.append(current)
            current = []
        current.extend(unit["rows"])
        previous_end = max(previous_end, unit["end"]) if previous_end else unit["end"]
    if current:
        batches.append(current)
    return batches


def _pending_batches(
    store: CaptureStore, chat_id: int
) -> list[tuple[int | None, list[dict[str, Any]]]]:
    existing: dict[int, list[dict[str, Any]]] = {}
    new_rows = []
    for row in store.pending_messages(chat_id):
        item = store.item_for_source_message(chat_id, row["message_id"])
        if item is None:
            new_rows.append(row)
        else:
            existing.setdefault(item["message_id"], []).append(row)
    batches = [(message_id, rows) for message_id, rows in existing.items()]
    batches.extend((None, rows) for rows in group_new_messages(new_rows))
    return sorted(
        batches,
        key=lambda value: min(
            datetime.fromisoformat(row["received_at"]) for row in value[1]
        ),
    )


def schedule_capture_finalization(application: Application, chat_id: int) -> None:
    tasks: dict[int, asyncio.Task] = application.bot_data.setdefault(
        "capture_group_tasks", {}
    )
    task = tasks.get(chat_id)
    if task and not task.done():
        task.cancel()
    tasks[chat_id] = application.create_task(
        finalize_pending_chat(application, chat_id),
        name=f"capture-group-{chat_id}",
    )


async def _finalize_batch(
    application: Application,
    chat_id: int,
    item_message_id: int | None,
    rows: list[dict[str, Any]],
) -> None:
    store: CaptureStore = application.bot_data["store"]
    coordinator: ProcessingCoordinator = application.bot_data["coordinator"]
    staged_payloads = {row["message_id"]: row["payload"] for row in rows}
    existing_bundle = (
        store.item_bundle(chat_id, item_message_id)
        if item_message_id is not None
        else None
    )
    payloads_by_id = {}
    if existing_bundle:
        payloads_by_id.update(
            {payload["message_id"]: payload for payload in existing_bundle["payloads"]}
        )
    payloads_by_id.update(staged_payloads)
    payloads = sorted(payloads_by_id.values(), key=_payload_order)
    if not payloads:
        return

    if item_message_id is None:
        item_message_id = payloads[0]["message_id"]
        received_at = min(row["received_at"] for row in rows)
        source_message_ids = [payload["message_id"] for payload in payloads]
        replace_source_ids = None
        media_group_ids = {row["media_group_id"] for row in rows}
        media_group_id = (
            next(iter(media_group_ids))
            if len(media_group_ids) == 1 and None not in media_group_ids
            else None
        )
    else:
        received_at = existing_bundle["item"]["created_at"]
        source_message_ids = sorted(
            set(existing_bundle["source_message_ids"]) | set(payloads_by_id)
        )
        replace_source_ids = set(staged_payloads)
        media_group_id = existing_bundle["metadata"].get("media_group_id")

    specs = [spec for row in rows for spec in row["specs"]]
    attachments = await download_attachments(application.bot, specs)
    edited_at = max(
        (row["edited_at"] for row in rows if row["edited_at"]), default=None
    )
    telegram_payload = payloads[0] if len(payloads) == 1 else {"messages": payloads}
    item = store.capture(
        chat_id,
        item_message_id,
        render_capture_payloads(payloads),
        edited_at=edited_at,
        received_at=received_at,
        telegram_payload=telegram_payload,
        attachments=attachments,
        replace_attachment_source_ids=replace_source_ids,
        media_group_id=media_group_id,
        source_message_ids=source_message_ids,
    )
    coordinator.submit(item)
    store.clear_pending_messages(chat_id, [row["message_id"] for row in rows])


async def finalize_pending_chat(application: Application, chat_id: int) -> None:
    try:
        await asyncio.sleep(CAPTURE_GROUP_SETTLE_SECONDS)
        store: CaptureStore = application.bot_data["store"]
        for item_message_id, rows in _pending_batches(store, chat_id):
            try:
                await _finalize_batch(application, chat_id, item_message_id, rows)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to finalize Telegram capture group")
                if item_message_id is None:
                    await application.bot.send_message(
                        chat_id, "Could not save this item. Check the server log."
                    )
    finally:
        tasks = application.bot_data.get("capture_group_tasks", {})
        if tasks.get(chat_id) is asyncio.current_task():
            tasks.pop(chat_id, None)


async def recover_pending_captures(application: Application) -> None:
    store: CaptureStore = application.bot_data["store"]
    for chat_id in store.pending_chat_ids():
        schedule_capture_finalization(application, chat_id)


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
    try:
        store.stage_pending_message(
            message.chat_id,
            message.message_id,
            message.media_group_id,
            content,
            message.date.isoformat(),
            edited_at,
            raw_payload,
            attachment_specs(message),
        )
        schedule_capture_finalization(context.application, message.chat_id)
    except Exception:
        logger.exception("Failed to stage Telegram message")
        if not is_edit:
            await message.reply_text("Could not save this item. Check the server log.")


async def handle_new_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await handle_message(update, context, is_edit=False)


async def handle_edited_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await handle_message(update, context, is_edit=True)


def build_application(
    bot_token: str,
    allowed_user_id: int,
    store: CaptureStore,
    coordinator: ProcessingCoordinator,
) -> Application:
    application = (
        Application.builder()
        .token(bot_token)
        .concurrent_updates(False)
        .post_init(recover_pending_captures)
        .build()
    )
    application.bot_data["store"] = store
    application.bot_data["coordinator"] = coordinator
    application.bot_data["allowed_user_id"] = allowed_user_id
    application.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & ~filters.COMMAND, handle_new_message
        )
    )
    application.add_handler(
        MessageHandler(
            filters.UpdateType.EDITED_MESSAGE & ~filters.COMMAND,
            handle_edited_message,
        )
    )
    return application
