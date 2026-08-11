"""Детерминированное определение способа отклика без сетевых запросов."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
TG_LINK_RE = re.compile(r"https?://(?:www\.)?t\.me/([A-Za-z0-9_]{4,32})", re.IGNORECASE)
TG_USER_RE = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_]{4,31})")
URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
CONTACT_CUES = ("write", "send", "apply", "contact", "portfolio", "resume", "cv", "email", "отклик", "резюме", "пишите", "напишите")
SOURCE_DOMAINS = {"Himalayas": "himalayas.app", "Jobicy": "jobicy.com", "Remotive": "remotive.com", "Telegram": "t.me"}


@dataclass(frozen=True)
class ApplicationInfo:
    method: str
    application_url: str | None = None
    contacts: tuple[str, ...] = ()


def _is_source_url(url: str, domain: str | None) -> bool:
    if not url or not domain:
        return False
    host = (urlparse(url).hostname or "").casefold().removeprefix("www.")
    domain = domain.casefold().removeprefix("www.")
    return host == domain or host.endswith(f".{domain}")


def _contact_related(text: str, start: int, end: int) -> bool:
    nearby = text[max(0, start - 100):end + 100].casefold()
    return any(cue in nearby for cue in CONTACT_CUES)


def detect_application(text: str, source: str, source_url: str | None, application_url: str | None = None) -> ApplicationInfo:
    """Приоритет: прямой контакт, внешняя форма, страница платформы, неизвестно."""
    contacts: list[str] = []
    for match in TG_LINK_RE.finditer(text):
        contacts.append(f"Telegram: @{match.group(1)}")
    for match in TG_USER_RE.finditer(text):
        if _contact_related(text, match.start(), match.end()):
            contacts.append(f"Telegram: @{match.group(1)}")
    for match in EMAIL_RE.finditer(text):
        if _contact_related(text, match.start(), match.end()):
            contacts.append(f"Email: {match.group(0)}")
    contacts = list(dict.fromkeys(contacts))[:3]
    if contacts:
        first_tg = next((match.group(0) for match in TG_LINK_RE.finditer(text)), None)
        return ApplicationInfo("direct_contact", first_tg, tuple(contacts))

    domain = SOURCE_DOMAINS.get(source)
    candidates = [application_url] if application_url else []
    candidates += URL_RE.findall(text)
    for candidate in candidates:
        if candidate and not _is_source_url(candidate, domain):
            return ApplicationInfo("external_application", candidate)
    if source_url:
        return ApplicationInfo("source_platform", source_url)
    return ApplicationInfo("unknown")


def application_rank(info: ApplicationInfo) -> int:
    return {"direct_contact": 0, "external_application": 1, "source_platform": 2, "unknown": 3}[info.method]


def format_application_block(info: ApplicationInfo, original_url: str | None) -> str:
    if info.method == "direct_contact":
        return "📨 <b>Отклик: НАПРЯМУЮ</b>\n" + "\n".join(info.contacts)
    if info.method == "external_application":
        return f"🌐 <b>Отклик: ВНЕШНЯЯ ФОРМА</b>\n{info.application_url}"
    if info.method == "source_platform":
        return f"🔐 <b>Отклик: ЧЕРЕЗ ПЛАТФОРМУ</b>\nВозможно, потребуется аккаунт\n{original_url or info.application_url}"
    return f"❓ <b>СПОСОБ ОТКЛИКА НЕ ОПРЕДЕЛЁН</b>\n{original_url or ''}".rstrip()
