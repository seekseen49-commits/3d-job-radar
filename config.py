"""Загрузка конфигурации только из локального файла .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from recipients import parse_recipient_chat_ids


BASE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    bot_token: str
    owner_chat_id: int
    recipient_chat_ids: tuple[int, ...]
    telethon_session: str
    telethon_string_session: str | None
    database_path: Path
    sources_path: Path
    log_level: str
    send_job_vacancies: bool
    send_freelance_vacancies: bool
    himalayas_enabled: bool
    himalayas_poll_interval_minutes: int
    jobicy_enabled: bool
    jobicy_poll_interval_minutes: int
    remotive_enabled: bool
    remotive_poll_interval_minutes: int
    himalayas_recovery_backfill: bool
    himalayas_diagnostic: bool
    himalayas_mcp_enabled: bool
    himalayas_mcp_poll_minutes: int


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"В .env не задано обязательное значение {name}")
    return value


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default
    return value if value > 0 else default


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
        recipient_chat_ids=parse_recipient_chat_ids(owner_chat_id, os.getenv("ADDITIONAL_RECIPIENT_CHAT_IDS")),
        telethon_session=os.getenv("TELETHON_SESSION", "collector").strip() or "collector",
        telethon_string_session=os.getenv("TELETHON_STRING_SESSION", "").strip() or None,
        database_path=BASE_DIR / (os.getenv("DATABASE_PATH", "orders.sqlite3").strip() or "orders.sqlite3"),
        sources_path=BASE_DIR / (os.getenv("SOURCES_PATH", "sources.json").strip() or "sources.json"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        send_job_vacancies=_bool("SEND_JOB_VACANCIES", False),
        send_freelance_vacancies=_bool("SEND_FREELANCE_VACANCIES", True),
        himalayas_enabled=_bool("HIMALAYAS_ENABLED", True),
        himalayas_poll_interval_minutes=_positive_int("HIMALAYAS_POLL_INTERVAL_MINUTES", 1440),
        jobicy_enabled=_bool("JOBICY_ENABLED", True),
        jobicy_poll_interval_minutes=_positive_int("JOBICY_POLL_INTERVAL_MINUTES", 60),
        remotive_enabled=_bool("REMOTIVE_ENABLED", True),
        remotive_poll_interval_minutes=_positive_int("REMOTIVE_POLL_INTERVAL_MINUTES", 360),
        himalayas_recovery_backfill=_bool("HIMALAYAS_RECOVERY_BACKFILL", False),
        himalayas_diagnostic=_bool("HIMALAYAS_DIAGNOSTIC", False),
        himalayas_mcp_enabled=_bool("HIMALAYAS_MCP_ENABLED", True),
        himalayas_mcp_poll_minutes=_positive_int("HIMALAYAS_MCP_POLL_MINUTES", 10),
    )
