"""Telegram capture, media download, albums, and optional labels."""

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .models import CATEGORIES, AttachmentSpec, DownloadedAttachment
from .processing import ProcessingCoordinator
from .storage import CaptureStore

MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
MEDIA_GROUP_SETTLE_SECONDS = 1.0
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
        coordinator: ProcessingCoordinator = application.bot_data["coordinator"]
        bundle = store.media_group_capture(chat_id, media_group_id)
        if bundle is None:
            return
        attachments = await download_attachments(application.bot, bundle["specs"])
        item = store.capture(
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
        coordinator.submit(item)
        if item.category or item.already_known:
            return
        warnings = [value.warning for value in attachments if value.warning]
        text = "Saved."
        if warnings:
            text += (
                " Some attachments could not be downloaded; "
                "details are in metadata.json."
            )
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
    coordinator: ProcessingCoordinator = context.application.bot_data["coordinator"]
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
        item = store.capture(
            message.chat_id,
            message.message_id,
            content,
            edited_at=edited_at,
            received_at=message.date.isoformat(),
            telegram_payload=raw_payload,
            attachments=attachments,
        )
        coordinator.submit(item)
    except Exception:
        logger.exception("Failed to capture Telegram message")
        if not is_edit:
            await message.reply_text("Could not save this item. Check the server log.")
        return

    if item.category:
        logger.info("Updated Telegram message %s", message.message_id)
        return
    if not item.already_known:
        warnings = [value.warning for value in attachments if value.warning]
        prefix = "Saved."
        if warnings:
            prefix += (
                " Some attachments could not be downloaded; "
                "details are in metadata.json."
            )
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
        if category not in CATEGORIES or query.message is None:
            raise ValueError
        chat_id = query.message.chat.id
    except ValueError:
        await query.answer("Invalid category button", show_alert=True)
        return

    store: CaptureStore = context.application.bot_data["store"]
    coordinator: ProcessingCoordinator = context.application.bot_data["coordinator"]
    try:
        item = store.categorize(chat_id, message_id, category)
        coordinator.submit(item)
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
        .post_init(recover_pending_media_groups)
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
    application.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^label\|"))
    return application
