"""Локальный запуск только ассистента комментариев Threads.

Этот процесс нужен на домашнем ПК, где доступна Ollama по localhost.
Основной Job Radar при этом может продолжать работать через GitHub Actions.
"""
from __future__ import annotations

import asyncio
import logging
import signal

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import load_settings
from database import Database
from threads_comment_assistant import ThreadsCommentAssistant, register_threads_comment_handlers


def install_shutdown_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def stop() -> None:
        logging.info("Получен сигнал завершения")
        stop_event.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(signum, lambda *_: stop())


async def run() -> None:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")

    if not settings.threads_comments_enabled:
        raise RuntimeError("THREADS_COMMENTS_ENABLED=false. Включите ассистент в .env.")
    if not settings.threads_access_token:
        raise RuntimeError("THREADS_ACCESS_TOKEN не задан в .env.")

    db = Database(settings.database_path)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    assistant = ThreadsCommentAssistant(settings, db, bot)
    register_threads_comment_handlers(dp, assistant, settings)

    stop_event = asyncio.Event()
    install_shutdown_handlers(stop_event)

    tasks = [
        asyncio.create_task(dp.start_polling(bot, handle_signals=False)),
        asyncio.create_task(assistant.run(stop_event)),
        asyncio.create_task(stop_event.wait()),
    ]

    try:
        logging.info(
            "Локальный Threads-комментатор запущен. Модель=%s, Ollama=%s",
            settings.ollama_model,
            settings.ollama_base_url,
        )
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task is not tasks[2] and not task.cancelled() and task.exception():
                raise task.exception()
    finally:
        stop_event.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await bot.session.close()
        db.close()


if __name__ == "__main__":
    asyncio.run(run())
