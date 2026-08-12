"""Read-only, quota-safe search of recent public Threads posts."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

from filters import FilterResult, evaluate
from work_metadata import analyze_work_metadata

from .base import ExternalJob, ExternalState, format_external_card, should_send
from .http import ThreadsApiError, fetch_json_with_bearer_token


THREADS_KEYWORDS = ("3D", "3д", "Blender", "визуализатор", "смоделировать", "рендер")
# Meta's current Keyword Search reference uses the unversioned Threads Graph host.
THREADS_SEARCH_ENDPOINT = "https://graph.threads.net/keyword_search"
THREADS_ME_ENDPOINT = "https://graph.threads.net/v1.0/me"
THREADS_FIELDS = "id,text,permalink,timestamp,username"
THREADS_MIN_INTERVAL = timedelta(minutes=30)
THREADS_WEEKLY_LIMIT = 400
THREADS_WINDOW = timedelta(days=7)
THREADS_MAX_BACKOFF = timedelta(hours=2)
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


def is_russian_post(text: str) -> bool:
    return bool(_CYRILLIC_RE.search(text))


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _safe_error_fields(error: ThreadsApiError) -> dict[str, Any]:
    payload = error.payload.get("error", error.payload) if isinstance(error.payload, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    return {key: payload.get(key) for key in ("message", "type", "code", "error_subcode", "is_transient", "fbtrace_id")}


class ThreadsSource:
    """Official API client: one keyword request per poll; no writes or replies."""

    name = "Threads"

    def __init__(self, access_token: str, interval_minutes: int = 30, fetcher: Callable[[str, str], Any] = fetch_json_with_bearer_token) -> None:
        self.access_token = access_token
        self.interval_minutes = max(interval_minutes, 30)
        self._fetcher = fetcher

    @staticmethod
    def me_url() -> str:
        return f"{THREADS_ME_ENDPOINT}?{urlencode({'fields': 'id,username'})}"

    @staticmethod
    def request_url(query: str) -> str:
        return f"{THREADS_SEARCH_ENDPOINT}?{urlencode({'q': query, 'search_type': 'RECENT', 'fields': THREADS_FIELDS})}"

    def check_authorization(self) -> dict[str, Any]:
        payload = self._fetcher(self.me_url(), self.access_token)
        return payload if isinstance(payload, dict) else {}

    def fetch_jobs(self, query: str) -> list[ExternalJob]:
        payload = self._fetcher(self.request_url(query), self.access_token)
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        return [self._job(row) for row in rows if isinstance(row, dict) and row.get("id") is not None]

    @staticmethod
    def _job(row: dict[str, Any]) -> ExternalJob:
        text = str(row.get("text") or "").strip()
        title = next((line.strip() for line in text.splitlines() if line.strip()), "Публикация Threads")[:200]
        return ExternalJob("Threads", str(row["id"]), title, text, str(row.get("permalink") or ""), parse_threads_timestamp(row.get("timestamp")), str(row.get("username") or ""), excerpt=text)


def parse_threads_timestamp(value: Any) -> datetime | None:
    return _parse_time(value)


def _threads_state(state: ExternalState) -> dict[str, Any]:
    value = state.values.setdefault("threads", {})
    value.setdefault("next_keyword_index", 0)
    value.setdefault("keyword_request_history", [])
    return value


def _history_within_budget(raw: list[Any], now: datetime) -> list[str]:
    cutoff = now - THREADS_WINDOW
    return [item for item in raw if isinstance(item, str) and (_parse_time(item) or cutoff) >= cutoff]


def _is_due(state: ExternalState, now: datetime, source: ThreadsSource) -> bool:
    values = _threads_state(state)
    history = _history_within_budget(values["keyword_request_history"], now)
    values["keyword_request_history"] = history
    if len(history) >= THREADS_WEEKLY_LIMIT:
        logging.warning("Threads weekly keyword-search budget exhausted: %s/%s", len(history), THREADS_WEEKLY_LIMIT)
        return False
    retry_at = _parse_time(values.get("next_retry_at"))
    if retry_at and now < retry_at:
        return False
    last_attempt = _parse_time(values.get("last_keyword_attempt_at"))
    return last_attempt is None or now - last_attempt >= timedelta(minutes=source.interval_minutes)


def _record_keyword_attempt(state: ExternalState, now: datetime) -> str:
    values = _threads_state(state)
    index = int(values["next_keyword_index"]) % len(THREADS_KEYWORDS)
    values["next_keyword_index"] = (index + 1) % len(THREADS_KEYWORDS)
    values["keyword_request_history"] = _history_within_budget(values["keyword_request_history"], now) + [now.isoformat()]
    values["last_keyword_attempt_at"] = now.isoformat()
    return THREADS_KEYWORDS[index]


def _record_transient_error(state: ExternalState, now: datetime) -> None:
    values = _threads_state(state)
    failures = int(values.get("consecutive_transient_errors", 0)) + 1
    values["consecutive_transient_errors"] = failures
    delay = max(THREADS_MIN_INTERVAL, timedelta(minutes=5 * (2 ** min(failures - 1, 5))))
    values["next_retry_at"] = (now + min(delay, THREADS_MAX_BACKOFF)).isoformat()


async def process_threads_source(source: ThreadsSource, state: ExternalState, settings: Any, send: Callable[[str], Awaitable[None]], *, now: datetime | None = None, evaluator: Callable[[str, str, str], FilterResult] = evaluate) -> int:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if not getattr(settings, "threads_enabled", False):
        return 0
    if not source.access_token:
        logging.warning("Threads enabled but THREADS_ACCESS_TOKEN is absent; source skipped")
        return 0
    values = _threads_state(state)
    # A successful /me is a separate, low-frequency authorization preflight.
    last_auth = _parse_time(values.get("last_authorization_check_at"))
    if last_auth is None or current - last_auth >= timedelta(days=1):
        try:
            me = await asyncio.to_thread(source.check_authorization)
            values["last_authorization_check_at"] = current.isoformat()
            logging.info("Threads authorization check succeeded: account_id_present=%s username_present=%s", bool(me.get("id")), bool(me.get("username")))
        except ThreadsApiError as exc:
            logging.error("Threads authorization check failed: %s", _safe_error_fields(exc))
            state.save()
            return 0
        except Exception:
            logging.exception("Threads authorization check failed without exposing credentials")
            return 0
    if not _is_due(state, current, source):
        state.save()
        return 0

    query = _record_keyword_attempt(state, current)
    try:
        jobs = await asyncio.to_thread(source.fetch_jobs, query)
    except ThreadsApiError as exc:
        fields = _safe_error_fields(exc)
        logging.error("Threads keyword search failed: query=%r http_status=%s error=%s", query, exc.status, fields)
        if exc.is_transient:
            _record_transient_error(state, current)
        state.save()
        return 0
    except Exception:
        logging.exception("Threads keyword search failed without exposing credentials")
        _record_transient_error(state, current)
        state.save()
        return 0

    values["consecutive_transient_errors"] = 0
    values.pop("next_retry_at", None)
    sent = 0
    completed = True
    for job in jobs:
        if state.is_processed(job):
            continue
        try:
            result = FilterResult("rejected", "публикация не на русском языке", "не указана") if not is_russian_post(job.raw_text) else evaluator(job.raw_text, "general", "mixed")
            if analyze_work_metadata(job.raw_text).russia_eligibility == "blocked":
                result = FilterResult("rejected", "работа явно недоступна из России", result.price)
            if should_send(result, settings):
                await send(format_external_card(job, result))
                state.mark_fingerprint(job, current)
                sent += 1
        except Exception:
            logging.exception("Threads post %s was not delivered and will be retried", job.external_id)
            completed = False
            continue
        state.mark_processed(job, current)
    if completed:
        state.finish_poll(source.name, current)
    state.prune(current)
    state.save()
    logging.info("Threads keyword search succeeded: query=%r received=%s sent=%s", query, len(jobs), sent)
    return sent
