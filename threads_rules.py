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


def eligible_post(
    post: ThreadPost,
    *,
    own_username: str | None = None,
    max_age_hours: int = 24,
) -> bool:
    text = post.text.strip()
    if len(text) < 25:
        return False
    if not _is_fresh(post.timestamp, max_age_hours=max_age_hours):
        return False
    if own_username and post.username.lower() == own_username.strip().lstrip("@").lower():
        return False
    if URL_RE.search(text) and len(text) < 120:
        return False
    if PROMO_RE.search(text):
        return False
    if RISK_RE.search(text):
        return False
    return True
