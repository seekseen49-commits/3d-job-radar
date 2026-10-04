"""Одноразовый ручной вход в Threads для браузерного поиска."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright


BASE_DIR = Path(__file__).resolve().parent


async def main() -> None:
    load_dotenv(BASE_DIR / ".env", override=True)
    raw = os.getenv("THREADS_BROWSER_PROFILE_DIR", "threads_browser_profile").strip() or "threads_browser_profile"
    profile_dir = Path(raw)
    if not profile_dir.is_absolute():
        profile_dir = BASE_DIR / profile_dir
    profile_dir.mkdir(parents=True, exist_ok=True)
    channel = os.getenv("THREADS_BROWSER_CHANNEL", "msedge").strip() or "msedge"

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel=channel,
            headless=False,
            viewport={"width": 1440, "height": 1000},
            locale="ru-RU",
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.threads.com/", wait_until="domcontentloaded")
        print()
        print("Открылся Threads. Войдите в свой аккаунт обычным способом.")
        print("Пароль и коды в PowerShell вводить не нужно.")
        await asyncio.to_thread(input, "Когда в браузере откроется ваша лента Threads, вернитесь сюда и нажмите Enter...")
        await context.close()
        print("Готово. Сессия сохранена локально в отдельном профиле браузера.")


if __name__ == "__main__":
    asyncio.run(main())
