#!/usr/bin/env python3
"""
Minimal Telegram capture bot.

Share or forward anything to the bot; it replies with category buttons and
logs the labeled item to captures.jsonl. No ingestion into org files, no LLM
calls — that's a later step. See README.md for setup.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

CATEGORIES = ["ML", "Career", "Life", "Other"]

BASE_DIR = Path(__file__).resolve().parent
CAPTURES_FILE = BASE_DIR / "captures.jsonl"
URL_RE = re.compile(r"https?://\S+")

load_dotenv(BASE_DIR / ".env")
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USER_ID = int(os.environ["ALLOWED_USER_ID"])

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("capture_bot")

# message_id -> {"text", "url", "chat_id", "timestamp"}, cleared once labeled.
# Lost on restart — an unlabeled item just needs to be resent. Fine for this MVP.
pending = {}


def extract_url(text):
    if not text:
        return None
    match = URL_RE.search(text)
    return match.group(0) if match else None


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user is None or user.id != ALLOWED_USER_ID:
        logger.info("Ignored message from unauthorized user %s", user.id if user else "unknown")
        return

    message = update.effective_message
    text = message.text or message.caption

    pending[message.message_id] = {
        "text": text,
        "url": extract_url(text),
        "chat_id": message.chat_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    keyboard = [
        [InlineKeyboardButton(category, callback_data=f"label|{message.message_id}|{category}")]
        for category in CATEGORIES
    ]
    await message.reply_text("Label this:", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    await query.answer()
    if user is None or user.id != ALLOWED_USER_ID:
        return

    try:
        _, message_id_str, category = query.data.split("|", 2)
        message_id = int(message_id_str)
    except ValueError:
        await query.edit_message_text("Couldn't read that button — please resend the item.")
        return

    item = pending.pop(message_id, None)
    if item is None:
        await query.edit_message_text(
            "This capture expired (bot probably restarted) — please resend it.",
            reply_markup=None,
        )
        return

    record = {
        "timestamp": item["timestamp"],
        "text": item["text"],
        "url": item["url"],
        "label": category,
        "telegram_message_id": message_id,
        "telegram_chat_id": item["chat_id"],
    }
    with open(CAPTURES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    await query.edit_message_text(f"✅ Saved as: {category}", reply_markup=None)


def main():
    # Python 3.14 no longer creates an event loop implicitly for the main
    # thread. python-telegram-bot's synchronous run_polling() API still
    # obtains the current loop, so install one before starting it.
    asyncio.set_event_loop(asyncio.new_event_loop())

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Bot started, polling...")
    application.run_polling()


if __name__ == "__main__":
    main()
