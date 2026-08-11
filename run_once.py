"""Одноразовая проверка Telegram-каналов для GitHub Actions.

Не изменяет SQLite и не вмешивается в постоянный main.py.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any, Callable

from config import Settings, load_settings
from filters import FilterResult, evaluate
from telegram_runtime import session_for_settings
from application_method import detect_application, format_application_block
from work_metadata import analyze_work_metadata
from recipients import send_to_recipients


STATE_PATH = Path(__file__).resolve().parent / "state" / "last_seen.json"


def configure_logging(level: str) -> None:
    """Настраивает единственный консольный handler до любой ветки запуска."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not root.handlers:
        logging.basicConfig(
            level=root.level,
            format="%(asctime)s %(levelname)s %(message)s",
        )


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
    application = detect_application(text, "Telegram", link)
    headings = {"direct_order": "3D-ЗАКАЗ", "freelance_vacancy": "3D-КОНТРАКТ / ФРИЛАНС", "job_vacancy": "3D-ВАКАНСИЯ"}
    metadata = analyze_work_metadata(text)
    preview = text[:600].rstrip() + ("…" if len(text) > 600 else "")
    payment = {"crypto_explicit": "КРИПТА", "fiat_explicit": "ФИАТ", "mixed": "КРИПТА / ФИАТ", "unknown": "НЕ УКАЗАН"}[metadata.payment_method]
    blocks = [f"<b>{headings[result.category]}</b>", f"<b>{html.escape(title).upper()}</b>", f"<b>ОПЛАТА</b>\n{html.escape(result.price)}" if result.price != "не указана" else None, f"<b>РАБОТА ИЗ РОССИИ</b>\n{'ДА' if metadata.russia_eligibility == 'allowed' else 'НЕИЗВЕСТНО'}", f"<b>СПОСОБ ПОЛУЧЕНИЯ ОПЛАТЫ</b>\n{payment}", f"<b>ОПИСАНИЕ</b>\n\n<blockquote>{html.escape(preview)}</blockquote>" if preview else None, f"<b>КАК ОТКЛИКНУТЬСЯ</b>\n{format_application_block(application, link)}", f"<b>ИСТОЧНИК</b>\n{html.escape(source.name)}"]
    return "\n\n".join(block for block in blocks if block)


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
            if analyze_work_metadata(text).russia_eligibility == "blocked":
                result = FilterResult("rejected", "работа явно недоступна из России", result.price)
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
    configure_logging(settings.log_level)
    state = LastSeenState()
    client = TelegramClient(session_for_settings(settings), settings.api_id, settings.api_hash)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("Аккаунт-сборщик не авторизован")
        async def send(card: str) -> None:
            if not await send_to_recipients(bot, settings.recipient_chat_ids, card):
                raise RuntimeError("Уведомление не доставлено ни одному получателю")
        total = await process_sources(load_sources(settings.sources_path), client, state, settings, send)
        logging.info("Одноразовая проверка завершена: обработано сообщений %s", total)
    finally:
        await client.disconnect()
        await bot.session.close()


async def run_himalayas_diagnostic(settings: Settings, *, provider: Any = None, state: Any = None) -> None:
    """Read-only Himalayas diagnostic with visible markers and fail-fast errors."""
    from external_sources import ExternalState, HimalayasSource
    from external_sources.base import process_external_provider

    async def no_send(_: str) -> None:
        raise RuntimeError("Himalayas diagnostic must not send notifications")

    logging.info("Himalayas diagnostic mode: START")
    logging.info("Himalayas diagnostic: fetching public jobs...")
    try:
        await process_external_provider(
            provider or HimalayasSource(settings.himalayas_poll_interval_minutes),
            state or ExternalState(),
            settings,
            no_send,
            diagnostic=True,
        )
    except Exception:
        logging.exception("Himalayas diagnostic mode: ERROR")
        raise
    logging.info("Himalayas diagnostic mode: DONE")


def select_external_providers(settings: Any, mcp_factory: Any, himalayas_factory: Any, jobicy_factory: Any, remotive_factory: Any) -> list[Any]:
    providers = []
    if settings.himalayas_mcp_enabled:
        providers.append(mcp_factory(settings.himalayas_mcp_poll_minutes))
    if settings.himalayas_enabled:
        providers.append(himalayas_factory(settings.himalayas_poll_interval_minutes))
    if settings.jobicy_enabled:
        providers.append(jobicy_factory(settings.jobicy_poll_interval_minutes))
    if settings.remotive_enabled:
        providers.append(remotive_factory(settings.remotive_poll_interval_minutes))
    return providers


async def run_once() -> None:
    """Telegram и внешние доски изолированы: сбой одного контура не останавливает другой."""
    try:
        settings = load_settings()
    except Exception:
        configure_logging("INFO")
        logging.exception("Job Radar startup configuration error")
        raise
    configure_logging(settings.log_level)
    if settings.himalayas_diagnostic:
        await run_himalayas_diagnostic(settings)
        return

    try:
        await run_telegram_once()
    except Exception:
        logging.exception("Проверка Telegram завершилась ошибкой; внешние источники продолжат работу")

    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from external_sources import ExternalState, HimalayasMcpSource, HimalayasSource, JobicySource, RemotiveSource, process_external_sources

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        async def send(card: str) -> None:
            if not await send_to_recipients(bot, settings.recipient_chat_ids, card):
                raise RuntimeError("Уведомление не доставлено ни одному получателю")

        providers = select_external_providers(
            settings, HimalayasMcpSource, HimalayasSource, JobicySource, RemotiveSource,
        )
        total = await process_external_sources(
            providers, ExternalState(), settings, send,
            himalayas_recovery=settings.himalayas_recovery_backfill,
        )
        logging.info("Проверка внешних источников завершена: отправлено %s", total)
    except Exception:
        logging.exception("Ошибка внешнего контура не влияет на Telegram")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run_once())
