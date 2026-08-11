import copy
from datetime import UTC, datetime, timedelta
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from external_sources.base import ExternalJob, ExternalState
from filters import FilterResult
import run_once as run_once_module
from run_once import LastSeenState, Source, process_source, process_sources, run_himalayas_diagnostic


class Message:
    def __init__(self, message_id: int, text: str = "") -> None:
        self.id = message_id
        self.raw_text = text
        self.date = None


class FakeClient:
    def __init__(self, messages_by_channel: dict[int, list[Message]], broken_channels: set[int] | None = None) -> None:
        self.messages_by_channel = messages_by_channel
        self.broken_channels = broken_channels or set()

    async def iter_messages(self, channel_id, limit=None, min_id=None, reverse=False):
        if channel_id in self.broken_channels:
            raise OSError("temporary channel error")
        messages = self.messages_by_channel.get(channel_id, [])
        if limit:
            if messages:
                yield messages[-1]
            return
        for message in messages:
            if min_id is None or message.id > min_id:
                yield message


class DiagnosticProvider:
    name = "Himalayas"
    interval_minutes = 60

    def __init__(self, jobs=None, error: Exception | None = None) -> None:
        self.jobs = jobs or []
        self.error = error

    def fetch_jobs(self):
        if self.error:
            raise self.error
        return self.jobs


def settings(freelance=True, jobs=False):
    return SimpleNamespace(send_freelance_vacancies=freelance, send_job_vacancies=jobs)


def result_for(category: str) -> FilterResult:
    return FilterResult(category, category, "не указана")


class RunOnceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.state = LastSeenState(Path(self.folder.name) / "last_seen.json")
        self.source = Source(-1001, "Test", None, "general", "mixed")
        self.sent: list[str] = []

        async def send(card: str) -> None:
            self.sent.append(card)
        self.send = send

    async def asyncTearDown(self):
        self.folder.cleanup()

    async def test_first_run_sets_boundary_without_sending_history(self):
        client = FakeClient({-1001: [Message(10, "old"), Message(11, "old") ]})
        count = await process_source(self.source, client, self.state, settings(), self.send, lambda *_: result_for("direct_order"))
        self.assertEqual(count, 0)
        self.assertEqual(self.state.get(-1001), 11)
        self.assertEqual(self.sent, [])

    async def test_new_message_is_sent_once_and_state_is_updated(self):
        self.state.set(-1001, 10)
        self.state.save()
        client = FakeClient({-1001: [Message(11, "new")]})
        count = await process_source(self.source, client, self.state, settings(), self.send, lambda *_: result_for("direct_order"))
        self.assertEqual(count, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.state.get(-1001), 11)
        await process_source(self.source, client, self.state, settings(), self.send, lambda *_: result_for("direct_order"))
        self.assertEqual(len(self.sent), 1)

    async def test_notification_categories(self):
        self.state.set(-1001, 10)
        client = FakeClient({-1001: [Message(11, "direct"), Message(12, "freelance"), Message(13, "job"), Message(14, "promo"), Message(15, "bad")]})
        categories = {"direct": "direct_order", "freelance": "freelance_vacancy", "job": "job_vacancy", "promo": "self_promo", "bad": "rejected"}
        await process_source(self.source, client, self.state, settings(freelance=True, jobs=False), self.send, lambda text, *_: result_for(categories[text]))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.state.get(-1001), 15)

    async def test_no_new_messages_sends_nothing(self):
        self.state.set(-1001, 10)
        client = FakeClient({-1001: [Message(10, "old")]})
        self.assertEqual(await process_source(self.source, client, self.state, settings(), self.send, lambda *_: result_for("direct_order")), 0)
        self.assertEqual(self.sent, [])

    async def test_one_channel_error_does_not_stop_other_channels(self):
        second = Source(-1002, "Second", None, "general", "mixed")
        self.state.set(-1001, 1)
        self.state.set(-1002, 1)
        client = FakeClient({-1002: [Message(2, "Need a Blender artist to create one product model")]}, broken_channels={-1001})
        processed = await process_sources([self.source, second], client, self.state, settings(), self.send)
        self.assertEqual(processed, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.state.get(-1002), 2)

    async def test_send_failure_does_not_advance_state(self):
        self.state.set(-1001, 10)
        client = FakeClient({-1001: [Message(11, "new")]})
        async def failing_send(_: str) -> None:
            raise RuntimeError("bot API failure")
        await process_source(self.source, client, self.state, settings(), failing_send, lambda *_: result_for("direct_order"))
        self.assertEqual(self.state.get(-1001), 10)


class HimalayasDiagnosticEntrypointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.state = ExternalState(Path(self.folder.name) / "external_sources.json")
        self.settings = SimpleNamespace(
            log_level="INFO",
            himalayas_poll_interval_minutes=60,
            send_freelance_vacancies=True,
            send_job_vacancies=True,
        )
        self.now = datetime.now(UTC)

    async def asyncTearDown(self):
        self.folder.cleanup()

    async def test_diagnostic_entrypoint_has_visible_start_diag_summary_and_done(self):
        item = ExternalJob(
            "Himalayas", "diag-1", "Need a Blender artist to create one 3D model",
            "Paid task", "https://example.test/diag-1", self.now - timedelta(hours=1), "Studio",
        )
        before = copy.deepcopy(self.state.values)
        with self.assertLogs(level="INFO") as logs:
            await run_himalayas_diagnostic(
                self.settings, provider=DiagnosticProvider([item]), state=self.state,
            )
        output = "\n".join(logs.output)
        self.assertIn("Himalayas diagnostic mode: START", output)
        self.assertIn("Himalayas diagnostic: fetching public jobs...", output)
        self.assertIn("Himalayas diagnostic: fetched_total=1", output)
        self.assertIn("DIAG:", output)
        self.assertIn("Himalayas diagnostic:", output)
        self.assertIn("Himalayas diagnostic mode: DONE", output)
        self.assertEqual(self.state.values, before)

    async def test_diagnostic_fetch_error_is_visible_and_reraised(self):
        with self.assertLogs(level="ERROR") as logs:
            with self.assertRaisesRegex(RuntimeError, "diagnostic fetch failed"):
                await run_himalayas_diagnostic(
                    self.settings,
                    provider=DiagnosticProvider(error=RuntimeError("diagnostic fetch failed")),
                    state=self.state,
                )
        self.assertIn("Himalayas diagnostic mode: ERROR", "\n".join(logs.output))

    async def test_run_once_selects_diagnostic_execution_path_from_setting(self):
        diagnostic_settings = SimpleNamespace(
            **self.settings.__dict__, himalayas_diagnostic=True,
        )
        diagnostic = AsyncMock()
        with patch.object(run_once_module, "load_settings", return_value=diagnostic_settings), patch.object(
            run_once_module, "run_himalayas_diagnostic", diagnostic,
        ):
            await run_once_module.run_once()
        diagnostic.assert_awaited_once_with(diagnostic_settings)
