"""Runtime entry point for Info Triage."""

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update

from info_triage.config import load_config
from info_triage.preprocessing import processing_steps_from_config
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
    config_path = Path(os.environ.get("INFO_TRIAGE_CONFIG", BASE_DIR / "config.yaml"))
    config = load_config(config_path)

    store = CaptureStore(config.data_dir)
    pipeline = ProcessingPipeline(processing_steps_from_config(config))
    worker = ProcessingWorker(store, pipeline)
    coordinator = ProcessingCoordinator(store, pipeline, worker)
    asyncio.set_event_loop(asyncio.new_event_loop())
    application = build_application(
        bot_token,
        allowed_user_id,
        store,
        coordinator,
        grouping_max_gap_seconds=config.grouping_max_gap_seconds,
        grouping_settle_seconds=config.grouping_settle_seconds,
    )
    web_server, web_thread = start_web_server(store, config.web_port)
    worker.start()
    logger.info("Web server listening on port %s", config.web_port)
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
