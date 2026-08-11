"""Разовая интерактивная авторизация аккаунта-сборщика Telethon."""
from __future__ import annotations

import asyncio
from telethon import TelegramClient
from config import load_settings


async def run() -> None:
    settings = load_settings()
    client = TelegramClient(settings.telethon_session, settings.api_id, settings.api_hash)
    await client.start()  # Telethon запросит телефон и код только в интерактивной консоли.
    print("Авторизация завершена. Файл сессии создан локально.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(run())
