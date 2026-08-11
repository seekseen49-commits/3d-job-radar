"""Одноразово выводит StringSession из уже авторизованного локального collector.session."""
from __future__ import annotations

import asyncio

from config import load_settings


async def run() -> None:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    settings = load_settings()
    if settings.telethon_string_session:
        raise RuntimeError("Уберите TELETHON_STRING_SESSION из окружения: экспортируется локальный collector.session.")
    client = TelegramClient(settings.telethon_session, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Локальная сессия не авторизована. Сначала вручную выполните authorize_telegram.py.")
        print("ВНИМАНИЕ: StringSession равна паролю от Telegram-аккаунта. Не публикуйте и не пересылайте её.")
        print(StringSession.save(client.session))
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(run())
