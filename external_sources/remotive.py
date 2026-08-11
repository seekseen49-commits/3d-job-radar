"""Чтение публичной выдачи Remotive без авторизации."""
from __future__ import annotations

from typing import Any, Callable

from .base import ExternalJob, contract_duration_from_text, html_to_text, parse_datetime
from .http import fetch_json


class RemotiveSource:
    name = "Remotive"
    url = "https://remotive.com/api/remote-jobs"

    def __init__(self, interval_minutes: int = 360, fetcher: Callable[[str], Any] = fetch_json) -> None:
        self.interval_minutes = interval_minutes
        self._fetcher = fetcher

    def fetch_jobs(self) -> list[ExternalJob]:
        payload = self._fetcher(self.url)
        rows = payload.get("jobs", []) if isinstance(payload, dict) else []
        return [self._job(row) for row in rows if isinstance(row, dict) and row.get("id") is not None]

    @staticmethod
    def _job(row: dict[str, Any]) -> ExternalJob:
        salary = html_to_text(row.get("salary"))
        title, description = html_to_text(row.get("title")), html_to_text(row.get("description"))
        return ExternalJob("Remotive", str(row["id"]), title, description, str(row.get("url") or ""), parse_datetime(row.get("publication_date")), html_to_text(row.get("company_name")), html_to_text(row.get("job_type")), html_to_text(row.get("candidate_required_location")), salary, "", *_salary_values(salary), contract_duration_from_text(title, description))


def _salary_values(salary: str) -> tuple[float | None, float | None, str, str]:
    """Remotive отдаёт compensation одной строкой; извлекаем безопасно, без конвертации валют."""
    import re
    numbers = [float(value.replace(",", "")) for value in re.findall(r"\d[\d,]*(?:\.\d+)?", salary)]
    currency = next((value for value in ("USD", "EUR", "GBP", "CAD", "AUD") if value.casefold() in salary.casefold()), "")
    period = "year" if re.search(r"\b(?:year|annual)\b", salary, re.IGNORECASE) else "hour" if re.search(r"\b(?:hour|hr)\b", salary, re.IGNORECASE) else "month" if re.search(r"\bmonth\b", salary, re.IGNORECASE) else ""
    return (numbers[0] if numbers else None, numbers[-1] if numbers else None, currency, period)
