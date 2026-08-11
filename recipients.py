"""Нормализация получателей и изолированная отправка уведомлений."""
from __future__ import annotations

import logging
from typing import Any


def parse_recipient_chat_ids(owner_chat_id: int, additional: str | None) -> tuple[int, ...]:
    values = [owner_chat_id]
    for part in (additional or "").split(","):
        try:
            value = int(part.strip())
        except ValueError:
            continue
        if value not in values:
            values.append(value)
    return tuple(values)


async def send_to_recipients(bot: Any, recipient_chat_ids: tuple[int, ...], text: str) -> None:
    """Пробует всех получателей; ID и секреты в лог не попадают."""
    for chat_id in recipient_chat_ids:
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            logging.exception("Не удалось доставить уведомление одному из получателей")
