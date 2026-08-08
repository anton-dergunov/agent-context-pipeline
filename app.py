"""Minimal Telegram-to-Markdown capture service."""

import asyncio
import html
import json
import logging
import os
import sqlite3
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

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
    ) -> None:
        atomic_write(item_path / "message.md", render_message(category, content))
        metadata = {
            "chat_id": chat_id,
            "message_id": message_id,
            "received_at": received_at,
            "edited_at": edited_at,
            "category": category,
            "revision": revision,
        }
        atomic_write(
            item_path / "metadata.json",
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        )

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
    ) -> tuple[bool, str | None]:
        """Capture or update a message. Return (already_known, category)."""
        existing = self.get_item(chat_id, message_id)
        already_known = existing is not None
        category = existing["category"] if existing else None
        revision = existing["revision"] if existing else 1
        if existing and edited_at is not None:
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

        return already_known, category

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
            edited_at=edited_at,
            received_at=message.date.isoformat(),
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
        await message.reply_text(
            "Saved. Optional label:",
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
