from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import patch

from external_sources.base import ExternalState
from external_sources.http import ThreadsApiError, fetch_json_with_bearer_token
from external_sources.threads import (
    THREADS_KEYWORDS,
    THREADS_WEEKLY_LIMIT,
    ThreadsSource,
    _safe_error_fields,
    process_threads_source,
)


NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)
SETTINGS = SimpleNamespace(
    threads_enabled=True,
    send_freelance_vacancies=True,
    send_job_vacancies=True,
)
ORDER = "Ищу Blender-специалиста для создания трех рендеров"


def post(identifier: str, text: str = ORDER) -> dict[str, str]:
    return {
        "id": identifier,
        "text": text,
        "permalink": f"https://www.threads.net/@client/post/{identifier}",
        "timestamp": "2026-08-12T11:30:00Z",
        "username": "client",
    }


class ThreadsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.state = ExternalState(Path(self.folder.name) / "external_sources.json")
        self.sent: list[str] = []

        async def send(card: str) -> None:
            self.sent.append(card)

        self.send = send

    async def asyncTearDown(self) -> None:
        self.folder.cleanup()

    def source(self, fetcher):
        return ThreadsSource("secret-token", fetcher=fetcher)

    async def test_successful_me_then_one_recent_keyword_search(self) -> None:
        calls: list[tuple[str, str]] = []

        def fetcher(url: str, token: str):
            calls.append((url, token))
            return {"id": "me", "username": "radar"} if "/me?" in url else {"data": [post("one")]}

        self.assertEqual(await process_threads_source(self.source(fetcher), self.state, SETTINGS, self.send, now=NOW), 1)
        self.assertEqual(len(calls), 2)
        self.assertIn("fields=id%2Cusername", calls[0][0])
        self.assertIn("search_type=RECENT", calls[1][0])
        self.assertEqual(len(self.sent), 1)

    async def test_authorization_error_stops_before_keyword_search(self) -> None:
        error = ThreadsApiError(403, {"error": {"message": "Invalid OAuth", "type": "OAuthException", "code": 190}})
        with self.assertLogs(level="ERROR") as logs:
            sent = await process_threads_source(
                self.source(lambda *_: (_ for _ in ()).throw(error)),
                self.state,
                SETTINGS,
                self.send,
                now=NOW,
            )
        self.assertEqual(sent, 0)
        self.assertIn("OAuthException", "\n".join(logs.output))
        self.assertEqual(self.sent, [])

    async def test_temporary_http_500_records_backoff_and_only_one_query(self) -> None:
        calls: list[str] = []
        error = ThreadsApiError(500, {"error": {"message": "Internal", "type": "OAuthException", "code": 2, "is_transient": True}})

        def fetcher(url: str, _token: str):
            calls.append(url)
            return {"id": "me", "username": "radar"} if "/me?" in url else (_ for _ in ()).throw(error)

        await process_threads_source(self.source(fetcher), self.state, SETTINGS, self.send, now=NOW)
        self.assertEqual(len(calls), 2)
        self.assertIn("next_retry_at", self.state.values["threads"])
        self.assertEqual(len(self.state.values["threads"]["keyword_request_history"]), 1)

    async def test_keywords_rotate_and_weekly_limit_is_enforced(self) -> None:
        queries: list[str] = []

        def fetcher(url: str, _token: str):
            if "/me?" in url:
                return {"id": "me", "username": "radar"}
            queries.append(url)
            return {"data": []}

        source = self.source(fetcher)
        await process_threads_source(source, self.state, SETTINGS, self.send, now=NOW)
        await process_threads_source(source, self.state, SETTINGS, self.send, now=NOW + timedelta(minutes=30))
        self.assertIn("q=3D", queries[0])
        self.assertIn("q=3%D0%B4", queries[1])
        state = self.state.values["threads"]
        state["keyword_request_history"] = [(NOW - timedelta(days=1)).isoformat()] * THREADS_WEEKLY_LIMIT
        await process_threads_source(source, self.state, SETTINGS, self.send, now=NOW + timedelta(hours=1))
        self.assertEqual(len(queries), 2)

    async def test_duplicate_and_english_posts_are_not_sent_twice(self) -> None:
        def fetcher(url: str, _token: str):
            return {"id": "me", "username": "radar"} if "/me?" in url else {"data": [post("one"), post("english", "Looking for a Blender artist")]}

        source = self.source(fetcher)
        await process_threads_source(source, self.state, SETTINGS, self.send, now=NOW)
        await process_threads_source(source, self.state, SETTINGS, self.send, now=NOW + timedelta(minutes=30))
        self.assertEqual(len(self.sent), 1)

    def test_safe_json_error_fields_and_no_token_or_url_logging(self) -> None:
        error = ThreadsApiError(400, {"error": {"message": "Missing permission", "type": "OAuthException", "code": 10, "error_subcode": 123, "is_transient": False, "fbtrace_id": "trace"}})
        fields = _safe_error_fields(error)
        self.assertEqual(fields, {"message": "Missing permission", "type": "OAuthException", "code": 10, "error_subcode": 123, "is_transient": False, "fbtrace_id": "trace"})
        self.assertNotIn("secret-token", str(fields))


class ThreadsHttpSecurityTests(unittest.TestCase):
    def test_bearer_token_stays_out_of_url(self) -> None:
        class Response:
            def read(self):
                return b'{"data": []}'

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        with patch("external_sources.http.urlopen", return_value=Response()) as urlopen:
            fetch_json_with_bearer_token("https://graph.threads.net/keyword_search?q=3D", "top-secret")
        request = urlopen.call_args.args[0]
        self.assertNotIn("top-secret", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer top-secret")

    def test_http_error_has_safe_payload(self) -> None:
        error = HTTPError("https://graph.threads.net/keyword_search?secret", 500, "Error", None, BytesIO(b'{"error":{"code":2,"is_transient":true}}'))
        with patch("external_sources.http.urlopen", side_effect=error):
            with self.assertRaises(ThreadsApiError) as caught:
                fetch_json_with_bearer_token("https://graph.threads.net/keyword_search?q=3D", "top-secret")
        self.assertEqual(caught.exception.status, 500)
        self.assertTrue(caught.exception.is_transient)
