"""Чтение публичной выдачи Jobicy без авторизации."""
from __future__ import annotations

from typing import Any, Callable

from .base import ExternalJob, contract_duration_from_text, html_to_text, parse_datetime
from .http import fetch_json


class JobicySource:
    name = "Jobicy"
    url = "https://jobicy.com/api/v2/remote-jobs?count=100"

    def __init__(self, interval_minutes: int = 60, fetcher: Callable[[str], Any] = fetch_json) -> None:
        self.interval_minutes = interval_minutes
        self._fetcher = fetcher

    def fetch_jobs(self) -> list[ExternalJob]:
        payload = self._fetcher(self.url)
        rows = payload.get("jobs", []) if isinstance(payload, dict) else []
        return [self._job(row) for row in rows if isinstance(row, dict) and row.get("id") is not None]

    @staticmethod
    def _job(row: dict[str, Any]) -> ExternalJob:
        job_type = row.get("jobType")
        if isinstance(job_type, list):
            job_type = ", ".join(str(value) for value in job_type)
        minimum, maximum = row.get("salaryMin"), row.get("salaryMax")
        salary = " ".join(str(part) for part in (minimum, "–" if maximum else "", maximum, row.get("salaryCurrency"), row.get("salaryPeriod")) if part not in (None, "")).strip()
        description = html_to_text(row.get("jobDescription"))
        title = html_to_text(row.get("jobTitle"))
        return ExternalJob("Jobicy", str(row["id"]), title, description, str(row.get("url") or ""), parse_datetime(row.get("pubDate")), html_to_text(row.get("companyName")), html_to_text(job_type), html_to_text(row.get("jobGeo")), salary, html_to_text(row.get("jobExcerpt")), _number(minimum), _number(maximum), str(row.get("salaryCurrency") or ""), str(row.get("salaryPeriod") or ""), contract_duration_from_text(title, description))


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None
