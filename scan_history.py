"""Диагностическое чтение истории: без бота, SQLite и записи дедупликации."""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from filters import NEGATIVE, Category, evaluate, normalize, positive_matches


@dataclass(frozen=True)
class Source:
    channel_id: int
    name: str
    username: str | None
    mode: str
    source_type: str


def load_enabled_sources(path: Path) -> list[Source]:
    items = json.loads(path.read_text(encoding="utf-8"))
    return [
        Source(item["channel_id"], str(item.get("name") or item["channel_id"]), item.get("username"), item["mode"], item.get("source_type", "job_board"))
        for item in items
        if item.get("enabled") is True and isinstance(item.get("channel_id"), int)
        and item["channel_id"] != 0 and item.get("mode") in {"general", "3d_only"} and item.get("source_type", "job_board") in {"mixed", "job_board"}
    ]


def find_keywords(text: str) -> tuple[list[str], list[str]]:
    content = normalize(text)
    return list(positive_matches(text)), [word for word in NEGATIVE if word in content]


def message_link(source: Source, message_id: int) -> str | None:
    if source.username:
        return f"https://t.me/{source.username.lstrip('@')}/{message_id}"
    id_text = str(source.channel_id)
    return f"https://t.me/c/{id_text[4:]}/{message_id}" if id_text.startswith("-100") else None


def category_label(category: Category) -> str:
    return {"direct_order": "ПРЯМОЙ ЗАКАЗ", "freelance_vacancy": "КОНТРАКТНАЯ ВАКАНСИЯ", "job_vacancy": "ВАКАНСИЯ", "self_promo": "САМОПРЕЗЕНТАЦИЯ", "rejected": "ОТКЛОНЕНО"}[category]


def render_message(source: Source, message_id: int, date, text: str) -> tuple[str, Category]:
    result = evaluate(text, source.mode, source.source_type)
    positive, negative = find_keywords(text)
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "(публикация без текста)")
    date_text = date.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC") if date else "неизвестна"
    link = message_link(source, message_id) or "не удалось сформировать"
    line = (
        f"Канал: {source.name} ({source.channel_id})\nДата: {date_text}\nПервая строка: {first_line}\n"
        f"Результат: {category_label(result.category)}\nПричина: {result.reason}\n"
        f"Положительные ключи: {', '.join(positive) if positive else 'не найдены'}\n"
        f"Отрицательные ключи: {', '.join(negative) if negative else 'не найдены'}\n"
        f"hiring_intent_matches: {', '.join(result.hiring_intent_matches) if result.hiring_intent_matches else 'нет'}\n"
        f"deliverable_matches: {', '.join(result.deliverable_matches) if result.deliverable_matches else 'нет'}\n"
        f"self_promo_matches: {', '.join(result.self_promo_matches) if result.self_promo_matches else 'нет'}\n"
        f"Цена: {result.price}\nСсылка: {link}\nИсходный текст:\n{text or '—'}\n" + "-" * 70
    )
    return line, result.category


def render_summary(source: Source, counts: Counter[str], newest_date, recent_7: int, recent_30: int) -> str:
    checked = sum(counts.values())
    suitable = counts["direct_order"] + counts["freelance_vacancy"] + counts["job_vacancy"]
    percentage = suitable / checked * 100 if checked else 0
    newest = newest_date.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC") if newest_date else "нет сообщений"
    inactive = bool(newest_date and newest_date < datetime.now(timezone.utc) - timedelta(days=60))
    activity = "НЕАКТИВНЫЙ: последнее сообщение старше 60 дней" if inactive else "активный"
    return (
        f"Канал: {source.name} ({source.channel_id})\nПоследнее сообщение: {newest}\nСтатус: {activity}\n"
        f"Проверено: {checked}\nПрямые заказы: {counts['direct_order']}\nКонтрактные вакансии: {counts['freelance_vacancy']}\n"
        f"Штатные вакансии: {counts['job_vacancy']}\nСамопрезентация: {counts['self_promo']}\nОтклонено: {counts['rejected']}\nПодходящих публикаций: {percentage:.1f}%\n"
        f"Сообщений за 7 дней: {recent_7}\nСообщений за 30 дней: {recent_30}\n"
    )


async def scan(limit: int, accepted_only: bool = False) -> tuple[str, str]:
    from config import load_settings
    from telethon import TelegramClient

    settings = load_settings()
    sources = load_enabled_sources(settings.sources_path)
    sections: dict[str, list[str]] = {category: [] for category in ("direct_order", "freelance_vacancy", "job_vacancy", "self_promo", "rejected")}
    summaries: list[str] = []
    if not sources:
        report = "Диагностический отчёт\n\nНет корректных включённых источников в sources.json.\n"
        return report, report

    client = TelegramClient(settings.telethon_session, settings.api_id, settings.api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Аккаунт-сборщик не авторизован. Сначала вручную запустите authorize_telegram.py.")
        now = datetime.now(timezone.utc)
        for source in sources:
            counts: Counter[str] = Counter()
            newest_date = None
            recent_7 = recent_30 = 0
            try:
                async for message in client.iter_messages(source.channel_id, limit=limit):
                    message_date = message.date
                    if newest_date is None or message_date > newest_date:
                        newest_date = message_date
                    if message_date >= now - timedelta(days=7): recent_7 += 1
                    if message_date >= now - timedelta(days=30): recent_30 += 1
                    line, category = render_message(source, message.id, message_date, message.raw_text or "")
                    sections[category].append(line)
                    counts[category] += 1
            except Exception as exc:
                sections["rejected"].append(f"Канал: {source.name}\nОшибка чтения: {type(exc).__name__}: {exc}\n" + "-" * 70)
            summaries.append(render_summary(source, counts, newest_date, recent_7, recent_30))
    finally:
        await client.disconnect()

    heading = f"Диагностический отчёт. Лимит на канал: {limit}\n" + "=" * 70
    report = "\n\n".join([
        heading, "ПРЯМЫЕ ЗАКАЗЫ\n" + "=" * 70 + "\n" + "\n".join(sections["direct_order"] or ["Нет."]),
        "КОНТРАКТНЫЕ ВАКАНСИИ\n" + "=" * 70 + "\n" + "\n".join(sections["freelance_vacancy"] or ["Нет."]),
        "ШТАТНЫЕ ВАКАНСИИ\n" + "=" * 70 + "\n" + "\n".join(sections["job_vacancy"] or ["Нет."]),
        "САМОПРЕЗЕНТАЦИЯ\n" + "=" * 70 + "\n" + "\n".join(sections["self_promo"] or ["Нет."]),
        "ОТКЛОНЁННЫЕ\n" + "=" * 70 + "\n" + "\n".join(sections["rejected"] or ["Нет."]),
        "СТАТИСТИКА ПО ИСТОЧНИКАМ\n" + "=" * 70 + "\n" + "\n".join(summaries),
    ]) + "\n"
    if accepted_only:
        console = "\n\n".join([heading, "ПРЯМЫЕ ЗАКАЗЫ\n" + "\n".join(sections["direct_order"] or ["Нет."]), "КОНТРАКТНЫЕ ВАКАНСИИ\n" + "\n".join(sections["freelance_vacancy"] or ["Нет."]), "СТАТИСТИКА ПО ИСТОЧНИКАМ\n" + "\n".join(summaries)]) + "\n"
    else:
        console = report
    return report, console


async def run(limit: int, accepted_only: bool) -> None:
    report, console = await scan(limit, accepted_only)
    report_path = Path(__file__).resolve().parent / "scan_report.txt"
    report_path.write_text(report, encoding="utf-8")
    print(console, end="")
    print(f"Полный отчёт сохранён: {report_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Проверить историю источников без отправки и записи в БД.")
    parser.add_argument("--limit", type=int, default=50, help="Последних сообщений на канал (по умолчанию: 50).")
    parser.add_argument("--accepted-only", action="store_true", help="Вывести в консоль только прямые заказы и контрактные вакансии; полный файл не сокращается.")
    args = parser.parse_args()
    if args.limit < 1: parser.error("--limit должен быть положительным числом")
    return args


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(args.limit, args.accepted_only))
