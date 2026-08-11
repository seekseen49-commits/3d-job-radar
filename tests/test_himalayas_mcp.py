from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from external_sources.base import ExternalState, process_external_provider
from external_sources.himalayas_mcp import HimalayasMcpSource

NOW = datetime(2026, 8, 11, 0, tzinfo=UTC)
SETTINGS = SimpleNamespace(send_freelance_vacancies=True, send_job_vacancies=True)

def payload(identifier="1", title=None, location="Worldwide"):
    title = title or f"Senior 3D Artist {identifier}, full-time"
    return {"jobs": [{"id": identifier, "title": title, "description": "Full-time 3D role", "url": f"https://himalayas.app/jobs/{identifier}", "companyName": "Co", "publishedAt": NOW.isoformat(), "locationRestrictions": location}]}

class McpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory(); self.state = ExternalState(Path(self.temp.name)/"state.json"); self.sent=[]
        async def send(card): self.sent.append(card)
        self.send=send
    async def asyncTearDown(self): self.temp.cleanup()
    async def test_first_run_baselines_then_new_job_is_sent_and_poll_interval_is_ten_minutes(self):
        source = HimalayasMcpSource(10, caller=lambda: payload("old"))
        await process_external_provider(source, self.state, SETTINGS, self.send, now=NOW)
        self.assertEqual(self.sent, [])
        self.assertFalse(self.state.has_fingerprint(source.fetch_jobs()[0]))
        await process_external_provider(source, self.state, SETTINGS, self.send, now=NOW+timedelta(minutes=5))
        self.assertEqual(self.sent, [])
        source._caller=lambda: payload("new")
        await process_external_provider(source, self.state, SETTINGS, self.send, now=NOW+timedelta(minutes=10))
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.state.has_fingerprint(source.fetch_jobs()[0]))
        self.assertIsNotNone(self.state.values.get("himalayas_mcp_last_successful_poll_at"))
    async def test_failed_poll_does_not_write_success_and_cross_fingerprint_prevents_duplicate(self):
        source = HimalayasMcpSource(10, caller=lambda: (_ for _ in ()).throw(RuntimeError("429")))
        await process_external_provider(source, self.state, SETTINGS, self.send, now=NOW)
        self.assertNotIn("himalayas_mcp_last_successful_poll_at", self.state.values)
        source._caller=lambda: payload("same")
        job=source.fetch_jobs()[0]; self.state.mark_fingerprint(job, NOW)
        await process_external_provider(source, self.state, SETTINGS, self.send, now=NOW+timedelta(minutes=10))
        self.assertEqual(self.sent, [])

    async def test_recovery_can_send_job_seen_only_by_mcp_baseline(self):
        source = HimalayasMcpSource(10, caller=lambda: payload("baseline"))
        await process_external_provider(source, self.state, SETTINGS, self.send, now=NOW)
        baseline_job = source.fetch_jobs()[0]
        self.assertTrue(self.state.is_processed(baseline_job))
        self.assertFalse(self.state.has_fingerprint(baseline_job))
        class JsonFallback:
            name = "Himalayas"; interval_minutes = 1440
            def fetch_jobs(self): return [baseline_job]
        await process_external_provider(JsonFallback(), self.state, SETTINGS, self.send, now=NOW, recovery=True)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.state.has_fingerprint(baseline_job))
