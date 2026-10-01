"""Асинхронный клиент официального Threads API."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


API_BASE = "https://graph.threads.net/v1.0"


@dataclass(frozen=True)
class ThreadPost:
    id: str
    text: str
    username: str
    permalink: str
    timestamp: str | None = None


class ThreadsApiError(RuntimeError):
    """Понятная ошибка официального Threads API."""


class ThreadsClient:
    def __init__(self, access_token: str, *, timeout: float = 25.0) -> None:
        token = access_token.strip()
        if not token:
            raise ValueError("THREADS_ACCESS_TOKEN is required")
        self.access_token = token
        self.timeout = timeout

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        params = dict(kwargs.pop("params", {}) or {})
        params["access_token"] = self.access_token
        async with httpx.AsyncClient(base_url=API_BASE, timeout=self.timeout) as client:
            response = await client.request(method, path, params=params, **kwargs)

        try:
            payload = response.json()
        except ValueError as exc:
            raise ThreadsApiError(
                f"Threads API returned non-JSON response ({response.status_code})"
            ) from exc

        if response.is_error:
            message = payload.get("error", {}).get("message") if isinstance(payload, dict) else None
            raise ThreadsApiError(message or f"Threads API error {response.status_code}")
        if not isinstance(payload, dict):
            raise ThreadsApiError("Unexpected Threads API response")
        return payload

    async def search_recent(self, query: str, *, limit: int = 20) -> list[ThreadPost]:
        payload = await self._request(
            "GET",
            "/keyword_search",
            params={
                "q": query,
                "search_type": "RECENT",
                "search_mode": "KEYWORD",
                "limit": max(1, min(limit, 50)),
                "fields": "id,text,username,permalink,timestamp,is_reply",
            },
        )
        posts: list[ThreadPost] = []
        for item in payload.get("data", []):
            if not isinstance(item, dict):
                continue
            post_id = str(item.get("id") or "").strip()
            text = str(item.get("text") or "").strip()
            if not post_id or not text:
                continue
            posts.append(
                ThreadPost(
                    id=post_id,
                    text=text,
                    username=str(item.get("username") or "").strip(),
                    permalink=str(item.get("permalink") or "").strip(),
                    timestamp=str(item.get("timestamp") or "").strip() or None,
                )
            )
        return posts

    async def create_text_reply(self, post_id: str, text: str) -> str:
        payload = await self._request(
            "POST",
            "/me/threads",
            params={
                "media_type": "TEXT",
                "text": text,
                "reply_to_id": post_id,
            },
        )
        container_id = str(payload.get("id") or "").strip()
        if not container_id:
            raise ThreadsApiError("Threads API did not return a reply container id")
        return container_id

    async def publish_container(self, container_id: str) -> str:
        payload = await self._request(
            "POST",
            "/me/threads_publish",
            params={"creation_id": container_id},
        )
        reply_id = str(payload.get("id") or "").strip()
        if not reply_id:
            raise ThreadsApiError("Threads API did not return a published reply id")
        return reply_id

    async def reply(self, post_id: str, text: str) -> str:
        container_id = await self.create_text_reply(post_id, text)
        return await self.publish_container(container_id)

    async def publishing_quota(self) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/me/threads_publishing_limit",
            params={"fields": "quota_usage,config,reply_quota_usage,reply_config"},
        )
