"""Запуск: Telethon читает указанные каналы, aiogram уведомляет только владельца."""
from __future__ import annotations

import asyncio
import json
import logging
import signal
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ParseMode
from telethon import TelegramClient, events

from config import Settings, load_settings
from database import Database
from filters import FilterResult, evaluate
from telegram_runtime import connect_with_retry, run_channel_handler, session_for_settings, shutdown_resources
from recipients import send_to_recipients


@dataclass(frozen=True)
class Source:
    channel_id: int
    name: str
    username: str | None
    mode: str
    source_type: str


def load_sources(path: Path) -> dict[int, Source]:
    data = json.loads(path.read_text(encoding="utf-8"))
    sources: dict[int, Source] = {}
    for item in data:
        if item.get("enabled") is not True:
            continue
        channel_id = item.get("channel_id")
        if not isinstance(channel_id, int) or channel_id == 0:
            logging.warning("Пропущен источник с некорректным channel_id")
            continue
        mode = item.get("mode")
        if mode not in {"general", "3d_only"}:
            logging.warning("Пропущен источник %s с неизвестным mode", channel_id)
            continue
        source_type = item.get("source_type", "job_board")
        if source_type not in {"mixed", "job_board"}:
            logging.warning("Пропущен источник %s с неизвестным source_type", channel_id)
            continue
        sources[channel_id] = Source(channel_id, str(item.get("name") or channel_id), item.get("username"), mode, source_type)
    return sources


def message_link(source: Source, message_id: int) -> str | None:
    if source.username:
        return f"https://t.me/{source.username.lstrip('@')}/{message_id}"
    # Такая ссылка доступна участникам соответствующего приватного канала.
    id_text = str(source.channel_id)
    if id_text.startswith("-100"):
        return f"https://t.me/c/{id_text[4:]}/{message_id}"
    return None


def card(source: Source, text: str, result: FilterResult, date, message_id: int) -> str:
    title = next((line.strip() for line in text.splitlines() if line.strip()), "Публикация без текста")
    title = title[:200]
    date_text = date.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC") if date else "неизвестна"
    link = message_link(source, message_id)
    link_text = f"\nСсылка: {link}" if link else ""
    headings = {
        "direct_order": "🔥 НАЙДЕН ПРЯМОЙ 3D-ЗАКАЗ",
        "freelance_vacancy": "Контрактная 3D-вакансия",
        "job_vacancy": "Вакансия по 3D",
    }
    heading = headings[result.category]
    return (
        f"<b>{heading}</b>\n"
        f"<b>Название:</b> {title}\n"
        f"<b>Канал:</b> {source.name}\n"
        f"<b>Цена:</b> {result.price}\n"
        f"<b>Причина:</b> {result.reason}\n"
        f"<b>Дата:</b> {date_text}{link_text}\n\n"
        f"<b>Исходный текст:</b>\n{text or '—'}"
    )


def build_dispatcher(settings: Settings, db: Database, sources_path: Path) -> Dispatcher:
    dp = Dispatcher()

    def owner_only(message: Message) -> bool:
        return bool(message.chat and message.chat.id == settings.owner_chat_id)

    @dp.message(Command("start"))
    async def start(message: Message) -> None:
        await message.answer(f"Бот работает. Ваш chat_id: <code>{message.chat.id}</code>")

    @dp.message(Command("id"))
    async def show_chat_id(message: Message) -> None:
        await message.answer(f"Ваш Chat ID:\n<code>{message.chat.id}</code>")

    @dp.message(Command("status"))
    async def status(message: Message) -> None:
        if not owner_only(message): return
        state = "приостановлены" if db.notifications_paused() else "включены"
        await message.answer(f"Система запущена. Уведомления: {state}. Активных источников: {len(load_sources(sources_path))}.")

    @dp.message(Command("stats"))
    async def stats(message: Message) -> None:
        if not owner_only(message): return
        values = db.stats()
        await message.answer(f"Принято: {values['accepted']}\nОтклонено: {values['rejected']}\nОтправлено: {values['sent']}")

    @dp.message(Command("pause"))
    async def pause(message: Message) -> None:
        if not owner_only(message): return
        db.set_notifications_paused(True)
        await message.answer("Уведомления приостановлены. Сообщения всё равно учитываются, чтобы не было дублей.")

    @dp.message(Command("resume"))
    async def resume(message: Message) -> None:
        if not owner_only(message): return
        db.set_notifications_paused(False)
        await message.answer("Уведомления возобновлены.")

    @dp.message(Command("sources"))
    async def sources(message: Message) -> None:
        if not owner_only(message): return
        items = load_sources(sources_path).values()
        answer = "\n".join(f"• {s.name} — {s.mode} ({s.channel_id})" for s in items) or "Активных источников нет."
        await message.answer(answer)

    @dp.message(Command("test"))
    async def test(message: Message) -> None:
        if not owner_only(message): return
        demo = Source(0, "Тестовый канал", None, "general", "mixed")
        result = FilterResult("direct_order", "тестовая карточка", "не указана")
        await message.answer(card(demo, "Тест: требуется 3D-модель.", result, None, 0))
    return dp


def install_shutdown_handlers(stop_event: asyncio.Event) -> None:
    """SIGTERM приходит от systemd, SIGINT — от Ctrl+C при локальном запуске."""
    loop = asyncio.get_running_loop()
    def stop() -> None:
        logging.info("Получен сигнал завершения")
        stop_event.set()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(signum, lambda *_: stop())


async def run(stop_event: asyncio.Event | None = None) -> None:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    db = Database(settings.database_path)
    sources = load_sources(settings.sources_path)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher(settings, db, settings.sources_path)
    client = TelegramClient(session_for_settings(settings), settings.api_id, settings.api_hash)
    stop_event = stop_event or asyncio.Event()
    install_shutdown_handlers(stop_event)

    @client.on(events.NewMessage(chats=list(sources)))
    async def handle(event) -> None:
        async def process() -> None:
            channel_id = event.chat_id
            source = sources.get(channel_id)
            if source is None or db.is_processed(channel_id, event.id):
                return
            text = event.raw_text or ""
            result = evaluate(text, source.mode, source.source_type)
            db.record(channel_id, event.id, result.accepted)
            if not result.accepted:
                logging.info("Отклонено %s/%s: %s", channel_id, event.id, result.reason)
                return
            if result.category == "freelance_vacancy" and not settings.send_freelance_vacancies:
                return
            if result.category == "job_vacancy" and not settings.send_job_vacancies:
                return
            if db.notifications_paused():
                return
            await send_to_recipients(bot, settings.recipient_chat_ids, card(source, text, result, event.message.date, event.id))
            db.increment_sent()
        await run_channel_handler(process)

    tasks: list[asyncio.Task] = []
    try:
        if not await connect_with_retry(client, stop_event):
            return
        if not await client.is_user_authorized():
            raise RuntimeError("Аккаунт-сборщик не авторизован. Запустите authorize_telegram.py вручную.")
        logging.info("Запущено. Активных источников: %s", len(sources))
        tasks = [
            asyncio.create_task(dp.start_polling(bot, handle_signals=False)),
            asyncio.create_task(client.run_until_disconnected()),
            asyncio.create_task(stop_event.wait()),
        ]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task is not tasks[2] and not task.cancelled() and task.exception():
                raise task.exception()
    finally:
        await shutdown_resources(client=client, bot=bot, db=db, tasks=tasks)


if __name__ == "__main__":
    asyncio.run(run())
