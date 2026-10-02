"""Telegram capture, durable message batching, and media download."""

import asyncio
import json
import logging
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
from .rendering import entity_slice, message_content, payload_order, render_capture_payloads
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
            extension=_extension_for(kind, value.get("mime_type"), value.get("file_name")),
            source_message_id=message_id,
        )
        for kind, value in candidates
    ]


def capture_content(message: Message) -> str | None:
    """Return useful Markdown, or None for deliberately ignored messages."""
    return message_content(message)


async def download_attachments(bot, specs: list[AttachmentSpec]) -> list[DownloadedAttachment]:
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
                    DownloadedAttachment(spec, bytes(await telegram_file.download_as_bytearray()))
                )
                break
            except TelegramError as error:
                if attempt == 2:
                    logger.warning("Could not download Telegram %s: %s", spec.kind, error)
                    attachments.append(
                        DownloadedAttachment(spec, None, f"Could not download {spec.kind}: {error}")
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


def group_new_messages(
    rows: list[dict[str, Any]],
    max_gap_seconds: float = CAPTURE_GROUP_MAX_GAP_SECONDS,
) -> list[list[dict[str, Any]]]:
    """Group logical messages while every consecutive gap stays within the limit."""
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous_end = None
    for unit in _logical_units(rows):
        gap = (unit["start"] - previous_end).total_seconds() if previous_end is not None else None
        if current and gap is not None and gap > max_gap_seconds:
            batches.append(current)
            current = []
        current.extend(unit["rows"])
        previous_end = max(previous_end, unit["end"]) if previous_end else unit["end"]
    if current:
        batches.append(current)
    return batches


def requested_route(payloads: list[dict[str, Any]], routes: tuple[str, ...]) -> str | None:
    """Return the configured route a `#route` hashtag asks for, or None.

    The correction path for having shared to the wrong bot: edit the message, add
    `#job`, and the item moves. Read out of Telegram's own `hashtag` entities
    rather than out of the text, because Telegram has already located them and a
    `#clip` inside a URL fragment is not an instruction.
    """
    found = set()
    for payload in payloads:
        for text_key, entity_key in (("text", "entities"), ("caption", "caption_entities")):
            text = payload.get(text_key)
            if not isinstance(text, str):
                continue
            for entity in payload.get(entity_key) or ():
                if not isinstance(entity, dict) or entity.get("type") != "hashtag":
                    continue
                tag = entity_slice(text, entity.get("offset"), entity.get("length"))
                if tag and tag[1:].lower() in routes:
                    found.add(tag[1:].lower())
    # Two routes named at once says nothing about which was meant. Never guess.
    return found.pop() if len(found) == 1 else None


def _pending_batches(
    store: CaptureStore,
    origin_route: str,
    chat_id: int,
    max_gap_seconds: float = CAPTURE_GROUP_MAX_GAP_SECONDS,
) -> list[tuple[int | None, list[dict[str, Any]]]]:
    existing: dict[int, list[dict[str, Any]]] = {}
    new_rows = []
    for row in store.pending_messages(origin_route, chat_id):
        item = store.item_for_source_message(origin_route, chat_id, row["message_id"])
        if item is None:
            new_rows.append(row)
        else:
            existing.setdefault(item["message_id"], []).append(row)
    batches = [(message_id, rows) for message_id, rows in existing.items()]
    batches.extend((None, rows) for rows in group_new_messages(new_rows, max_gap_seconds))
    return sorted(
        batches,
        key=lambda value: min(datetime.fromisoformat(row["received_at"]) for row in value[1]),
    )


def schedule_capture_finalization(application: Application, chat_id: int) -> None:
    tasks: dict[int, asyncio.Task] = application.bot_data.setdefault("capture_group_tasks", {})
    task = tasks.get(chat_id)
    if task and not task.done():
        task.cancel()
    tasks[chat_id] = application.create_task(
        finalize_pending_chat(application, chat_id),
        name=f"capture-group-{application.bot_data['route']}-{chat_id}",
    )


async def _finalize_batch(
    application: Application,
    chat_id: int,
    item_message_id: int | None,
    rows: list[dict[str, Any]],
) -> None:
    store: CaptureStore = application.bot_data["store"]
    coordinator: ProcessingCoordinator = application.bot_data["coordinator"]
    origin_route: str = application.bot_data["route"]
    staged_payloads = {row["message_id"]: row["payload"] for row in rows}
    existing_bundle = (
        store.item_bundle(origin_route, chat_id, item_message_id)
        if item_message_id is not None
        else None
    )
    payloads_by_id = {}
    if existing_bundle:
        payloads_by_id.update(
            {payload["message_id"]: payload for payload in existing_bundle["payloads"]}
        )
    payloads_by_id.update(staged_payloads)
    payloads = sorted(payloads_by_id.values(), key=payload_order)
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
    edited_at = max((row["edited_at"] for row in rows if row["edited_at"]), default=None)
    capture_payload = payloads[0] if len(payloads) == 1 else {"messages": payloads}
    current_route = existing_bundle["item"]["route"] if existing_bundle else origin_route
    item = store.capture(
        origin_route,
        chat_id,
        item_message_id,
        render_capture_payloads(payloads),
        route=requested_route(payloads, store.routes) or current_route,
        edited_at=edited_at,
        received_at=received_at,
        capture_payload=capture_payload,
        attachments=attachments,
        replace_attachment_source_ids=replace_source_ids,
        media_group_id=media_group_id,
        source_message_ids=source_message_ids,
    )
    coordinator.submit(item)
    store.clear_pending_messages(origin_route, chat_id, [row["message_id"] for row in rows])


async def finalize_pending_chat(application: Application, chat_id: int) -> None:
    try:
        await asyncio.sleep(
            application.bot_data.get("capture_group_settle_seconds", CAPTURE_GROUP_SETTLE_SECONDS)
        )
        store: CaptureStore = application.bot_data["store"]
        max_gap_seconds = application.bot_data.get(
            "capture_group_max_gap_seconds", CAPTURE_GROUP_MAX_GAP_SECONDS
        )
        for item_message_id, rows in _pending_batches(
            store, application.bot_data["route"], chat_id, max_gap_seconds
        ):
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
    """Resume this bot's unfinished capture groups after a restart.

    Scoped to this route: a private chat's id is the user's own id and therefore
    identical on every bot, so an unscoped sweep would have every bot try to
    finalize the others' messages and download their files with the wrong token.
    """
    store: CaptureStore = application.bot_data["store"]
    for chat_id in store.pending_chat_ids(application.bot_data["route"]):
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
            context.application.bot_data["route"],
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


async def handle_new_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await handle_message(update, context, is_edit=False)


async def handle_edited_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await handle_message(update, context, is_edit=True)


def build_application(
    bot_token: str,
    allowed_user_id: int,
    route: str,
    store: CaptureStore,
    coordinator: ProcessingCoordinator,
    *,
    grouping_max_gap_seconds: float = CAPTURE_GROUP_MAX_GAP_SECONDS,
    grouping_settle_seconds: float = CAPTURE_GROUP_SETTLE_SECONDS,
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
    # Which route this bot captures into. Read wherever a store call needs it, and
    # what makes the per-Application debounce dict route-scoped for free.
    application.bot_data["route"] = route
    application.bot_data["allowed_user_id"] = allowed_user_id
    application.bot_data["capture_group_max_gap_seconds"] = grouping_max_gap_seconds
    application.bot_data["capture_group_settle_seconds"] = grouping_settle_seconds
    application.add_handler(
        MessageHandler(filters.UpdateType.MESSAGE & ~filters.COMMAND, handle_new_message)
    )
    application.add_handler(
        MessageHandler(
            filters.UpdateType.EDITED_MESSAGE & ~filters.COMMAND,
            handle_edited_message,
        )
    )
    return application
