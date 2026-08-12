from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from external_sources.base import ExternalJob, ExternalState, process_external_sources
from external_sources.http import fetch_json_with_bearer_token
from external_sources.threads import THREADS_KEYWORDS, ThreadsSource, is_russian_post, process_threads_source


NOW = datetime(2026, 8, 12, 12, tzinfo=UTC)
SETTINGS = SimpleNamespace(threads_enabled=True, send_freelance_vacancies=True, send_job_vacancies=True)
RUSSIAN_ORDER = "Ищу Blender-специалиста для создания трех рендеров"


def post(identifier: str, text: str = RUSSIAN_ORDER) -> dict[str, str]:
    return {"id": identifier, "text": text, "permalink": f"https://www.threads.net/@client/post/{identifier}", "timestamp": "2026-08-12T11:30:00Z", "username": "client"}


class ThreadsSourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.state = ExternalState(Path(self.folder.name) / "external_sources.json")
        self.sent: list[str] = []

        async def send(card: str) -> None:
            self.sent.append(card)
        self.send = send

    async def asyncTearDown(self) -> None:
        self.folder.cleanup()

    def source(self, rows: list[dict[str, str]], calls: list[tuple[str, str]] | None = None) -> ThreadsSource:
        def fetcher(url: str, token: str):
            if calls is not None:
                calls.append((url, token))
            return {"data": rows}
        return ThreadsSource("secret-value", fetcher=fetcher)

    def test_official_recent_search_uses_queries_and_hides_token_from_url(self) -> None:
        calls: list[tuple[str, str]] = []
        self.source([], calls).fetch_jobs()
        self.assertEqual(len(calls), len(THREADS_KEYWORDS))
        self.assertTrue(all("search_type=RECENT" in url and "q=" in url for url, _ in calls))
        self.assertTrue(all("secret-value" not in url for url, _ in calls))
        self.assertTrue(all(token == "secret-value" for _, token in calls))
        self.assertTrue(all(url.startswith("https://graph.threads.net/keyword_search?") for url, _ in calls))

    async def test_russian_direct_order_is_sent_once_and_external_id_is_saved(self) -> None:
        source = self.source([post("one")])
        self.assertEqual(await process_threads_source(source, self.state, SETTINGS, self.send, now=NOW), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.state.is_processed(source._job(post("one"))))
        self.assertEqual(await process_threads_source(source, self.state, SETTINGS, self.send, now=NOW + timedelta(minutes=5)), 0)
        self.assertEqual(len(self.sent), 1)

    async def test_english_post_is_rejected_before_order_filter(self) -> None:
        english = "Looking for a Blender artist to create three renders"
        source = self.source([post("english", english)])
        self.assertEqual(await process_threads_source(source, self.state, SETTINGS, self.send, now=NOW), 0)
        self.assertEqual(self.sent, [])
        self.assertTrue(self.state.is_processed(source._job(post("english", english))))

    async def test_threads_api_error_does_not_stop_other_external_source(self) -> None:
        bad = ThreadsSource("secret-value", fetcher=lambda *_: (_ for _ in ()).throw(RuntimeError("Threads unavailable")))
        self.assertEqual(await process_threads_source(bad, self.state, SETTINGS, self.send, now=NOW), 0)

        class GoodProvider:
            name, interval_minutes = "Good", 60
            def fetch_jobs(self):
                return [ExternalJob("Good", "one", "Need a Blender artist to create one product model", "Paid task", "https://example.test/one", NOW, "Studio")]

        self.assertEqual(await process_external_sources([GoodProvider()], self.state, SETTINGS, self.send, now=NOW), 1)
        self.assertEqual(len(self.sent), 1)

    async def test_missing_token_skips_without_changing_state(self) -> None:
        source = ThreadsSource("", fetcher=lambda *_: self.fail("must not call API"))
        before = dict(self.state.values)
        self.assertEqual(await process_threads_source(source, self.state, SETTINGS, self.send, now=NOW), 0)
        self.assertEqual(self.state.values, before)


class ThreadsHttpSecurityTests(unittest.TestCase):
    def test_bearer_token_is_only_in_authorization_header(self) -> None:
        class Response:
            def read(self): return b'{"data": []}'
            def __enter__(self): return self
            def __exit__(self, *_): return False
        with patch("external_sources.http.urlopen", return_value=Response()) as urlopen:
            self.assertEqual(fetch_json_with_bearer_token("https://graph.threads.net/v1.0/keyword_search?q=3D", "top-secret"), {"data": []})
        request = urlopen.call_args.args[0]
        self.assertNotIn("top-secret", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer top-secret")

    def test_cyrillic_marks_russian_or_mixed_posts_only(self) -> None:
        self.assertTrue(is_russian_post("Ищу 3D-моделлера в Blender"))
        self.assertFalse(is_russian_post("Looking for a Blender artist"))
