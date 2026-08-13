from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from strict_run_once import StrictSource, StrictState, load_strict_sources, process_source


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


class Message:
    def __init__(self, message_id: int, text: str, age: timedelta = timedelta(hours=1), fwd_from=None) -> None:
        self.id = message_id
        self.raw_text = text
        self.date = NOW - age
        self.fwd_from = fwd_from


class FakeClient:
    def __init__(self, messages):
        self.messages = messages

    async def iter_messages(self, _entity, limit=None, min_id=None, reverse=False):
        messages = self.messages[-limit:] if limit else self.messages
        for message in messages:
            if min_id is None or message.id > min_id:
                yield message


GOOD = (
    "Ищу 3D-моделлера. Нужно сделать hard-surface корпус по фото и размерам в Blender. "
    "Оплата 5 000 ₽. Пишите @client_name"
)


class StrictRunOnceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.state = StrictState(Path(self.folder.name) / "state.json")
        self.source = StrictSource("cg_worker", "CG Worker", "cg_worker", "general", "mixed")
        self.sent = []

        async def send(card):
            self.sent.append(card)

        self.send = send

    async def asyncTearDown(self):
        self.folder.cleanup()

    async def test_first_run_only_sets_its_own_boundary(self):
        count = await process_source(self.source, FakeClient([Message(10, GOOD)]), self.state, self.send, now=NOW)
        self.assertEqual(count, 0)
        self.assertEqual(self.state.get(self.source), 10)
        self.assertEqual(self.sent, [])

    async def test_good_message_is_sent_and_bad_message_is_audited(self):
        self.state.set(self.source, 10)
        bad = "Looking for a Blender artist. Paid job. Apply to hr@example.com"
        count = await process_source(
            self.source,
            FakeClient([Message(11, GOOD), Message(12, bad)]),
            self.state,
            self.send,
            now=NOW,
        )
        self.assertEqual(count, 2)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.state.accepted, 1)
        self.assertEqual(self.state.checked, 2)
        self.assertEqual(self.state.get(self.source), 12)
        self.assertEqual(self.state.rejected_by_reason["объявление не русскоязычное"], 1)

    async def test_forward_without_original_date_is_not_sent(self):
        self.state.set(self.source, 10)
        await process_source(
            self.source,
            FakeClient([Message(11, GOOD, fwd_from=SimpleNamespace())]),
            self.state,
            self.send,
            now=NOW,
        )
        self.assertEqual(self.sent, [])
        self.assertIn("у пересланного объявления нет точной даты оригинала", self.state.rejected_by_reason)

    def test_three_parallel_sources_are_configured(self):
        sources = load_strict_sources()
        self.assertEqual([source.entity for source in sources], ["cgfreelance", "cg_worker", "artisthh"])
