"""Быстрый публичный источник Himalayas через официальный MCP transport."""
from __future__ import annotations
import asyncio
import json
from typing import Any

from .base import ExternalJob, contract_duration_from_text, html_to_text, parse_datetime


class HimalayasMcpSource:
    name = "Himalayas MCP"
    endpoint = "https://mcp.himalayas.app/mcp"
    baseline_only = True

    def __init__(self, interval_minutes: int = 10, caller=None) -> None:
        self.interval_minutes = interval_minutes
        self._caller = caller or self._call_get_jobs

    def fetch_jobs(self) -> list[ExternalJob]:
        payload = self._caller()
        rows = payload.get("jobs", payload.get("results", [])) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            raise ValueError("MCP get_jobs returned no jobs list")
        return [self._job(row) for row in rows if isinstance(row, dict)]

    @staticmethod
    def _call_get_jobs() -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
            async with streamablehttp_client(HimalayasMcpSource.endpoint, timeout=20) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("get_jobs", {})
                    structured = getattr(result, "structuredContent", None)
                    if isinstance(structured, dict): return structured
                    for item in getattr(result, "content", []):
                        text = getattr(item, "text", None)
                        if text:
                            try: return json.loads(text)
                            except json.JSONDecodeError: continue
                    raise ValueError("MCP get_jobs response has no structured jobs")
        return asyncio.run(call())

    @staticmethod
    def _job(row: dict[str, Any]) -> ExternalJob:
        title, description = html_to_text(row.get("title")), html_to_text(row.get("description") or row.get("excerpt"))
        url = str(row.get("url") or row.get("jobUrl") or row.get("canonicalUrl") or "")
        application = str(row.get("applicationLink") or "")
        location = row.get("locationRestrictions") or row.get("location") or ""
        if isinstance(location, list): location = ", ".join(map(str, location))
        identifier = str(row.get("id") or row.get("guid") or url or f"{row.get('companyName','')}:{title}")
        return ExternalJob("Himalayas", identifier, title, description, url or application, parse_datetime(row.get("pubDate") or row.get("publishedAt")), html_to_text(row.get("companyName") or row.get("company")), html_to_text(row.get("employmentType") or row.get("jobType")), html_to_text(location), str(row.get("salary") or ""), html_to_text(row.get("excerpt")), _number(row.get("minSalary")), _number(row.get("maxSalary")), str(row.get("currency") or ""), str(row.get("salaryPeriod") or ""), contract_duration_from_text(title, description), application)

def _number(value: Any) -> float | None:
    try: return float(value) if value not in (None, "") else None
    except (TypeError, ValueError): return None
