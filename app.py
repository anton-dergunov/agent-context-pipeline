"""Runtime entry point for Info Triage."""

import asyncio
import logging
import os
import signal
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.error import InvalidToken
from telegram.ext import Application

from info_triage.config import AppConfig, load_config
from info_triage.preprocessing import processing_steps_for_route
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


def _read_tokens(config: AppConfig) -> dict[str, str]:
    """Read every route's bot token before any bot starts.

    All at once, and fatally: a route whose bot never polls swallows everything
    sent to it silently, and the whole point of the capture layer is that nothing
    sent is ever lost. A container that will not start is the loud failure.
    """
    tokens = {}
    for route in config.routes:
        token = os.environ.get(route.token_env, "").strip()
        if not token:
            raise SystemExit(f"Route {route.name}: {route.token_env} is not set in .env")
        tokens[route.name] = token
    return tokens


async def _serve(
    config: AppConfig,
    tokens: dict[str, str],
    allowed_user_id: int,
    store: CaptureStore,
    coordinator: ProcessingCoordinator,
) -> None:
    """Poll every route's bot in one event loop until a signal arrives.

    `run_polling` owns the loop and blocks, so its sequence is replicated here for
    each application: initialize, post_init, start polling, start.
    """
    applications = [
        build_application(
            tokens[route.name],
            allowed_user_id,
            route.name,
            store,
            coordinator,
            grouping_max_gap_seconds=config.grouping_max_gap_seconds,
            grouping_settle_seconds=config.grouping_settle_seconds,
        )
        for route in config.routes
    ]

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for received in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(received, stop.set)

    initialized: list[Application] = []
    started: list[Application] = []
    try:
        for route, application in zip(config.routes, applications, strict=True):
            try:
                await application.initialize()
            except InvalidToken:
                # Never re-raise this one: its message quotes the token itself.
                raise SystemExit(
                    f"Route {route.name}: the token in {route.token_env} was rejected"
                ) from None
            initialized.append(application)
            if application.post_init is not None:
                await application.post_init(application)
            await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            await application.start()
            started.append(application)
            logger.info("Route %s polling as bot %s", route.name, application.bot.id)
        await stop.wait()
    finally:
        for application in reversed(started):
            if application.updater is not None and application.updater.running:
                await application.updater.stop()
            if application.running:
                await application.stop()
        for application in reversed(initialized):
            await application.shutdown()


def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    allowed_user_id = int(os.environ["ALLOWED_USER_ID"])
    config_path = Path(os.environ.get("INFO_TRIAGE_CONFIG", BASE_DIR / "config.yaml"))
    config = load_config(config_path)
    tokens = _read_tokens(config)
    capture_token = os.environ.get(config.capture_token_env, "").strip()
    if not capture_token:
        raise SystemExit(f"{config.capture_token_env} is not set in .env")

    routes = tuple(route.name for route in config.routes)
    store = CaptureStore(config.data_dir, routes)
    pipelines = {
        route.name: ProcessingPipeline(processing_steps_for_route(config, route))
        for route in config.routes
    }
    worker = ProcessingWorker(store, pipelines)
    coordinator = ProcessingCoordinator(store, pipelines, worker)
    web_server, web_thread = start_web_server(
        store,
        coordinator,
        config.web_port,
        capture_token=capture_token,
        routes=routes,
    )
    worker.start()
    logger.info("Web server listening on port %s", config.web_port)
    logger.info("Polling %d Telegram bots: %s", len(routes), ", ".join(routes))
    try:
        asyncio.run(_serve(config, tokens, allowed_user_id, store, coordinator))
    finally:
        worker.stop()
        web_server.shutdown()
        web_server.server_close()
        web_thread.join(timeout=5)


if __name__ == "__main__":
    main()
