"""Загрузка конфигурации только из локального файла .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    bot_token: str
    owner_chat_id: int
    telethon_session: str
    telethon_string_session: str | None
    database_path: Path
    sources_path: Path
    log_level: str
    send_job_vacancies: bool
    send_freelance_vacancies: bool


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"В .env не задано обязательное значение {name}")
    return value


def load_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env")
    try:
        api_id = int(_required("TELEGRAM_API_ID"))
        owner_chat_id = int(_required("OWNER_CHAT_ID"))
    except ValueError as exc:
        raise ValueError("TELEGRAM_API_ID и OWNER_CHAT_ID должны быть целыми числами") from exc
    return Settings(
        api_id=api_id,
        api_hash=_required("TELEGRAM_API_HASH"),
        bot_token=_required("BOT_TOKEN"),
        owner_chat_id=owner_chat_id,
        telethon_session=os.getenv("TELETHON_SESSION", "collector").strip() or "collector",
        telethon_string_session=os.getenv("TELETHON_STRING_SESSION", "").strip() or None,
        database_path=BASE_DIR / (os.getenv("DATABASE_PATH", "orders.sqlite3").strip() or "orders.sqlite3"),
        sources_path=BASE_DIR / (os.getenv("SOURCES_PATH", "sources.json").strip() or "sources.json"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        send_job_vacancies=os.getenv("SEND_JOB_VACANCIES", "false").strip().lower() in {"1", "true", "yes", "on"},
        send_freelance_vacancies=os.getenv("SEND_FREELANCE_VACANCIES", "true").strip().lower() in {"1", "true", "yes", "on"},
    )
