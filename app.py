"""Minimal Telegram-to-Markdown capture service."""

import asyncio
import json
import logging
import os
import sqlite3
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

CATEGORIES = ("ML", "Career", "Life", "Other")
BASE_DIR = Path(__file__).resolve().parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("info_triage")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def render_message(category: str, content: str) -> str:
    return f"---\ncategory: {category}\n---\n\n{content}"


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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    short_text TEXT,
                    error TEXT,
                    PRIMARY KEY (chat_id, message_id)
                )
                """
            )

    @staticmethod
    def item_name(chat_id: int, message_id: int) -> str:
        return f"{chat_id}_{message_id}"

    def get_item(self, chat_id: int, message_id: int):
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM items WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id),
            ).fetchone()

    def _save_state(
        self,
        chat_id: int,
        message_id: int,
        status: str,
        category: str | None,
        created_at: str,
        content: str,
        error: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO items (
                    chat_id, message_id, status, category, created_at,
                    updated_at, short_text, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    status = excluded.status,
                    category = excluded.category,
                    updated_at = excluded.updated_at,
                    short_text = excluded.short_text,
                    error = excluded.error
                """,
                (
                    chat_id,
                    message_id,
                    status,
                    category,
                    created_at,
                    now_iso(),
                    " ".join(content.split())[:160],
                    error,
                ),
            )

    def capture(
        self,
        chat_id: int,
        message_id: int,
        content: str,
        edited_at: str | None = None,
    ) -> tuple[bool, str | None]:
        """Capture or update a message. Return (already_known, category)."""
        existing = self.get_item(chat_id, message_id)
        already_known = existing is not None
        category = existing["category"] if existing else None
        created_at = existing["created_at"] if existing else now_iso()
        item_name = self.item_name(chat_id, message_id)
        staging_path = self.staging_dir / item_name
        inbox_path = self.inbox_dir / item_name

        if inbox_path.exists():
            if staging_path.exists():
                raise RuntimeError(f"Both staging and inbox contain {item_name}")
            inbox_path.rename(staging_path)
        else:
            staging_path.mkdir(exist_ok=True)

        message_content = render_message(category, content) if category else content
        atomic_write(staging_path / "message.md", message_content)

        metadata = {
            "chat_id": chat_id,
            "message_id": message_id,
            "received_at": created_at,
            "edited_at": edited_at,
        }
        atomic_write(
            staging_path / "metadata.json",
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        )
        self._save_state(chat_id, message_id, "received", category, created_at, content)

        if category:
            self._promote(chat_id, message_id, category, created_at, content)

        return already_known, category

    def categorize(self, chat_id: int, message_id: int, category: str) -> None:
        if category not in CATEGORIES:
            raise ValueError(f"Unknown category: {category}")

        item = self.get_item(chat_id, message_id)
        staging_path = self.staging_dir / self.item_name(chat_id, message_id)
        if item is None or not staging_path.is_dir():
            raise FileNotFoundError("Capture is no longer waiting for a category")

        content = (staging_path / "message.md").read_text(encoding="utf-8")
        atomic_write(staging_path / "message.md", render_message(category, content))
        self._save_state(
            chat_id,
            message_id,
            "received",
            category,
            item["created_at"],
            content,
        )
        self._promote(chat_id, message_id, category, item["created_at"], content)

    def _promote(
        self,
        chat_id: int,
        message_id: int,
        category: str,
        created_at: str,
        content: str,
    ) -> None:
        item_name = self.item_name(chat_id, message_id)
        staging_path = self.staging_dir / item_name
        inbox_path = self.inbox_dir / item_name

        try:
            self._save_state(
                chat_id,
                message_id,
                "processing",
                category,
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
                created_at,
                content,
            )
        except Exception as error:
            self._save_state(
                chat_id,
                message_id,
                "failed",
                category,
                created_at,
                content,
                str(error),
            )
            raise


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/health"):
            self.send_error(404)
            return

        body = b"Info Triage is running\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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

    content = message.text if message.text is not None else message.caption
    existing = store.get_item(message.chat_id, message.message_id)
    if content is None and not (is_edit and existing is not None):
        if not is_edit:
            await message.reply_text(
                "For now, Info Triage supports text and captions only."
            )
        return
    content = content or ""

    edited_at = None
    if is_edit:
        edit_date = message.edit_date or datetime.now(UTC)
        edited_at = edit_date.isoformat()

    try:
        already_known, category = store.capture(
            message.chat_id,
            message.message_id,
            content,
            edited_at,
        )
    except Exception:
        logger.exception("Failed to capture Telegram message")
        if not is_edit:
            await message.reply_text("Could not save this item. Check the server log.")
        return

    if category:
        logger.info(
            "Updated ready item %s",
            store.item_name(message.chat_id, message.message_id),
        )
        return

    if not already_known:
        await message.reply_text(
            "Label this:", reply_markup=category_keyboard(message.message_id)
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
            "This capture is no longer waiting for a category.", reply_markup=None
        )
        return
    except Exception:
        logger.exception("Failed to categorize Telegram message")
        await query.answer("Could not save this item", show_alert=True)
        return

    await query.answer()
    await query.edit_message_text(f"✅ Saved as: {category}", reply_markup=None)


def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    allowed_user_id = int(os.environ["ALLOWED_USER_ID"])
    data_dir = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
    port = int(os.environ.get("PORT", "8000"))

    store = CaptureStore(data_dir)
    health_server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    health_thread = threading.Thread(target=health_server.serve_forever, daemon=True)
    health_thread.start()
    logger.info("Health server listening on port %s", port)

    asyncio.set_event_loop(asyncio.new_event_loop())
    application = Application.builder().token(bot_token).build()
    application.bot_data["store"] = store
    application.bot_data["allowed_user_id"] = allowed_user_id

    supported_messages = filters.TEXT | filters.CAPTION
    application.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & supported_messages & ~filters.COMMAND,
            handle_new_message,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.UpdateType.EDITED_MESSAGE & supported_messages,
            handle_edited_message,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & ~supported_messages & ~filters.COMMAND,
            handle_new_message,
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
