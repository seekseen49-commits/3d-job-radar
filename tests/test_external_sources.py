import copy
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest

from external_sources.base import HIMALAYAS_RECOVERY_VERSION, ExternalJob, ExternalState, contract_duration_from_text, external_3d_reason, external_3d_relevant, format_external_card, html_to_text, opportunity_priority, process_external_provider, process_external_sources
from external_sources.himalayas import HimalayasSource
from external_sources.jobicy import JobicySource
from external_sources.remotive import RemotiveSource
from filters import evaluate
from recipients import send_to_recipients


NOW = datetime(2026, 8, 11, 12, tzinfo=UTC)
SETTINGS = SimpleNamespace(send_freelance_vacancies=True, send_job_vacancies=False)


class Provider:
    def __init__(self, name, jobs=None, interval=60, error=None):
        self.name, self.jobs, self.interval_minutes, self.error = name, jobs or [], interval, error
        self.calls = 0

    def fetch_jobs(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.jobs


def job(identifier, title="Need a Blender artist to create one product model", company="Studio", age_hours=1, description="Paid task"):
    if title == "Need a Blender artist to create one product model":
        title = f"{title} {identifier}"
    return ExternalJob("Test", str(identifier), title, description, f"https://example.test/{identifier}", NOW - timedelta(hours=age_hours), company, "", "Remote", "")


def ranked_job(identifier, priority="UNKNOWN", age_hours=1):
    salary_min = salary_max = None
    period = ""
    if priority == "HIGH":
        salary_min, salary_max, period = 100, 100, "hourly"
    elif priority == "NORMAL":
        salary_min, salary_max, period = 25, 25, "hourly"
    return ExternalJob(
        "Test", str(identifier), f"Need a Blender artist to create one 3D model {identifier}", "Paid task",
        f"https://example.test/{identifier}", NOW - timedelta(hours=age_hours), "Studio", salary_raw=period,
        salary_min=salary_min, salary_max=salary_max, currency="USD", salary_period=period,
    )


class ExternalSourcesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.state = ExternalState(Path(self.folder.name) / "external_sources.json")
        self.sent = []

        async def send(card):
            self.sent.append(card)
        self.send = send

    async def asyncTearDown(self):
        self.folder.cleanup()

    async def test_first_backfill_sends_at_most_ten_and_marks_all_seen(self):
        provider = Provider("Test", [job(index) for index in range(12)])
        self.assertEqual(await process_external_provider(provider, self.state, SETTINGS, self.send, now=NOW), 10)
        self.assertEqual(len(self.sent), 10)
        self.assertTrue(self.state.is_initialized("Test"))
        self.assertTrue(all(self.state.is_processed(item) for item in provider.jobs))

    async def test_old_first_run_jobs_do_not_flood_notifications(self):
        provider = Provider("Old", [job(1, age_hours=73)])
        self.assertEqual(await process_external_provider(provider, self.state, SETTINGS, self.send, now=NOW), 0)
        self.assertTrue(self.state.is_processed(provider.jobs[0]))

    async def test_new_external_id_is_sent_once_after_baseline(self):
        provider = Provider("New", [job(1)])
        await process_external_provider(provider, self.state, SETTINGS, self.send, now=NOW)
        provider.jobs.append(job(2))
        self.assertEqual(await process_external_provider(provider, self.state, SETTINGS, self.send, now=NOW + timedelta(hours=1)), 1)
        self.assertEqual(len(self.sent), 2)
        await process_external_provider(provider, self.state, SETTINGS, self.send, now=NOW + timedelta(hours=2))
        self.assertEqual(len(self.sent), 2)

    async def test_cross_source_title_and_company_is_deduplicated(self):
        first_job = job(1, company="Same Co")
        first = Provider("First", [first_job])
        second_job = ExternalJob(
            "Second", "2", first_job.title, "Paid task",
            "https://example.test/2", NOW - timedelta(hours=1), "Same Co",
        )
        second = Provider("Second", [second_job])
        await process_external_sources([first, second], self.state, SETTINGS, self.send, now=NOW)
        self.assertEqual(len(self.sent), 1)

    async def test_intervals_prevent_early_polling(self):
        provider = Provider("Timed", [job(1)], interval=60)
        await process_external_provider(provider, self.state, SETTINGS, self.send, now=NOW)
        await process_external_provider(provider, self.state, SETTINGS, self.send, now=NOW + timedelta(minutes=59))
        self.assertEqual(provider.calls, 1)

    async def test_provider_specific_intervals_are_respected(self):
        for name, interval in (("Himalayas", 1440), ("Jobicy", 60), ("Remotive", 360)):
            provider = Provider(name, [], interval=interval)
            await process_external_provider(provider, self.state, SETTINGS, self.send, now=NOW)
            await process_external_provider(provider, self.state, SETTINGS, self.send, now=NOW + timedelta(minutes=interval - 1))
            self.assertEqual(provider.calls, 1, name)

    async def test_provider_error_does_not_stop_other_provider(self):
        bad = Provider("Bad", error=RuntimeError("429"))
        good = Provider("Good", [job(1)])
        await process_external_sources([bad, good], self.state, SETTINGS, self.send, now=NOW)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(good.calls, 1)

    async def test_no_new_jobs_sends_nothing(self):
        provider = Provider("Empty", [])
        await process_external_provider(provider, self.state, SETTINGS, self.send, now=NOW)
        await process_external_provider(provider, self.state, SETTINGS, self.send, now=NOW + timedelta(hours=1))
        self.assertEqual(self.sent, [])

    async def test_send_failure_keeps_job_and_poll_unfinished_for_retry(self):
        provider = Provider("Retry", [job(1)])

        async def failing_send(_):
            raise RuntimeError("bot unavailable")

        await process_external_provider(provider, self.state, SETTINGS, failing_send, now=NOW)
        self.assertFalse(self.state.is_processed(provider.jobs[0]))
        self.assertTrue(self.state.is_due("Retry", 60, NOW + timedelta(minutes=1)))

    async def test_category_send_rules_apply_to_external_jobs(self):
        jobs = [
            job(1),
            job(2, "Freelance Blender 3D Modeler, remote contract"),
            job(3, "Senior 3D Environment Artist, full-time"),
            job(4, "I am a 3D artist, my portfolio"),
            job(5, "Graphic Designer, remote"),
        ]
        await process_external_provider(Provider("Categories", jobs), self.state, SETTINGS, self.send, now=NOW)
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(any("3D-ЗАКАЗ" in card for card in self.sent))
        self.assertTrue(any("3D-КОНТРАКТ / ФРИЛАНС" in card for card in self.sent))

    async def test_first_backfill_prioritizes_high_then_newer_and_limits_to_ten(self):
        jobs = [ranked_job("high-old", "HIGH", age_hours=5), ranked_job("high-new", "HIGH", age_hours=1)]
        jobs += [ranked_job(f"normal-{index}", "NORMAL", age_hours=index + 1) for index in range(10)]
        jobs += [ranked_job(f"unknown-{index}", "UNKNOWN", age_hours=index + 1) for index in range(8)]
        await process_external_provider(Provider("Ranked", jobs), self.state, SETTINGS, self.send, now=NOW)
        self.assertEqual(len(self.sent), 10)
        self.assertIn("high-new", self.sent[0])
        self.assertIn("high-old", self.sent[1])
        self.assertNotIn("unknown-0", "\n".join(self.sent))
        self.assertTrue(all(self.state.is_processed(item) for item in jobs))

    async def test_subsequent_run_sends_all_new_jobs_without_ten_limit(self):
        provider = Provider("AfterBaseline", [ranked_job("baseline")])
        await process_external_provider(provider, self.state, SETTINGS, self.send, now=NOW)
        provider.jobs.extend(ranked_job(f"new-{index}") for index in range(12))
        sent_before = len(self.sent)
        await process_external_provider(provider, self.state, SETTINGS, self.send, now=NOW + timedelta(hours=1))
        self.assertEqual(len(self.sent) - sent_before, 12)

    async def test_job_vacancy_sends_when_enabled(self):
        enabled = SimpleNamespace(send_freelance_vacancies=True, send_job_vacancies=True)
        vacancy = ExternalJob("Test", "job", "Senior 3D Artist, full-time", "Employment role", "https://example.test/job", NOW, "Studio")
        await process_external_provider(Provider("JobsEnabled", [vacancy]), self.state, enabled, self.send, now=NOW)
        self.assertEqual(len(self.sent), 1)

    async def test_high_paid_non_3d_job_is_rejected_before_priority(self):
        non_3d = ExternalJob("Test", "bad", "Senior DevOps Engineer", "Uses CAD and Unreal Engine tooling", "https://example.test/bad", NOW, "Studio", salary_raw="200000 USD annual", salary_min=200000, salary_max=200000, currency="USD", salary_period="annual")
        self.assertEqual(opportunity_priority(non_3d), "HIGH")
        await process_external_provider(Provider("BadHigh", [non_3d]), self.state, SimpleNamespace(send_freelance_vacancies=True, send_job_vacancies=True), self.send, now=NOW)
        self.assertEqual(self.sent, [])

    async def test_backfill_uses_application_method_after_financial_priority(self):
        high_platform = ranked_job("high-platform", "HIGH")
        normal_direct = replace(ranked_job("normal-direct", "NORMAL"), description="Write to @client3d")
        high_external = replace(ranked_job("high-external", "HIGH"), application_link="https://boards.greenhouse.io/apply")
        high_direct = replace(ranked_job("high-direct", "HIGH"), description="Write to @client3d")
        await process_external_provider(Provider("ApplicationRank", [high_external, normal_direct, high_platform, high_direct]), self.state, SETTINGS, self.send, now=NOW)
        cards = "\n---\n".join(self.sent)
        self.assertLess(cards.index("HIGH-DIRECT"), cards.index("HIGH-EXTERNAL"))
        self.assertLess(cards.index("HIGH-PLATFORM"), cards.index("NORMAL-DIRECT"))

    async def test_himalayas_recovery_reconsiders_processed_but_not_sent_jobs_once(self):
        item = ranked_job("recovery", "HIGH")
        provider = Provider("Himalayas", [item])
        self.state.mark_processed(item, NOW - timedelta(days=1))
        self.state.finish_poll("Himalayas", NOW - timedelta(days=1))
        await process_external_provider(provider, self.state, SETTINGS, self.send, now=NOW, recovery=True)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.state.recovery_completed())
        self.assertEqual(self.state.recovery_version(), HIMALAYAS_RECOVERY_VERSION)
        self.assertTrue(self.state.is_processed(item))
        self.assertTrue(self.state.is_initialized("Himalayas"))
        await process_external_provider(provider, self.state, SETTINGS, self.send, now=NOW, recovery=True)
        self.assertEqual(len(self.sent), 1)

    async def test_himalayas_recovery_skips_existing_sent_fingerprint_and_blocked_jobs(self):
        sent_item = ranked_job("sent", "HIGH")
        blocked_item = replace(ranked_job("blocked", "HIGH"), location="USA, Canada, UK", location_restrictions=["USA", "Canada", "UK"])
        provider = Provider("Himalayas", [sent_item, blocked_item])
        for item in provider.jobs:
            self.state.mark_processed(item, NOW - timedelta(days=1))
        self.state.mark_fingerprint(sent_item, NOW - timedelta(days=1))
        await process_external_provider(provider, self.state, SETTINGS, self.send, now=NOW, recovery=True)
        self.assertEqual(self.sent, [])

    async def test_recovery_v3_runs_from_stored_v2_then_skips_repeat(self):
        item = ranked_job("v3-from-v2", "HIGH")
        provider = Provider("Himalayas", [item])
        self.state.values["himalayas_recovery_version"] = 2
        self.state.mark_processed(item, NOW - timedelta(days=1))
        self.assertEqual(HIMALAYAS_RECOVERY_VERSION, 3)
        self.assertFalse(self.state.recovery_completed())
        await process_external_provider(provider, self.state, SETTINGS, self.send, now=NOW, recovery=True)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.state.recovery_version(), 3)
        with self.assertLogs(level="INFO") as logs:
            self.assertEqual(
                await process_external_provider(provider, self.state, SETTINGS, self.send, now=NOW, recovery=True),
                0,
            )
        self.assertIn("Himalayas recovery v3 already completed", "\n".join(logs.output))

    async def test_recovery_v3_delivers_to_all_recipients_and_marks_after_delivery(self):
        item = ranked_job("v3-recipients", "HIGH")
        self.state.values["himalayas_recovery_version"] = 2

        class Bot:
            def __init__(self):
                self.chat_ids = []

            async def send_message(self, chat_id, _text):
                self.chat_ids.append(chat_id)

        bot = Bot()

        async def send(card):
            self.assertTrue(await send_to_recipients(bot, (101, 202, 303), card))

        await process_external_provider(
            Provider("Himalayas", [item]), self.state, SETTINGS, send, now=NOW, recovery=True,
        )
        self.assertEqual(bot.chat_ids, [101, 202, 303])
        self.assertTrue(self.state.has_fingerprint(item))

    async def test_recovery_v3_allows_unknown_russia_but_rejects_blocked(self):
        allowed_unknown = ranked_job("unknown-eligibility", "HIGH")
        blocked = replace(
            ranked_job("blocked-russia", "HIGH"),
            location="USA, Canada, UK", location_restrictions=["USA", "Canada", "UK"],
        )
        self.state.values["himalayas_recovery_version"] = 2
        self.assertEqual(allowed_unknown.metadata.russia_eligibility, "unknown")
        await process_external_provider(
            Provider("Himalayas", [allowed_unknown, blocked]), self.state, SETTINGS, self.send,
            now=NOW, recovery=True,
        )
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.state.has_fingerprint(allowed_unknown))
        self.assertFalse(self.state.has_fingerprint(blocked))

    async def test_recovery_counters_only_count_recent_strong_3d_jobs(self):
        recent = ranked_job("recent-strong", "HIGH", age_hours=1)
        old = ranked_job("old-strong", "HIGH", age_hours=73)
        weak = ExternalJob(
            "Himalayas", "weak", "Customer Success Manager", "Remote full-time",
            "https://example.test/weak", NOW - timedelta(hours=2), "Studio",
        )
        with self.assertLogs(level="INFO") as logs:
            await process_external_provider(
                Provider("Himalayas", [recent, old, weak]), self.state, SETTINGS,
                self.send, now=NOW, recovery=True,
            )
        output = "\n".join(logs.output)
        self.assertIn("within_72h=2", output)
        self.assertIn("within_72h_strong_3d=1", output)
        self.assertIn("within_72h_not_strong_3d=1", output)

    async def test_diagnostic_logs_every_recent_job_without_sending_or_state_mutation(self):
        direct = ranked_job("recent-direct", "HIGH", age_hours=1)
        rejected = ExternalJob(
            "Himalayas", "recent-rejected", "Graphic Designer", "Remote freelance",
            "https://example.test/rejected", NOW - timedelta(hours=2), "Studio",
        )
        old = ranked_job("old", "HIGH", age_hours=73)
        self.state.mark_processed(direct, NOW - timedelta(days=1))
        self.state.mark_fingerprint(direct, NOW - timedelta(days=1))
        self.state.finish_poll("Himalayas", NOW - timedelta(days=1))
        self.state.mark_recovery_completed()
        before = copy.deepcopy(self.state.values)
        with self.assertLogs(level="INFO") as logs:
            result = await process_external_provider(
                Provider("Himalayas", [direct, rejected, old]), self.state, SETTINGS,
                self.send, now=NOW, diagnostic=True,
            )
        output = "\n".join(logs.output)
        self.assertEqual(result, 0)
        self.assertEqual(self.sent, [])
        self.assertIn("title='Need a Blender artist to create one 3D model recent-direct'", output)
        self.assertIn("title='Graphic Designer'", output)
        self.assertIn("strong_3d_reason=", output)
        self.assertNotIn("title='Need a Blender artist to create one 3D model old'", output)
        self.assertIn("Himalayas diagnostic:", output)
        self.assertEqual(self.state.values, before)

    async def test_regular_production_mode_still_sends_and_updates_state(self):
        item = ranked_job("normal-production", "HIGH")
        await process_external_provider(
            Provider("Himalayas", [item]), self.state, SETTINGS, self.send, now=NOW,
        )
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.state.is_processed(item))
        self.assertTrue(self.state.has_fingerprint(item))


class ExternalParsingAndRelevanceTests(unittest.TestCase):
    def test_html_is_converted_to_plain_text(self):
        self.assertEqual(html_to_text("<p>Need <b>Blender</b>&amp; CAD</p>"), "Need Blender& CAD")

    def test_real_3d_directions_pass_existing_gate(self):
        texts = (
            "Blender 3D Modeler, full-time",
            "3D furniture modeling artist, full-time",
            "Architectural visualization 3D Artist, full-time",
            "Product visualization 3D Artist, full-time",
            "CAD STL 3D Modeler, full-time",
            "Unreal Engine 3D Artist, full-time",
        )
        self.assertTrue(all(evaluate(text, "general", "job_board").accepted for text in texts))

    def test_non_3d_roles_are_rejected(self):
        texts = ("Graphic Designer, remote", "UI/UX Designer", "Video Editor", "Digital Marketing Specialist", "2D Animator, freelance")
        self.assertTrue(all(evaluate(text, "general", "job_board").category == "rejected" for text in texts))

    def test_jobicy_payload_is_parsed_without_network(self):
        source = JobicySource(fetcher=lambda _: {"jobs": [{"id": 1, "jobTitle": "<b>Blender</b> 3D Modeler", "jobDescription": "<p>full-time</p>", "url": "https://job", "companyName": "Co", "jobType": ["Full-Time"], "pubDate": "2026-08-11T10:00:00Z"}]})
        parsed = source.fetch_jobs()[0]
        self.assertEqual((parsed.source, parsed.title, parsed.description), ("Jobicy", "Blender 3D Modeler", "full-time"))
        self.assertEqual(parsed.job_type, "Full-Time")

    def test_remotive_payload_is_parsed_without_network(self):
        source = RemotiveSource(fetcher=lambda _: {"jobs": [{"id": 2, "title": "CAD 3D Modeler", "description": "<p>role</p>", "url": "https://rem", "company_name": "Co", "publication_date": "2026-08-11T10:00:00Z"}]})
        self.assertEqual(source.fetch_jobs()[0].source, "Remotive")

    def test_himalayas_payload_accepts_unix_milliseconds(self):
        payload = {"jobs": [{"guid": "g1", "title": "3D Artist", "description": "<p>role</p>", "applicationLink": "https://him", "companyName": "Co", "pubDate": 1786442400000}]}
        source = HimalayasSource(fetcher=lambda _: payload)
        parsed = source.fetch_jobs()
        self.assertEqual(len(parsed), 1)
        self.assertIsNotNone(parsed[0].published_at)

    def test_live_false_positive_roles_do_not_pass_external_3d_gate(self):
        titles = (
            "Senior Customer Success Manager", "Principal Customer Success Manager", "Customer Support Specialist",
            "Manager, Ad Operations", "Senior Graphic Designer", "Full-Stack Rails Engineer", "Senior DevOps Engineer",
        )
        for index, title in enumerate(titles):
            with self.subTest(title=title):
                weak_description = "Our roadmap mentions OBJ, CAD, Unreal Engine and Blender integrations."
                self.assertFalse(external_3d_relevant(job(index, title, description=weak_description)))

    def test_non_3d_title_needs_explicit_role_context_for_strong_phrase_in_description(self):
        self.assertFalse(external_3d_relevant(job(1, "Graphic Designer", description="Our company once hired a Technical Artist.")))
        self.assertTrue(external_3d_relevant(job(2, "Graphic Designer", description="Responsibilities include working as a Technical Artist on 3D assets.")))

    def test_strong_external_3d_roles_pass_gate(self):
        titles = (
            "Blender 3D Modeler", "3D Furniture Modeler", "Architectural Visualization Artist",
            "Product Visualization Artist", "CAD Modeler", "Senior Technical Artist", "Unreal Artist",
        )
        self.assertTrue(all(external_3d_relevant(job(index, title)) for index, title in enumerate(titles)))

    def test_production_3d_titles_and_contextual_roles_pass_external_gate(self):
        cases = (
            ("2D/3D Game Artist - Environment - Contract", "", "explicit title: 2D/3D Game Artist"),
            ("Surface Modeling Expert - Fully Remote - Upto $84/hr", "Create Class-A surfaces with CAD, NURBS and Rhino.", "surface modeling + technical 3D context"),
            ("Senior Landscape Artist", "Create terrain and 3D environments in Unreal Engine.", "environment art + technical 3D context"),
            ("R&D Art Generalist", "Own a Blender-based 3D asset pipeline.", "art generalist + technical 3D context"),
        )
        for index, (title, description, expected_reason) in enumerate(cases):
            with self.subTest(title=title):
                candidate = job(index, title, description=description)
                self.assertTrue(external_3d_relevant(candidate))
                self.assertEqual(external_3d_reason(candidate), expected_reason)

    def test_ambiguous_modeling_and_generic_design_roles_stay_outside_external_gate(self):
        titles = (
            ("Visual Design Specialist - Fully Remote | Upto $150/hr", ""),
            ("Senior Visual Designer 1", ""),
            ("Product Designer (Figma)", ""),
            ("Aerospace Engineer - Fully Remote | Upto $85/hr", ""),
            ("SmartPlant 3D Administrator", "Administer SmartPlant software and user access."),
            ("Financial Modeling Expert", ""),
            ("Data Modeler", ""),
            ("AI Model Engineer", ""),
            ("Surface Modeling Expert", "Financial modeling for revenue forecasts."),
            ("Senior Landscape Artist", "Landscape design for gardens and parks."),
            ("R&D Art Generalist", "Coordinate visual design research."),
        )
        for index, (title, description) in enumerate(titles):
            with self.subTest(title=title):
                self.assertFalse(external_3d_relevant(job(index, title, description=description)))

    def test_priority_is_informational_and_high_is_marked_in_card(self):
        high = ExternalJob("Test", "high", "3D Artist", "Full-time role", "https://example.test/high", NOW, "Co", salary_raw="80000 USD per year", salary_min=70000, salary_max=80000, currency="USD", salary_period="year")
        unknown = ExternalJob("Test", "unknown", "3D Artist", "Role", "https://example.test/unknown", NOW, "Co")
        self.assertEqual(opportunity_priority(high), "HIGH")
        self.assertEqual(opportunity_priority(unknown), "UNKNOWN")
        self.assertIn("ВЫСОКИЙ ПОТЕНЦИАЛ ДОХОДА", format_external_card(high, evaluate("3D Artist full-time", "general", "job_board")))

    def test_contract_duration_is_extracted_without_affecting_relevance(self):
        self.assertEqual(contract_duration_from_text("Technical Artist (6-Month Contract)"), "6-Month")
