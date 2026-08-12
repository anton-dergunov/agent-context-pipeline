"""Runtime entry point for Info Triage."""

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update

from info_triage.preprocessing import VoiceTranscriptionStep
from info_triage.processing import (
    ProcessingCoordinator,
    ProcessingPipeline,
    ProcessingWorker,
)
from info_triage.storage import CaptureStore
from info_triage.telegram_bot import build_application
from info_triage.web import start_web_server

BASE_DIR = Path(__file__).resolve().parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("info_triage")
# Telegram request URLs contain the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    allowed_user_id = int(os.environ["ALLOWED_USER_ID"])
    data_dir = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
    transcription_model_cache = Path(
        os.environ.get(
            "INSTAGRAM_TRANSCRIPTION_MODEL_CACHE_DIR",
            BASE_DIR / ".whisper_models",
        )
    )
    port = int(os.environ.get("PORT", "8000"))

    store = CaptureStore(data_dir)
    pipeline = ProcessingPipeline([VoiceTranscriptionStep(transcription_model_cache)])
    worker = ProcessingWorker(store, pipeline)
    coordinator = ProcessingCoordinator(store, pipeline, worker)
    asyncio.set_event_loop(asyncio.new_event_loop())
    application = build_application(
        bot_token,
        allowed_user_id,
        store,
        coordinator,
    )
    web_server, web_thread = start_web_server(store, port)
    worker.start()
    logger.info("Web server listening on port %s", port)
    logger.info("Telegram bot started, polling...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        worker.stop()
        web_server.shutdown()
        web_server.server_close()
        web_thread.join(timeout=5)


if __name__ == "__main__":
    main()
