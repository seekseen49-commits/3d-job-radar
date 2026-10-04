"""Локальный запуск только ассистента комментариев Threads.

Нужны THREADS_COMMENT_BOT_TOKEN (или BOT_TOKEN как запасной вариант), OWNER_CHAT_ID и THREADS_ACCESS_TOKEN.
Основной Job Radar может продолжать работать через GitHub Actions.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

from database import Database
from threads_comment_assistant import ThreadsCommentAssistant, register_threads_comment_handlers


BASE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class LocalThreadsSettings:
    bot_token: str
    owner_chat_id: int
    database_path: Path
    log_level: str
    threads_access_token: str
    threads_comments_enabled: bool
    threads_comment_queries: tuple[str, ...]
    threads_comment_scan_minutes: int
    threads_comment_own_username: str | None
    ollama_base_url: str
    ollama_model: str
    ollama_vision_model: str | None


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"В .env не задано обязательное значение {name}")
    return value


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default
    return value if value > 0 else default


def _csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    values = tuple(part.strip() for part in raw.split(",") if part.strip())
    return values or tuple(part.strip() for part in default.split(",") if part.strip())


def load_local_settings() -> LocalThreadsSettings:
    load_dotenv(BASE_DIR / ".env", override=True)
    try:
        owner_chat_id = int(_required("OWNER_CHAT_ID"))
    except ValueError as exc:
        raise ValueError("OWNER_CHAT_ID должен быть целым числом") from exc

    database_name = os.getenv("THREADS_COMMENT_DATABASE_PATH", "threads_comments.sqlite3").strip() or "threads_comments.sqlite3"
    database_path = Path(database_name)
    if not database_path.is_absolute():
        database_path = BASE_DIR / database_path

    return LocalThreadsSettings(
        bot_token=(os.getenv("THREADS_COMMENT_BOT_TOKEN", "").strip() or _required("BOT_TOKEN")),
        owner_chat_id=owner_chat_id,
        database_path=database_path,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        threads_access_token=_required("THREADS_ACCESS_TOKEN"),
        threads_comments_enabled=_bool("THREADS_COMMENTS_ENABLED", True),
        threads_comment_queries=_csv(
            "THREADS_COMMENT_QUERIES",
            "фриланс,заказчик,клиент,монтаж,видеомонтаж,Blender,3D,3д,game dev,Unreal Engine,motion design,After Effects,портфолио,3D printing,нейросети,AI,freelance,video editing,client work,creative work",
        ),
        threads_comment_scan_minutes=max(_positive_int("THREADS_COMMENT_SCAN_MINUTES", 30), 15),
        threads_comment_own_username=os.getenv("THREADS_COMMENT_OWN_USERNAME", "").strip().lstrip("@") or None,
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen3:4b").strip() or "qwen3:4b",
        ollama_vision_model=os.getenv("OLLAMA_VISION_MODEL", "qwen3-vl:4b").strip() or None,
    )


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
    settings = load_local_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")

    if not settings.threads_comments_enabled:
        raise RuntimeError("THREADS_COMMENTS_ENABLED=false. Включите ассистент в .env.")

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
            "Локальный Threads-комментатор запущен. Модель=%s, vision=%s, Ollama=%s",
            settings.ollama_model,
            settings.ollama_vision_model or "off",
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
