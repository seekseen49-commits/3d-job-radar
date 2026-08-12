"""Небольшой HTTP-клиент только для публичных JSON API."""
from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


USER_AGENT = "3D-Job-Radar/1.0 (+https://github.com/)"


def _safe_api_error_detail(error: HTTPError) -> str:
    """Return Meta's public error classification without URLs, headers, or secrets."""
    try:
        payload = json.loads(error.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    data = payload.get("error", payload) if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return ""
    fields = [str(data[key]).strip() for key in ("type", "code", "message") if data.get(key) not in (None, "")]
    return "; ".join(fields)[:500]


def fetch_json_with_bearer_token(url: str, token: str, timeout: int = 20, retries: int = 1) -> Any:
    """Fetch JSON with an OAuth bearer token kept out of the URL and logs."""
    last_error: Exception | None = None
    last_detail = ""
    for attempt in range(retries + 1):
        try:
            request = Request(
                url,
                headers={"Accept": "application/json", "User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"},
            )
            with urlopen(request, timeout=timeout) as response:  # nosec B310: fixed official API URL
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            last_error = exc
            detail = _safe_api_error_detail(exc)
            last_detail = detail
            if exc.code != 429 and not 500 <= exc.code < 600:
                suffix = f": {detail}" if detail else ""
                raise RuntimeError(f"Threads API request rejected (HTTP {exc.code}){suffix}") from exc
        except URLError as exc:
            last_error = exc
        if attempt < retries:
            time.sleep(1)
    status = last_error.code if isinstance(last_error, HTTPError) else None
    suffix = f" (HTTP {status})" if status is not None else ""
    if last_detail:
        suffix += f": {last_detail}"
    raise RuntimeError(f"Threads API temporarily unavailable{suffix}") from last_error


def fetch_json(url: str, timeout: int = 20, retries: int = 1) -> Any:
    """Выполняет не более двух попыток при 429/5xx и не содержит секретов."""
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
            with urlopen(request, timeout=timeout) as response:  # nosec B310: public fixed API URLs
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            last_error = exc
            if exc.code != 429 and not 500 <= exc.code < 600:
                raise
        except URLError as exc:
            last_error = exc
        if attempt < retries:
            time.sleep(1)
    raise RuntimeError("Публичный API временно недоступен") from last_error
