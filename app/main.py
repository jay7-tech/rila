"""Entry point. One codebase, several roles that can run as separate containers.

    python -m app.main migrate  apply database migrations, then exit
    python -m app.main api      web app + REST API
    python -m app.main bot      Telegram bot (long polling)
    python -m app.main worker   reel processing (run several for more throughput)
    python -m app.main all      everything in one process (laptop / small servers)
"""
from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv

from .api import create_app
from .config import get_settings
from .db import init_db

log = logging.getLogger("rilla")
ROLES = ("migrate", "api", "bot", "worker", "all")


def _warn_config(role: str) -> None:
    s = get_settings()
    if role in ("bot", "all") and not s.bot_token:
        log.warning("BOT_TOKEN is not set: the Telegram bot will not run")
    if role in ("worker", "all") and not s.groq_api_key:
        log.warning("GROQ_API_KEY is not set: reels cannot be read until it is added")


def _worker_notifier(token: str):
    """Send job results to Telegram without needing the polling bot in this process."""
    if not token:
        return None
    from telegram import Bot

    from .formatting import saved_summary

    bot = Bot(token)

    async def notify(job, summary: dict) -> None:
        await bot.send_message(chat_id=job.chat_id, text=saved_summary(summary))

    return notify


async def run_bot(token: str) -> None:
    from .bot import build_application

    application = build_application(token)
    await application.initialize()
    await application.start()
    await application.updater.start_polling(allowed_updates=["message", "edited_message"])
    log.info("Telegram bot is polling")
    try:
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


def make_lifespan(role: str):
    @asynccontextmanager
    async def lifespan(app):
        from .services.worker import worker_loop

        s = get_settings()
        init_db(s.database_url, migrate=s.run_migrations)
        tasks = []
        if role == "all":
            if s.bot_token:
                tasks.append(asyncio.create_task(run_bot(s.bot_token)))
            tasks.append(asyncio.create_task(worker_loop(_worker_notifier(s.bot_token))))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    return lifespan


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore"):  # their INFO lines contain the bot token in the URL
        logging.getLogger(noisy).setLevel(logging.WARNING)
    role = sys.argv[1] if len(sys.argv) > 1 else "all"
    if role not in ROLES:
        raise SystemExit(f"usage: python -m app.main [{'|'.join(ROLES)}]")
    s = get_settings()
    _warn_config(role)

    if role == "migrate":
        init_db(s.database_url, migrate=True)
        log.info("Database is up to date")
        return

    if role in ("api", "all"):
        log.info("Open the app at %s (port %s)", s.public_url, s.port)
        uvicorn.run(create_app(make_lifespan(role)), host="0.0.0.0", port=s.port, log_level="info")
        return

    init_db(s.database_url, migrate=s.run_migrations)
    if role == "bot":
        if not s.bot_token:
            raise SystemExit("BOT_TOKEN is required for the bot role")
        asyncio.run(run_bot(s.bot_token))
    else:
        from .services.worker import worker_loop

        asyncio.run(worker_loop(_worker_notifier(s.bot_token)))


if __name__ == "__main__":
    main()
