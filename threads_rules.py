"""Детерминированные фильтры для кандидатов Threads."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from threads_client import ThreadPost


URL_RE = re.compile(r"https?://", re.I)
PROMO_RE = re.compile(
    r"\b(hiring|hire me|dm me|commission|sale|discount|ваканси|ищу сотрудник|набор на курс|купи курс|скидка на курс)\b",
    re.I,
)
RISK_RE = re.compile(
    r"\b(выборы|президент|депутат|партия|война|суицид|самоповреж|оружие|weapon|weapons|gun|guns|firearm|firearms|knife|knives|наркот|казино|ставки|18\+|nsfw)\b",
    re.I,
)


def _is_fresh(timestamp: str | None, *, max_age_hours: int = 24) -> bool:
    if not timestamp:
        return False
    try:
        published = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - published.astimezone(timezone.utc)
    return -timedelta(minutes=5) <= age <= timedelta(hours=max_age_hours)


def ineligibility_reason(
    post: ThreadPost,
    *,
    own_username: str | None = None,
    max_age_hours: int = 24,
) -> str | None:
    """Вернуть причину отклонения поста или None, если пост подходит."""
    text = post.text.strip()
    if len(text) < 25:
        return "too_short"
    if not post.timestamp:
        return "missing_timestamp"
    if not _is_fresh(post.timestamp, max_age_hours=max_age_hours):
        return "not_fresh"
    if own_username and post.username.lower() == own_username.strip().lstrip("@").lower():
        return "own_post"
    if URL_RE.search(text) and len(text) < 120:
        return "short_with_url"
    if PROMO_RE.search(text):
        return "promo"
    if RISK_RE.search(text):
        return "risk"
    return None


def eligible_post(
    post: ThreadPost,
    *,
    own_username: str | None = None,
    max_age_hours: int = 24,
) -> bool:
    return ineligibility_reason(
        post,
        own_username=own_username,
        max_age_hours=max_age_hours,
    ) is None
