"""Общие модели, состояние и безопасная обработка внешних job boards."""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from filters import FilterResult, evaluate
from application_method import application_rank, detect_application, format_application_block
from work_metadata import analyze_work_metadata


STATE_PATH = Path(__file__).resolve().parent.parent / "state" / "external_sources.json"
BACKFILL_WINDOW = timedelta(hours=72)
STATE_RETENTION = timedelta(days=30)
MAX_INITIAL_NOTIFICATIONS = 10


@dataclass(frozen=True)
class ExternalJob:
    source: str
    external_id: str
    title: str
    description: str
    url: str
    published_at: datetime | None
    company: str = ""
    job_type: str = ""
    location: str = ""
    salary_raw: str = ""
    excerpt: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    currency: str = ""
    salary_period: str = ""
    contract_duration: str = ""
    application_link: str = ""
    location_restrictions: list[str] | None = None

    @property
    def raw_text(self) -> str:
        return "\n".join(part for part in (self.title, self.excerpt, self.description) if part).strip()

    @property
    def application_info(self):
        return detect_application(self.raw_text, self.source, self.url, self.application_link)

    @property
    def application_method(self) -> str:
        return self.application_info.method

    @property
    def application_url(self) -> str | None:
        return self.application_info.application_url

    @property
    def application_contact(self) -> tuple[str, ...]:
        return self.application_info.contacts

    @property
    def metadata(self):
        return analyze_work_metadata(self.raw_text, self.location_restrictions if self.location_restrictions is not None else self.location)


# Эти шаблоны намеренно описывают профессию или задачу, а не случайный термин
# внутри длинного текста объявления. Одинокие OBJ/CAD/Unreal/Blender не подходят.
STRONG_3D_PATTERNS = (
    r"\b3d\s+(?:artist|model(?:er|ler|ing)|designer|visuali[sz]ation|rendering|furniture|environment|animator|generalist)\b",
    r"\b(?:cgi|cg)\s+artist\b", r"\barchitectural\s+visuali[sz]ation\b", r"\barchviz\b",
    r"\b(?:product\s+(?:visuali[sz]ation|rendering)|environment artist|prop artist|hard surface|technical artist)\b",
    r"\bblender\s+(?:artist|model(?:er|ler))\b", r"\b(?:cad\s+(?:model(?:er|ler)|designer)|3d\s+cad|stl\s+modeling)\b",
    r"\b(?:unreal(?:\s+engine)?|maya|cinema\s+4d|3ds\s+max)\s+artist\b",
    r"\bsketchup\b.{0,40}\b(?:rendering|model(?:ing|ling))\b",
)
EXTERNAL_NON_3D_ROLES = (
    "customer success", "customer support", "marketing", "advertising", "ad operations", "software engineer",
    "developer", "devops", "sales", "account manager", "product manager", "graphic designer", "ui/ux",
    "video editor", "social media",
)
ROLE_CONTEXT_PATTERNS = ("responsibil", "duties", "you will", "responsible", "requirements", "experience", "обязанност", "требован")


def external_3d_relevant(job: ExternalJob) -> bool:
    """Требует сильный сигнал 3D-роли/задачи для публичных job boards."""
    title = job.title.casefold()
    role_text = f"{job.excerpt}\n{job.description}".casefold()
    if any(re.search(pattern, title, re.IGNORECASE) for pattern in STRONG_3D_PATTERNS):
        return True
    matches = [match for pattern in STRONG_3D_PATTERNS if (match := re.search(pattern, role_text, re.IGNORECASE))]
    if not matches:
        return False
    # У нерелевантной профессии сильная фраза в произвольном описании не
    # достаточна: она должна быть частью обязанностей или требований роли.
    if any(role in title for role in EXTERNAL_NON_3D_ROLES):
        return any(
            any(cue in role_text[max(0, match.start() - 160):match.end() + 160] for cue in ROLE_CONTEXT_PATTERNS)
            for match in matches
        )
    return True


def opportunity_priority(job: ExternalJob) -> str:
    """Оценка дохода только для подходящих 3D-вакансий; ничего не отбрасывает."""
    amount = max((value for value in (job.salary_min, job.salary_max) if value is not None), default=None)
    if amount is None:
        return "UNKNOWN"
    period = job.salary_period.casefold()
    if "hour" in period and amount >= 50:
        return "HIGH"
    if any(word in period for word in ("month", "monthly")) and amount >= 4_000:
        return "HIGH"
    if any(word in period for word in ("year", "annual")) and amount >= 60_000:
        return "HIGH"
    if not period and amount >= 3_000:
        return "HIGH"
    if job.contract_duration and amount > 0 and re.search(r"\b(?:6|7|8|9|1[0-2])\s*(?:month|months|мес)", job.contract_duration, re.IGNORECASE):
        return "HIGH"
    return "NORMAL"


class ExternalProvider(Protocol):
    name: str
    interval_minutes: int

    def fetch_jobs(self) -> list[ExternalJob]: ...


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append(" ")


def html_to_text(value: Any) -> str:
    """Удаляет HTML, оставляя нормальный текст для строгой существующей фильтрации."""
    parser = _PlainTextParser()
    parser.feed(str(value or ""))
    parser.close()
    return " ".join(html.unescape("".join(parser.parts)).split())


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.isdigit():
        return parse_datetime(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def contract_duration_from_text(*values: Any) -> str:
    text = " ".join(str(value or "") for value in values)
    match = re.search(r"\b\d+\s*[- ]?(?:month|months|week|weeks|year|years|месяц(?:ев|а)?|недел[ьяи])\b", text, re.IGNORECASE)
    return match.group(0) if match else ""


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def normalized_fingerprint(job: ExternalJob) -> str:
    text = re.sub(r"[^\w]+", " ", f"{job.title} {job.company}".casefold()).strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ExternalState:
    """Состояние job boards, отдельное от Telegram `last_seen.json`."""

    def __init__(self, path: Path = STATE_PATH) -> None:
        self.path = path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            raw = {}
        self.values: dict[str, Any] = raw if isinstance(raw, dict) else {}
        self.values.setdefault("last_poll_at", {})
        self.values.setdefault("processed", {})
        self.values.setdefault("fingerprints", {})
        self.values.setdefault("initialized", {})

    def is_due(self, source: str, interval_minutes: int, now: datetime) -> bool:
        raw = self.values.get("himalayas_mcp_last_successful_poll_at") if source == "Himalayas MCP" else self.values["last_poll_at"].get(source)
        last = parse_datetime(raw)
        return last is None or now - last >= timedelta(minutes=interval_minutes)

    def is_initialized(self, source: str) -> bool:
        return self.values["initialized"].get(source) is True

    def is_processed(self, job: ExternalJob) -> bool:
        return f"{job.source}:{job.external_id}" in self.values["processed"]

    def has_fingerprint(self, job: ExternalJob) -> bool:
        return normalized_fingerprint(job) in self.values["fingerprints"]

    def mark_processed(self, job: ExternalJob, now: datetime) -> None:
        self.values["processed"][f"{job.source}:{job.external_id}"] = _timestamp(now)

    def mark_fingerprint(self, job: ExternalJob, now: datetime) -> None:
        self.values["fingerprints"][normalized_fingerprint(job)] = _timestamp(now)

    def finish_poll(self, source: str, now: datetime) -> None:
        self.values["last_poll_at"][source] = _timestamp(now)
        if source == "Himalayas MCP":
            self.values["himalayas_mcp_last_successful_poll_at"] = _timestamp(now)
        self.values["initialized"][source] = True

    def recovery_completed(self) -> bool:
        return self.recovery_version() >= 2

    def recovery_version(self) -> int:
        value = self.values.get("himalayas_recovery_version")
        if isinstance(value, int):
            return value
        return 1 if self.values.get("himalayas_recovery_72h_completed") is True else 0

    def mark_recovery_completed(self) -> None:
        self.values["himalayas_recovery_72h_completed"] = True
        self.values["himalayas_recovery_version"] = 2

    def prune(self, now: datetime) -> None:
        cutoff = now - STATE_RETENTION
        for section in ("processed", "fingerprints"):
            self.values[section] = {
                key: value for key, value in self.values[section].items()
                if (parsed := parse_datetime(value)) is not None and parsed >= cutoff
            }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.values, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)


def should_send(result: FilterResult, settings: Any) -> bool:
    return result.category == "direct_order" or (
        result.category == "freelance_vacancy" and settings.send_freelance_vacancies
    ) or (result.category == "job_vacancy" and settings.send_job_vacancies)


def format_external_card(job: ExternalJob, result: FilterResult) -> str:
    headings = {"direct_order": "3D-ЗАКАЗ", "freelance_vacancy": "3D-КОНТРАКТ / ФРИЛАНС", "job_vacancy": "3D-ВАКАНСИЯ"}
    date_text = job.published_at.strftime("%d.%m.%Y %H:%M UTC") if job.published_at else "не указано"
    excerpt = job.excerpt or job.description
    excerpt = (excerpt[:600].rstrip() + "…") if len(excerpt) > 600 else excerpt
    priority = opportunity_priority(job)
    application = job.application_info
    metadata = job.metadata
    payment = {"crypto_explicit": "КРИПТА", "fiat_explicit": "ФИАТ", "mixed": "КРИПТА / ФИАТ", "unknown": "НЕ УКАЗАН"}[metadata.payment_method]
    eligibility = {"allowed": "ДА", "unknown": "НЕИЗВЕСТНО"}[metadata.russia_eligibility]
    fields = [
        "<b>ВЫСОКИЙ ПОТЕНЦИАЛ ДОХОДА</b>" if priority == "HIGH" else None,
        f"<b>{headings[result.category]}</b>", f"<b>{html.escape(job.title or 'БЕЗ НАЗВАНИЯ').upper()}</b>",
        f"<b>ОПЛАТА</b>\n{html.escape(job.salary_raw)}" if job.salary_raw else None,
        f"<b>ФОРМАТ</b>\n{html.escape(' / '.join(x for x in (job.job_type, job.location) if x))}" if job.job_type or job.location else None,
        f"<b>РАБОТА ИЗ РОССИИ</b>\n{eligibility}", f"<b>СПОСОБ ПОЛУЧЕНИЯ ОПЛАТЫ</b>\n{payment}{(': ' + html.escape(metadata.payment_details)) if metadata.payment_details else ''}",
        f"<b>ОПИСАНИЕ</b>\n\n<blockquote>{html.escape(excerpt)}</blockquote>" if excerpt else None,
        f"<b>КАК ОТКЛИКНУТЬСЯ</b>\n{format_application_block(application, job.url)}",
        f"<b>ИСТОЧНИК</b>\n{html.escape(job.source)}",
    ]
    return "\n\n".join(field for field in fields if field)


def _diagnostic_rejection_key(result: FilterResult, *, strong_3d: bool, russia_blocked: bool) -> str:
    if not strong_3d:
        return "no_strong_3d"
    if russia_blocked:
        return "russia_blocked"
    reason = result.reason.casefold()
    if result.category == "self_promo":
        return "self_promo"
    if "платформ" in reason or "platform" in reason:
        return "platform_promo"
    if "исключено" in reason or "negative" in reason:
        return "negative_term"
    if "намерени" in reason or "hire" in reason or "результат" in reason:
        return "no_hiring_intent_or_deliverable"
    return "other_rejected"


def _empty_recovery_counts(fetched_total: int) -> dict[str, int]:
    return {
        "fetched_total": fetched_total, "within_72h": 0,
        "within_72h_strong_3d": 0, "within_72h_not_strong_3d": 0,
        "within_72h_russia_blocked": 0, "within_72h_direct_order": 0,
        "within_72h_freelance_vacancy": 0, "within_72h_job_vacancy": 0,
        "within_72h_self_promo": 0, "within_72h_rejected": 0,
        "duplicates_sent_before": 0, "candidates_before_dedupe": 0,
        "candidates_after_dedupe": 0, "selected": 0, "sent": 0,
    }


def _log_himalayas_diagnostics(counts: dict[str, int], reasons: dict[str, int], *, mode: str) -> None:
    keys = (
        "fetched_total", "within_72h", "within_72h_strong_3d",
        "within_72h_not_strong_3d", "within_72h_russia_blocked",
        "within_72h_direct_order", "within_72h_freelance_vacancy",
        "within_72h_job_vacancy", "within_72h_self_promo",
        "within_72h_rejected", "duplicates_sent_before",
        "candidates_before_dedupe", "candidates_after_dedupe",
        "selected", "sent",
    )
    logging.info(
        "Himalayas %s: fetched_total=%s within_72h=%s within_72h_strong_3d=%s "
        "within_72h_not_strong_3d=%s within_72h_russia_blocked=%s "
        "within_72h_direct_order=%s within_72h_freelance_vacancy=%s "
        "within_72h_job_vacancy=%s within_72h_self_promo=%s within_72h_rejected=%s "
        "duplicates_sent_before=%s candidates_before_dedupe=%s candidates_after_dedupe=%s "
        "selected=%s sent=%s rejection_reasons=%s",
        mode, *(counts[key] for key in keys), dict(sorted(reasons.items())),
    )


async def process_external_provider(
    provider: ExternalProvider,
    state: ExternalState,
    settings: Any,
    send: Callable[[str], Awaitable[None]],
    *,
    now: datetime | None = None,
    evaluator: Callable[[str, str, str], FilterResult] = evaluate,
    recovery: bool = False,
    diagnostic: bool = False,
) -> int:
    """Обрабатывает один provider; ошибка API остаётся локальной для него."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    is_recovery = recovery and provider.name == "Himalayas"
    is_diagnostic = diagnostic and provider.name == "Himalayas"
    if is_recovery and state.recovery_completed():
        logging.info("Himalayas recovery v2 already completed")
        return 0
    if not is_recovery and not is_diagnostic and not state.is_due(provider.name, provider.interval_minutes, current):
        logging.info("Внешний источник %s пока не требует опроса", provider.name)
        return 0
    try:
        jobs = await asyncio.to_thread(provider.fetch_jobs)
    except Exception:
        logging.exception("Не удалось получить объявления %s; остальные источники продолжат работу", provider.name)
        if is_diagnostic:
            raise
        return 0
    if is_diagnostic:
        logging.info("Himalayas diagnostic: fetched_total=%s", len(jobs))

    first_run = not state.is_initialized(provider.name)
    baseline_only = bool(getattr(provider, "baseline_only", False)) and first_run
    cutoff = current - BACKFILL_WINDOW
    sent = 0
    completed = True
    entries: list[tuple[ExternalJob, FilterResult, bool, bool]] = []
    recovery_counts = _empty_recovery_counts(len(jobs))
    rejection_reasons: dict[str, int] = {}
    for job in jobs:
        already_processed = state.is_processed(job)
        if already_processed and not is_recovery and not is_diagnostic:
            continue
        eligible_for_initial = not first_run or (job.published_at is not None and job.published_at >= cutoff)
        within_72h = job.published_at is not None and job.published_at >= cutoff
        if is_recovery or is_diagnostic:
            eligible_for_initial = job.published_at is not None and job.published_at >= cutoff
            if not within_72h:
                continue
            recovery_counts["within_72h"] += 1
        result = evaluator(job.raw_text, "general", "job_board")
        strong_3d = external_3d_relevant(job)
        if not strong_3d:
            result = FilterResult("rejected", "нет сильного признака 3D-роли или 3D-задачи во внешней вакансии", result.price)
        else:
            if is_recovery or is_diagnostic:
                recovery_counts["within_72h_strong_3d"] += 1
        if (is_recovery or is_diagnostic) and not strong_3d:
            recovery_counts["within_72h_not_strong_3d"] += 1
        if job.metadata.russia_eligibility == "blocked":
            result = FilterResult("rejected", "работа явно недоступна из России", result.price)
            if is_recovery or is_diagnostic:
                recovery_counts["within_72h_russia_blocked"] += 1
        if is_recovery or is_diagnostic:
            recovery_counts[f"within_72h_{result.category}"] += 1
            if result.category == "rejected":
                key = _diagnostic_rejection_key(
                    result, strong_3d=strong_3d, russia_blocked=job.metadata.russia_eligibility == "blocked",
                )
                rejection_reasons[key] = rejection_reasons.get(key, 0) + 1
        is_candidate = eligible_for_initial and should_send(result, settings)
        duplicate = is_candidate and state.has_fingerprint(job)
        if is_recovery or is_diagnostic:
            if is_candidate:
                recovery_counts["candidates_before_dedupe"] += 1
            if duplicate:
                recovery_counts["duplicates_sent_before"] += 1
            if is_candidate and not duplicate:
                recovery_counts["candidates_after_dedupe"] += 1
            logging.info(
                "DIAG: title=%r published_at=%s strong_3d=%s filter_category=%s "
                "filter_reason=%r russia=%s duplicate_sent=%s candidate=%s",
                job.title, _timestamp(job.published_at) if job.published_at else "",
                strong_3d, result.category, result.reason, job.metadata.russia_eligibility,
                duplicate, is_candidate and not duplicate,
            )
        entries.append((job, result, is_candidate, duplicate))

    if is_diagnostic:
        _log_himalayas_diagnostics(recovery_counts, rejection_reasons, mode="diagnostic")
        return 0

    selected_initial_ids: set[str] = set()
    selected_initial_rank: dict[str, int] = {}
    if first_run or is_recovery:
        priority_rank = {"HIGH": 0, "NORMAL": 1, "UNKNOWN": 2}
        initial_candidates = [entry for entry in entries if entry[2] and not entry[3]]
        initial_candidates.sort(
            key=lambda entry: (
                priority_rank[opportunity_priority(entry[0])],
                application_rank(entry[0].application_info),
                -(entry[0].published_at.timestamp() if entry[0].published_at else 0),
            )
        )
        selected_initial_rank = {entry[0].external_id: index for index, entry in enumerate(initial_candidates[:MAX_INITIAL_NOTIFICATIONS])}
        selected_initial_ids = set(selected_initial_rank)
        # Избранная десятка должна отправляться именно в ранжированном порядке,
        # а не в том порядке, в котором API вернул объявления.
        entries.sort(key=lambda entry: (0, selected_initial_rank[entry[0].external_id]) if entry[0].external_id in selected_initial_rank else (1, 0))

    for job, result, is_candidate, duplicate in entries:
        try:
            should_notify = is_candidate and not duplicate and not baseline_only and (not (first_run or is_recovery) or job.external_id in selected_initial_ids)
            if should_notify:
                await send(format_external_card(job, result))
                sent += 1
                state.mark_fingerprint(job, current)
            elif is_candidate and duplicate:
                state.mark_fingerprint(job, current)
        except Exception:
            logging.exception("Не удалось отправить внешнее объявление %s/%s; оно будет повторено", job.source, job.external_id)
            completed = False
            continue
        state.mark_processed(job, current)
        state.save()

    # Не сдвигаем интервал, если часть ответа не удалось обработать/доставить:
    # при следующем запуске необработанное объявление будет повторено.
    if completed and not is_recovery:
        state.finish_poll(provider.name, current)
    if completed and is_recovery:
        state.mark_recovery_completed()
        recovery_counts["selected"] = len(selected_initial_ids)
        recovery_counts["sent"] = sent
        _log_himalayas_diagnostics(recovery_counts, rejection_reasons, mode="recovery")
    state.prune(current)
    state.save()
    return sent


async def process_external_sources(
    providers: list[ExternalProvider], state: ExternalState, settings: Any, send: Callable[[str], Awaitable[None]], *, now: datetime | None = None, himalayas_recovery: bool = False, himalayas_diagnostic: bool = False
) -> int:
    total = 0
    for provider in providers:
        try:
            total += await process_external_provider(
                provider, state, settings, send, now=now, recovery=himalayas_recovery,
                diagnostic=himalayas_diagnostic,
            )
        except Exception:
            logging.exception("Ошибка внешнего источника %s не остановила остальные", provider.name)
    return total
