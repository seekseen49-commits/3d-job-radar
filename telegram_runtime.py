"""Небольшие тестируемые помощники для Telethon в постоянно работающем процессе."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from config import Settings


def session_for_settings(settings: "Settings", string_session_factory: Callable[[str], Any] | None = None) -> Any:
    """StringSession имеет приоритет на сервере; локальный файл остаётся запасным вариантом."""
    if settings.telethon_string_session:
        if string_session_factory is None:
            from telethon.sessions import StringSession
            string_session_factory = StringSession
        return string_session_factory(settings.telethon_string_session)
    return settings.telethon_session


async def connect_with_retry(
    client: Any,
    stop_event: asyncio.Event,
    *,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> bool:
    """Повторяет только временные сетевые ошибки и немедленно прерывается при остановке."""
    delay = initial_delay
    while not stop_event.is_set():
        try:
            await client.connect()
            return True
        except (OSError, ConnectionError, asyncio.TimeoutError) as exc:
            logging.warning("Временная ошибка Telethon, новая попытка через %.0f с: %s", delay, exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, max_delay)
    return False


async def shutdown_resources(*, client: Any, bot: Any, db: Any, tasks: list[asyncio.Task[Any]]) -> None:
    """Отменяет фоновые задачи и закрывает все локальные ресурсы в предсказуемом порядке."""
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    with suppress(Exception):
        await client.disconnect()
    with suppress(Exception):
        await bot.session.close()
    db.close()


async def run_channel_handler(handler: Callable[[], Awaitable[None]]) -> None:
    """Одна ошибка обработки сообщения не завершает подписку на остальные каналы."""
    try:
        await handler()
    except Exception:
        logging.exception("Ошибка обработки сообщения канала; приём остальных сообщений продолжается")
