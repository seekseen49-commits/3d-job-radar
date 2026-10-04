"""Асинхронный клиент официального Threads API."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx


API_BASE = "https://graph.threads.net"


@dataclass(frozen=True)
class ThreadPost:
    id: str
    text: str
    username: str
    permalink: str
    timestamp: str | None = None
    media_type: str | None = None
    media_url: str | None = None
    thumbnail_url: str | None = None
    alt_text: str | None = None
    has_replies: bool = False
    image_urls: tuple[str, ...] = ()


class ThreadsApiError(RuntimeError):
    """Понятная ошибка официального Threads API."""

    def __init__(self, message: str, *, status: int | None = None, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


class ThreadsClient:
    def __init__(self, access_token: str, *, timeout: float = 25.0) -> None:
        token = access_token.strip()
        if not token:
            raise ValueError("THREADS_ACCESS_TOKEN is required")
        self.access_token = token
        self.timeout = timeout

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {self.access_token}"
        async with httpx.AsyncClient(base_url=API_BASE, timeout=self.timeout) as client:
            response = await client.request(method, path, headers=headers, **kwargs)

        try:
            payload = response.json()
        except ValueError as exc:
            raise ThreadsApiError(
                f"Threads API returned non-JSON response ({response.status_code})",
                status=response.status_code,
            ) from exc

        if response.is_error:
            message = payload.get("error", {}).get("message") if isinstance(payload, dict) else None
            raise ThreadsApiError(
                message or f"Threads API error {response.status_code}",
                status=response.status_code,
                payload=payload if isinstance(payload, dict) else {},
            )
        if not isinstance(payload, dict):
            raise ThreadsApiError("Unexpected Threads API response", status=response.status_code)
        return payload

    async def me(self) -> dict[str, Any]:
        return await self._request("GET", "/v1.0/me", params={"fields": "id,username"})

    async def search_recent(self, query: str, *, limit: int = 20) -> list[ThreadPost]:
        # Keyword Search в Threads использует отдельный endpoint без /v1.0.
        payload = await self._request(
            "GET",
            "/keyword_search",
            params={
                "q": query,
                "search_type": "RECENT",
                "limit": max(1, min(limit, 50)),
                "fields": "id,text,username,permalink,timestamp,media_type,media_url,thumbnail_url,alt_text,has_replies,children",
            },
        )
        posts: list[ThreadPost] = []
        for item in payload.get("data", [])[: max(1, min(limit, 50))]:
            if not isinstance(item, dict):
                continue
            post_id = str(item.get("id") or "").strip()
            text = str(item.get("text") or "").strip()
            if not post_id or not text:
                continue
            media_type = str(item.get("media_type") or "").strip() or None
            media_url = str(item.get("media_url") or "").strip() or None
            thumbnail_url = str(item.get("thumbnail_url") or "").strip() or None
            image_urls: list[str] = []

            if media_type and "IMAGE" in media_type.upper() and media_url:
                image_urls.append(media_url)
            elif thumbnail_url:
                image_urls.append(thumbnail_url)

            children = item.get("children")
            if isinstance(children, dict):
                for child in children.get("data", [])[:3]:
                    if not isinstance(child, dict):
                        continue
                    child_type = str(child.get("media_type") or "").upper()
                    child_media = str(child.get("media_url") or "").strip()
                    child_thumb = str(child.get("thumbnail_url") or "").strip()
                    if "IMAGE" in child_type and child_media:
                        image_urls.append(child_media)
                    elif child_thumb:
                        image_urls.append(child_thumb)

            posts.append(
                ThreadPost(
                    id=post_id,
                    text=text,
                    username=str(item.get("username") or "").strip(),
                    permalink=str(item.get("permalink") or "").strip(),
                    timestamp=str(item.get("timestamp") or "").strip() or None,
                    media_type=media_type,
                    media_url=media_url,
                    thumbnail_url=thumbnail_url,
                    alt_text=str(item.get("alt_text") or "").strip() or None,
                    has_replies=bool(item.get("has_replies")),
                    image_urls=tuple(dict.fromkeys(image_urls))[:3],
                )
            )
        return posts

    async def get_replies(self, post_id: str, *, limit: int = 6) -> list[str]:
        payload = await self._request(
            "GET",
            f"/{post_id}/replies",
            params={
                "fields": "id,text,timestamp,username",
                "reverse": "false",
                "limit": max(1, min(limit, 20)),
            },
        )
        replies: list[str] = []
        for item in payload.get("data", [])[: max(1, min(limit, 20))]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if text:
                replies.append(text)
        return replies

    async def create_text_reply(self, post_id: str, text: str) -> str:
        payload = await self._request(
            "POST",
            "/v1.0/me/threads",
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
            "/v1.0/me/threads_publish",
            params={"creation_id": container_id},
        )
        reply_id = str(payload.get("id") or "").strip()
        if not reply_id:
            raise ThreadsApiError("Threads API did not return a published reply id")
        return reply_id

    async def reply(self, post_id: str, text: str) -> str:
        container_id = await self.create_text_reply(post_id, text)
        last_error: Exception | None = None
        # Текстовый контейнер обычно готов сразу, но API иногда отвечает раньше,
        # чем контейнер становится публикуемым.
        for delay in (0.0, 1.0, 2.0):
            if delay:
                await asyncio.sleep(delay)
            try:
                return await self.publish_container(container_id)
            except ThreadsApiError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error
