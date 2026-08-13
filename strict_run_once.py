"""Отдельный запуск строгого русскоязычного Telegram-радара.

Файл не импортируется и не вызывается существующими ``main.py`` и
``run_once.py``. У него собственные источники и состояние, поэтому текущий
рабочий контур продолжает работать без изменений.
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from application_method import detect_application, format_application_block
from config import Settings, load_settings
from filters import FilterResult
from recipients import send_to_recipients
from strict_filters import evaluate_strict
from telegram_runtime import session_for_settings


BASE_DIR = Path(__file__).resolve().parent
STRICT_SOURCES_PATH = BASE_DIR / "strict_sources.json"
STRICT_STATE_PATH = BASE_DIR / "state" / "strict_last_seen.json"


@dataclass(frozen=True)
class StrictSource:
    entity: int | str
    name: str
    username: str | None
    mode: str
    source_type: str

    @property
    def key(self) -> str:
        return str(self.entity)


class StrictState:
    def __init__(self, path: Path = STRICT_STATE_PATH) -> None:
        self.path = path
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        self.last_seen: dict[str, int] = {
            str(key): value for key, value in data.get("last_seen", {}).items()
            if isinstance(value, int)
        }
        self.checked = int(data.get("checked", 0))
        self.accepted = int(data.get("accepted", 0))
        raw_reasons = data.get("rejected_by_reason", {})
        self.rejected_by_reason: Counter[str] = Counter({
            str(reason): int(count) for reason, count in raw_reasons.items()
            if isinstance(count, int) and count >= 0
        })

    def get(self, source: StrictSource) -> int | None:
        return self.last_seen.get(source.key)

    def set(self, source: StrictSource, message_id: int) -> None:
        self.last_seen[source.key] = message_id

    def record(self, result: FilterResult) -> None:
        self.checked += 1
        if result.accepted:
            self.accepted += 1
        else:
            self.rejected_by_reason[result.reason] += 1

    def save(self) -> None:
        payload = {
            "last_seen": self.last_seen,
            "checked": self.checked,
            "accepted": self.accepted,
            "rejected_by_reason": dict(self.rejected_by_reason.most_common()),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)


def load_strict_sources(path: Path = STRICT_SOURCES_PATH) -> list[StrictSource]:
    items = json.loads(path.read_text(encoding="utf-8"))
    sources: list[StrictSource] = []
    for item in items:
        if item.get("enabled") is not True:
            continue
        entity = item.get("channel")
        mode = item.get("mode")
        source_type = item.get("source_type")
        valid_entity = (isinstance(entity, int) and entity != 0) or (isinstance(entity, str) and bool(entity.strip()))
        if not valid_entity or mode not in {"general", "3d_only"} or source_type not in {"mixed", "job_board"}:
            logging.warning("Строгий источник пропущен: %r", entity)
            continue
        sources.append(StrictSource(entity, str(item.get("name") or entity), item.get("username"), mode, source_type))
    return sources


def message_link(source: StrictSource, message_id: int) -> str | None:
    if source.username:
        return f"https://t.me/{source.username.lstrip('@')}/{message_id}"
    entity = str(source.entity)
    return f"https://t.me/c/{entity[4:]}/{message_id}" if entity.startswith("-100") else None


def original_publication(message: Any) -> tuple[bool, datetime | None]:
    forward = getattr(message, "fwd_from", None)
    return (forward is not None, getattr(forward, "date", None) if forward is not None else None)


def evaluate_message(source: StrictSource, message: Any, *, now: datetime | None = None) -> FilterResult:
    text = getattr(message, "raw_text", "") or ""
    forwarded, original_date = original_publication(message)
    return evaluate_strict(
        text,
        source.mode,
        source.source_type,
        published_at=getattr(message, "date", None),
        now=now,
        source="Telegram",
        source_url=message_link(source, getattr(message, "id", 0)),
        forwarded=forwarded,
        original_published_at=original_date,
    )


def format_strict_card(source: StrictSource, message: Any, result: FilterResult) -> str:
    text = getattr(message, "raw_text", "") or ""
    message_id = getattr(message, "id", 0)
    link = message_link(source, message_id)
    forwarded, original_date = original_publication(message)
    date = original_date if forwarded and original_date else getattr(message, "date", None)
    date_text = date.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC") if date else "неизвестна"
    title = next((line.strip() for line in text.splitlines() if line.strip()), "Публикация без текста")[:180]
    category = {
        "direct_order": "СТРОГИЙ 3D-ЗАКАЗ",
        "freelance_vacancy": "СТРОГАЯ ПРОЕКТНАЯ ВАКАНСИЯ",
        "job_vacancy": "СТРОГАЯ 3D-ВАКАНСИЯ",
    }[result.category]
    fit = ", ".join(result.profile_reasons) if result.profile_reasons else "прошло все профильные ограничения"
    application = detect_application(text, "Telegram", link)
    preview = text[:900].rstrip() + ("…" if len(text) > 900 else "")
    blocks = [
        f"<b>{category}</b>",
        f"<b>{html.escape(title).upper()}</b>",
        f"<b>ДАТА ОРИГИНАЛА</b>\n{date_text}",
        f"<b>ОПЛАТА</b>\n{html.escape(result.price)}" if result.price != "не указана" else "<b>ОПЛАТА</b>\nСумма не указана",
        f"<b>ПОЧЕМУ ПОДХОДИТ</b>\n{html.escape(fit)}",
        "<b>РИСК</b>\nЗаказчик не верифицирован каналом. Не передавать доступ к устройству, Apple ID, банковские данные и исходники без согласованных условий.",
        f"<b>КАК ОТКЛИКНУТЬСЯ</b>\n{format_application_block(application, link)}",
        f"<b>ОПИСАНИЕ</b>\n<blockquote>{html.escape(preview)}</blockquote>",
        f"<b>ИСТОЧНИК</b>\n{html.escape(source.name)}" + (f"\n{link}" if link else ""),
    ]
    return "\n\n".join(blocks)


async def process_source(
    source: StrictSource,
    client: Any,
    state: StrictState,
    send: Callable[[str], Awaitable[None]],
    *,
    now: datetime | None = None,
) -> int:
    last_seen = state.get(source)
    if last_seen is None:
        async for message in client.iter_messages(source.entity, limit=1):
            state.set(source, message.id)
            state.save()
            break
        return 0

    processed = 0
    async for message in client.iter_messages(source.entity, min_id=last_seen, reverse=True):
        message_id = getattr(message, "id", None)
        if not isinstance(message_id, int) or message_id <= last_seen:
            continue
        try:
            result = evaluate_message(source, message, now=now)
            if result.accepted:
                await send(format_strict_card(source, message, result))
            state.record(result)
            state.set(source, message_id)
            state.save()
        except Exception:
            logging.exception("Строгое сообщение %s/%s не обработано; будет повторено", source.key, message_id)
            break
        last_seen = message_id
        processed += 1
    return processed


async def process_sources(
    sources: list[StrictSource],
    client: Any,
    state: StrictState,
    send: Callable[[str], Awaitable[None]],
    *,
    now: datetime | None = None,
) -> int:
    total = 0
    for source in sources:
        try:
            total += await process_source(source, client, state, send, now=now)
        except Exception:
            logging.exception("Строгий источник %s временно недоступен", source.key)
    return total


async def dry_run_source(source: StrictSource, client: Any, limit: int, *, now: datetime | None = None) -> Counter[str]:
    counts: Counter[str] = Counter()
    async for message in client.iter_messages(source.entity, limit=limit):
        result = evaluate_message(source, message, now=now)
        counts["accepted" if result.accepted else result.reason] += 1
        status = "ПОДХОДИТ" if result.accepted else f"ОТКЛОНЕНО: {result.reason}"
        print(f"[{source.name}] {getattr(message, 'id', '?')}: {status}")
    return counts


async def run(*, dry_run: bool = False, audit: bool = False, limit: int = 50) -> None:
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from telethon import TelegramClient

    settings: Settings = load_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    sources = load_strict_sources()
    client = TelegramClient(session_for_settings(settings), settings.api_id, settings.api_hash)
    bot = None
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("Аккаунт-сборщик не авторизован")
        if dry_run:
            for source in sources:
                counts = await dry_run_source(source, client, limit)
                logging.info("Строгая диагностика %s: %s", source.name, dict(counts))
            return
        if audit:
            async def no_send(_: str) -> None:
                return None

            state = StrictState()
            total = await process_sources(sources, client, state, no_send)
            logging.info(
                "Строгий аудит завершён: обработано=%s, всего_проверено=%s, прошло=%s, причины_отказа=%s",
                total,
                state.checked,
                state.accepted,
                dict(state.rejected_by_reason.most_common()),
            )
            return
        bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

        async def send(card: str) -> None:
            if not await send_to_recipients(bot, settings.recipient_chat_ids, card):
                raise RuntimeError("Строгое уведомление не доставлено")

        total = await process_sources(sources, client, StrictState(), send)
        logging.info("Строгая проверка завершена: обработано %s", total)
    finally:
        await client.disconnect()
        if bot is not None:
            await bot.session.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Отдельный строгий русскоязычный 3D-радар")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Проверить историю без отправки и изменения состояния")
    mode.add_argument("--audit", action="store_true", help="Обработать только новые сообщения, сохранить статистику, ничего не отправлять")
    parser.add_argument("--limit", type=int, default=50, help="Сообщений на источник в dry-run")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit должен быть положительным")
    return args


if __name__ == "__main__":
    arguments = parse_args()
    asyncio.run(run(dry_run=arguments.dry_run, audit=arguments.audit, limit=arguments.limit))
