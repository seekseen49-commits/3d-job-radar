"""Детерминированные фильтры для кандидатов Threads."""
from __future__ import annotations

import re

from threads_client import ThreadPost


URL_RE = re.compile(r"https?://", re.I)
PROMO_RE = re.compile(
    r"\b(hiring|hire me|dm me|commission|sale|discount|курс|обучение|ваканси|ищу сотрудник|набор на курс)\b",
    re.I,
)
RISK_RE = re.compile(
    r"\b(выборы|президент|депутат|партия|война|суицид|самоповреж|оружие|наркот|казино|ставки|18\+|nsfw)\b",
    re.I,
)


def eligible_post(post: ThreadPost, *, own_username: str | None = None) -> bool:
    text = post.text.strip()
    if len(text) < 25:
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
