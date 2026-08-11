"""Одноразовая проверка Telegram-каналов для GitHub Actions.

Не изменяет SQLite и не вмешивается в постоянный main.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any, Callable

from config import Settings, load_settings
from filters import FilterResult, evaluate
from telegram_runtime import session_for_settings


STATE_PATH = Path(__file__).resolve().parent / "state" / "last_seen.json"


@dataclass(frozen=True)
class Source:
    channel_id: int
    name: str
    username: str | None
    mode: str
    source_type: str


class LastSeenState:
    def __init__(self, path: Path = STATE_PATH) -> None:
        self.path = path
        try:
            self.values: dict[str, int] = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.values = {}

    def get(self, channel_id: int) -> int | None:
        return self.values.get(str(channel_id))

    def set(self, channel_id: int, message_id: int) -> None:
        self.values[str(channel_id)] = message_id

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.values, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)


def load_sources(path: Path) -> list[Source]:
    data = json.loads(path.read_text(encoding="utf-8"))
    sources: list[Source] = []
    for item in data:
        if item.get("enabled") is not True:
            continue
        channel_id, mode = item.get("channel_id"), item.get("mode")
        source_type = item.get("source_type", "job_board")
        if isinstance(channel_id, int) and channel_id != 0 and mode in {"general", "3d_only"} and source_type in {"mixed", "job_board"}:
            sources.append(Source(channel_id, str(item.get("name") or channel_id), item.get("username"), mode, source_type))
        else:
            logging.warning("Пропущен некорректный источник %r", channel_id)
    return sources


def message_link(source: Source, message_id: int) -> str | None:
    if source.username:
        return f"https://t.me/{source.username.lstrip('@')}/{message_id}"
    id_text = str(source.channel_id)
    return f"https://t.me/c/{id_text[4:]}/{message_id}" if id_text.startswith("-100") else None


def format_card(source: Source, text: str, result: FilterResult, date: Any, message_id: int) -> str:
    title = next((line.strip() for line in text.splitlines() if line.strip()), "Публикация без текста")[:200]
    date_text = date.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC") if date else "неизвестна"
    link = message_link(source, message_id)
    headings = {
        "direct_order": "🔥 НАЙДЕН ПРЯМОЙ 3D-ЗАКАЗ",
        "freelance_vacancy": "Контрактная 3D-вакансия",
        "job_vacancy": "Вакансия по 3D",
    }
    link_text = f"\nСсылка: {link}" if link else ""
    return (
        f"<b>{headings[result.category]}</b>\n<b>Название:</b> {title}\n"
        f"<b>Канал:</b> {source.name}\n<b>Цена:</b> {result.price}\n"
        f"<b>Причина:</b> {result.reason}\n<b>Дата:</b> {date_text}{link_text}\n\n"
        f"<b>Исходный текст:</b>\n{text or '—'}"
    )


def should_send(result: FilterResult, settings: Settings) -> bool:
    return result.category == "direct_order" or (
        result.category == "freelance_vacancy" and settings.send_freelance_vacancies
    ) or (result.category == "job_vacancy" and settings.send_job_vacancies)


async def process_source(
    source: Source,
    client: Any,
    state: LastSeenState,
    settings: Settings,
    send: Callable[[str], Any],
    evaluator: Callable[[str, str, str], FilterResult] = evaluate,
) -> int:
    """Обрабатывает один канал. Возвращает число новых успешно обработанных сообщений."""
    last_seen = state.get(source.channel_id)
    if last_seen is None:
        latest_id = None
        async for message in client.iter_messages(source.channel_id, limit=1):
            latest_id = message.id
            break
        if latest_id is not None:
            state.set(source.channel_id, latest_id)
            state.save()
        return 0

    processed = 0
    async for message in client.iter_messages(source.channel_id, min_id=last_seen, reverse=True):
        message_id = getattr(message, "id", None)
        if not isinstance(message_id, int) or message_id <= last_seen:
            continue
        text = getattr(message, "raw_text", "") or ""
        try:
            result = evaluator(text, source.mode, source.source_type)
            if should_send(result, settings):
                await send(format_card(source, text, result, getattr(message, "date", None), message_id))
        except Exception:
            logging.exception("Сообщение %s/%s не обработано; состояние не изменено", source.channel_id, message_id)
            break
        state.set(source.channel_id, message_id)
        state.save()
        last_seen = message_id
        processed += 1
    return processed


async def process_sources(
    sources: list[Source], client: Any, state: LastSeenState, settings: Settings, send: Callable[[str], Any]
) -> int:
    total = 0
    for source in sources:
        try:
            total += await process_source(source, client, state, settings, send)
        except Exception:
            logging.exception("Ошибка проверки канала %s; остальные каналы продолжают проверяться", source.channel_id)
    return total


async def run_telegram_once() -> None:
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from telethon import TelegramClient

    settings = load_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    state = LastSeenState()
    client = TelegramClient(session_for_settings(settings), settings.api_id, settings.api_hash)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("Аккаунт-сборщик не авторизован")
        async def send(card: str) -> None:
            await bot.send_message(settings.owner_chat_id, card)
        total = await process_sources(load_sources(settings.sources_path), client, state, settings, send)
        logging.info("Одноразовая проверка завершена: обработано сообщений %s", total)
    finally:
        await client.disconnect()
        await bot.session.close()


async def run_once() -> None:
    """Telegram и внешние доски изолированы: сбой одного контура не останавливает другой."""
    try:
        await run_telegram_once()
    except Exception:
        logging.exception("Проверка Telegram завершилась ошибкой; внешние источники продолжат работу")

    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from external_sources import ExternalState, HimalayasSource, JobicySource, RemotiveSource, process_external_sources

    settings = load_settings()
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        async def send(card: str) -> None:
            await bot.send_message(settings.owner_chat_id, card)

        providers = []
        if settings.himalayas_enabled:
            providers.append(HimalayasSource(settings.himalayas_poll_interval_minutes))
        if settings.jobicy_enabled:
            providers.append(JobicySource(settings.jobicy_poll_interval_minutes))
        if settings.remotive_enabled:
            providers.append(RemotiveSource(settings.remotive_poll_interval_minutes))
        total = await process_external_sources(providers, ExternalState(), settings, send)
        logging.info("Проверка внешних источников завершена: отправлено %s", total)
    except Exception:
        logging.exception("Ошибка внешнего контура не влияет на Telegram")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run_once())
