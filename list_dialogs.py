"""Выводит каналы, доступные уже авторизованному аккаунту-сборщику."""
from __future__ import annotations

import asyncio
from telethon import TelegramClient
from config import load_settings


async def run() -> None:
    settings = load_settings()
    client = TelegramClient(settings.telethon_session, settings.api_id, settings.api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        print("Нет авторизации. Сначала запустите: python authorize_telegram.py")
        return
    async for dialog in client.iter_dialogs():
        if dialog.is_channel:
            username = getattr(dialog.entity, "username", None) or "—"
            print(f"{dialog.id}\t{dialog.name}\t@{username}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(run())
