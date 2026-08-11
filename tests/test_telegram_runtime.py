import asyncio
from types import SimpleNamespace
import unittest

from telegram_runtime import connect_with_retry, run_channel_handler, session_for_settings, shutdown_resources


class TelegramRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_string_session_has_priority(self):
        settings = SimpleNamespace(telethon_string_session="secret-session", telethon_session="collector")
        self.assertEqual(session_for_settings(settings, lambda value: ("string", value)), ("string", "secret-session"))

    def test_local_session_is_preserved_without_string_session(self):
        settings = SimpleNamespace(telethon_string_session=None, telethon_session="collector")
        self.assertEqual(session_for_settings(settings), "collector")

    async def test_temporary_network_error_reconnects(self):
        class Client:
            attempts = 0
            async def connect(self):
                self.attempts += 1
                if self.attempts == 1:
                    raise OSError("temporary network failure")
        client = Client()
        stop = asyncio.Event()
        async def no_wait(_: float): pass
        self.assertTrue(await connect_with_retry(client, stop, sleep=no_wait))
        self.assertEqual(client.attempts, 2)

    async def test_channel_error_does_not_escape(self):
        async def broken(): raise RuntimeError("broken channel")
        await run_channel_handler(broken)

    async def test_shutdown_closes_resources(self):
        class Client:
            disconnected = False
            async def disconnect(self): self.disconnected = True
        class Session:
            closed = False
            async def close(self): self.closed = True
        class Bot:
            session = Session()
        class Database:
            closed = False
            def close(self): self.closed = True
        client, bot, db = Client(), Bot(), Database()
        pending = asyncio.create_task(asyncio.sleep(60))
        await shutdown_resources(client=client, bot=bot, db=db, tasks=[pending])
        self.assertTrue(client.disconnected)
        self.assertTrue(bot.session.closed)
        self.assertTrue(db.closed)
        self.assertTrue(pending.cancelled())
