"""Небольшой HTTP-клиент только для публичных JSON API."""
from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


USER_AGENT = "3D-Job-Radar/1.0 (+https://github.com/)"


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
