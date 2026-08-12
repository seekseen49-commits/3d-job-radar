"""Read-only search of recent public Threads posts through the official API."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

from filters import FilterResult, evaluate
from work_metadata import analyze_work_metadata

from .base import ExternalJob, ExternalState, format_external_card, should_send
from .http import fetch_json_with_bearer_token


THREADS_KEYWORDS = ("3D", "3д", "Blender", "визуализатор", "смоделировать", "рендер")
THREADS_SEARCH_ENDPOINT = "https://graph.threads.net/keyword_search"
THREADS_FIELDS = "id,text,permalink,timestamp,username"
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


def is_russian_post(text: str) -> bool:
    """A Russian post contains Cyrillic; mixed Russian text is allowed."""
    return bool(_CYRILLIC_RE.search(text))


class ThreadsSource:
    """Official Threads keyword search. It never writes, replies, or publishes."""

    name = "Threads"

    def __init__(
        self,
        access_token: str,
        interval_minutes: int = 5,
        fetcher: Callable[[str, str], Any] = fetch_json_with_bearer_token,
    ) -> None:
        self.access_token = access_token
        self.interval_minutes = interval_minutes
        self._fetcher = fetcher
        self.had_query_error = False

    def request_url(self, query: str) -> str:
        params = {"q": query, "search_type": "RECENT", "fields": THREADS_FIELDS}
        return f"{THREADS_SEARCH_ENDPOINT}?{urlencode(params)}"

    def fetch_jobs(self) -> list[ExternalJob]:
        jobs: list[ExternalJob] = []
        seen_ids: set[str] = set()
        self.had_query_error = False
        for query in THREADS_KEYWORDS:
            try:
                payload = self._fetcher(self.request_url(query), self.access_token)
            except Exception as exc:
                self.had_query_error = True
                logging.warning("Threads keyword query failed (%s); remaining queries continue", exc)
                continue
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            for row in rows:
                if not isinstance(row, dict) or row.get("id") is None:
                    continue
                job = self._job(row)
                if job.external_id not in seen_ids:
                    seen_ids.add(job.external_id)
                    jobs.append(job)
        return jobs

    @staticmethod
    def _job(row: dict[str, Any]) -> ExternalJob:
        text = str(row.get("text") or "").strip()
        title = next((line.strip() for line in text.splitlines() if line.strip()), "Публикация Threads")[:200]
        return ExternalJob(
            "Threads", str(row["id"]), title, text, str(row.get("permalink") or ""),
            parse_threads_timestamp(row.get("timestamp")), str(row.get("username") or ""), excerpt=text,
        )


def parse_threads_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


async def process_threads_source(
    source: ThreadsSource,
    state: ExternalState,
    settings: Any,
    send: Callable[[str], Awaitable[None]],
    *,
    now: datetime | None = None,
    evaluator: Callable[[str, str, str], FilterResult] = evaluate,
) -> int:
    """Process unique Russian posts; failed sends do not advance their IDs."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if not getattr(settings, "threads_enabled", False):
        return 0
    if not source.access_token:
        logging.warning("Threads enabled but THREADS_ACCESS_TOKEN is absent; source skipped")
        return 0
    if not state.is_due(source.name, source.interval_minutes, current):
        logging.info("Threads source is not due yet")
        return 0
    try:
        jobs = await asyncio.to_thread(source.fetch_jobs)
    except Exception:
        logging.exception("Threads API request failed; other sources continue")
        return 0

    sent = 0
    completed = not source.had_query_error
    for job in jobs:
        if state.is_processed(job):
            continue
        try:
            if not is_russian_post(job.raw_text):
                result = FilterResult("rejected", "публикация не на русском языке", "не указана")
            else:
                result = evaluator(job.raw_text, "general", "mixed")
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
        state.save()
    if completed:
        state.finish_poll(source.name, current)
    state.prune(current)
    state.save()
    logging.info("Threads search completed: received=%s sent=%s", len(jobs), sent)
    return sent
