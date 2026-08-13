"""Жёсткий параллельный фильтр для русскоязычного радара Дарьи.

Модуль не меняет поведение основного :mod:`filters`. Он добавляет обязательные
проверки свежести, языка, доступности отклика и явных стоп-условий поверх уже
существующей 3D-классификации.
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from application_method import detect_application
from filters import FilterResult, SourceType, evaluate, extract_price, normalize
from work_metadata import analyze_work_metadata


ORDER_MAX_AGE = timedelta(hours=72)
VACANCY_MAX_AGE = timedelta(days=7)

CLOSED_PATTERNS = (
    r"\bваканси\w* закрыт\w*\b",
    r"\bзаказ закрыт\w*\b",
    r"\bисполнител[ья] найден\w*\b",
    r"\bпозици\w* закрыт\w*\b",
    r"\b(?:уже )?не актуальн\w*\b",
)
NON_COMMERCIAL_PATTERNS = (
    r"\bконкурс\w*\b",
    r"\brevenue\s*share\b",
    r"\brevshare\b",
    r"\bза процент от (?:прибыли|дохода|продаж)\b",
    r"\bволонтер\w*\b",
    r"\bволонтёр\w*\b",
    r"\bэнтузиаст\w*\b",
    r"\bнеоплачиваем\w*\s+(?:тестов\w*|стажиров\w*)\b",
    r"\b(?:тестов\w*|стажиров\w*)\s+без оплаты\b",
)
PAID_PLATFORM_PATTERNS = (
    r"https?://(?:www\.)?fl\.ru/",
    r"https?://(?:www\.)?profi\.ru/",
    r"https?://(?:www\.)?youdo\.com/",
    r"\b(?:купить|покупка) отклик\w*\b",
    r"\bплатн\w* отклик\w*\b",
)
LANGUAGE_BARRIER_PATTERNS = (
    r"\benglish\s+(?:is\s+)?(?:required|mandatory)\b",
    r"\bmust (?:speak|know|communicate in) english\b",
    r"\bанглийск\w*.{0,30}\b(?:обязател\w*|не ниже\s+[abc][12]|свободн\w*)\b",
    r"\b(?:обязател\w*|требуется).{0,30}\bанглийск\w*\b",
)
FOREIGN_BARRIER_PATTERNS = (
    r"\b(?:только|исключительно)\s+(?:из|для)\s+(?:европы|ес)\b",
    r"\b(?:художник|кандидат|исполнитель)\w*.{0,30}\bиз европы\b",
    r"\beurope[- ]based\b",
    r"\bbased in europe\b",
    r"\b(?:иностранн\w*|зарубежн\w*)\s+(?:счет|счёт|карт\w*)\b",
    r"\b(?:счет|счёт|карт\w*)\s+(?:иностранн\w*|зарубежн\w*)\b",
    r"\bmust be able to invoice\b",
    r"\bforeign bank account\b",
    r"\b(?:paypal|wise|sepa)\s+only\b",
)
PROFILE_BARRIER_PATTERNS = (
    r"\bткан\w*\b",
    r"\bодежд\w*\b",
    r"\bвышивк\w*\b",
    r"\bсум(?:ка|ки|ок|ку|кой|ками)\b",
    r"\b(?:fabric|garment|clothing|cloth simulation)\b",
    r"\bmarvelous designer\b",
)
PAYMENT_CUES = (
    "оплата", "бюджет", "гонорар", "зарплата", "заработная плата", "ставка",
    "оплачиваем", "договорная", "₽", "руб", " usd", "$", " eur", "€", "₸", "тенге",
)


def _reject(result: FilterResult | None, reason: str, text: str) -> FilterResult:
    if result is None:
        return FilterResult("rejected", reason, extract_price(text)[2])
    return replace(result, category="rejected", reason=reason)


def _matches_any(content: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, content, re.IGNORECASE | re.DOTALL) for pattern in patterns)


def _is_russian_language(text: str) -> bool:
    cyrillic = len(re.findall(r"[а-яё]", text, re.IGNORECASE))
    latin = len(re.findall(r"[a-z]", text, re.IGNORECASE))
    total = cyrillic + latin
    # Названия ролей, форматов и программ часто остаются на английском даже в
    # полностью русских объявлениях: Junior 3D Artist, low-poly, Blender, FBX.
    return cyrillic >= 12 and total > 0 and cyrillic / total >= 0.25


def _utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return None
    return value.astimezone(UTC)


def evaluate_strict(
    text: str,
    mode: str,
    source_type: SourceType = "mixed",
    *,
    published_at: datetime | None,
    now: datetime | None = None,
    source: str = "Telegram",
    source_url: str | None = None,
    application_url: str | None = None,
    forwarded: bool = False,
    original_published_at: datetime | None = None,
) -> FilterResult:
    """Применяет жёсткие условия, не затрагивая основной широкий фильтр."""
    content = normalize(text)
    current = _utc(now or datetime.now(UTC))
    message_date = _utc(published_at)
    original_date = _utc(original_published_at)

    if current is None or message_date is None:
        return _reject(None, "точная дата публикации отсутствует или не проверяется", text)
    if forwarded and original_date is None:
        return _reject(None, "у пересланного объявления нет точной даты оригинала", text)
    effective_date = original_date if forwarded else message_date
    if effective_date is None:
        return _reject(None, "точная дата публикации отсутствует или не проверяется", text)
    if effective_date > current + timedelta(minutes=5):
        return _reject(None, "дата публикации находится в будущем", text)

    if not _is_russian_language(text):
        return _reject(None, "объявление не русскоязычное", text)
    if _matches_any(content, CLOSED_PATTERNS):
        return _reject(None, "объявление уже закрыто или исполнитель найден", text)
    if _matches_any(content, NON_COMMERCIAL_PATTERNS):
        return _reject(None, "конкурс, revshare, волонтёрство или неоплачиваемая работа", text)
    if _matches_any(content, PAID_PLATFORM_PATTERNS):
        return _reject(None, "платная площадка для отклика", text)
    if _matches_any(content, LANGUAGE_BARRIER_PATTERNS):
        return _reject(None, "требуется обязательный английский", text)
    if _matches_any(content, FOREIGN_BARRIER_PATTERNS):
        return _reject(None, "нужна зарубежная география, карта или возможность выставлять invoice", text)
    if _matches_any(content, PROFILE_BARRIER_PATTERNS):
        return _reject(None, "вне рабочего профиля: ткань, одежда, вышивка или сумки", text)

    result = evaluate(text, mode, source_type)
    if not result.accepted:
        return result

    metadata = analyze_work_metadata(text)
    if metadata.russia_eligibility == "blocked":
        return _reject(result, "работа явно недоступна из России", text)

    age = current - effective_date
    max_age = ORDER_MAX_AGE if result.category == "direct_order" else VACANCY_MAX_AGE
    if age > max_age:
        label = "72 часов" if result.category == "direct_order" else "7 дней"
        return _reject(result, f"объявление старше {label}", text)

    if result.category == "direct_order" and not any(cue in content for cue in PAYMENT_CUES):
        return _reject(result, "для разового заказа не подтверждена оплата", text)

    application = detect_application(text, source, source_url, application_url)
    if application.method not in {"direct_contact", "external_application"}:
        return _reject(result, "нет бесплатного прямого контакта или внешней формы отклика", text)

    return result
