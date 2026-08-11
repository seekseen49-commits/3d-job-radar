"""Чтение публичного API Himalayas без авторизации."""
from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlencode

from .base import ExternalJob, contract_duration_from_text, html_to_text, parse_datetime
from .http import fetch_json


class HimalayasSource:
    name = "Himalayas"
    endpoint = "https://himalayas.app/jobs/api/search"
    queries = (
        "3D artist", "3D modeler", "Blender", "architectural visualization", "product visualization",
        "CAD modeler", "Unreal Engine artist", "environment artist", "Cinema 4D", "3ds Max", "Maya",
    )

    def __init__(self, interval_minutes: int = 1440, fetcher: Callable[[str], Any] = fetch_json) -> None:
        self.interval_minutes = interval_minutes
        self._fetcher = fetcher

    def fetch_jobs(self) -> list[ExternalJob]:
        result: list[ExternalJob] = []
        identifiers: set[str] = set()
        for query in self.queries:
            payload = self._fetcher(f"{self.endpoint}?{urlencode({'q': query, 'sort': 'recent'})}")
            rows = payload.get("jobs", payload.get("data", [])) if isinstance(payload, dict) else []
            for row in rows:
                if not isinstance(row, dict) or row.get("guid") is None or str(row["guid"]) in identifiers:
                    continue
                identifiers.add(str(row["guid"]))
                result.append(self._job(row))
        return result

    @staticmethod
    def _job(row: dict[str, Any]) -> ExternalJob:
        salary_parts = [row.get("minSalary"), "–" if row.get("maxSalary") else None, row.get("maxSalary"), row.get("currency"), row.get("salaryPeriod")]
        salary = " ".join(str(part) for part in salary_parts if part not in (None, ""))
        location = row.get("locationRestrictions")
        if isinstance(location, list):
            location = ", ".join(str(part) for part in location)
        title, description = html_to_text(row.get("title")), html_to_text(row.get("description"))
        application_link = str(row.get("applicationLink") or "")
        source_url = str(row.get("url") or row.get("jobUrl") or application_link)
        return ExternalJob("Himalayas", str(row["guid"]), title, description, source_url, parse_datetime(row.get("pubDate")), html_to_text(row.get("companyName")), html_to_text(row.get("employmentType")), html_to_text(location), salary, html_to_text(row.get("excerpt")), _number(row.get("minSalary")), _number(row.get("maxSalary")), str(row.get("currency") or ""), str(row.get("salaryPeriod") or ""), contract_duration_from_text(title, description), application_link)


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None
