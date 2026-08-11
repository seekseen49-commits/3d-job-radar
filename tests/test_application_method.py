from datetime import UTC, datetime
import unittest

from application_method import application_rank, detect_application
from external_sources.base import ExternalJob, format_external_card, opportunity_priority
from filters import evaluate


NOW = datetime(2026, 8, 11, tzinfo=UTC)


class ApplicationMethodTests(unittest.TestCase):
    def test_direct_contacts(self):
        self.assertEqual(detect_application("Write to @client3d", "Telegram", None).method, "direct_contact")
        self.assertEqual(detect_application("Send portfolio to artist@example.com", "Telegram", None).method, "direct_contact")
        self.assertEqual(detect_application("https://t.me/client3d", "Telegram", None).method, "direct_contact")

    def test_external_and_platform_links(self):
        self.assertEqual(detect_application("", "Himalayas", "https://himalayas.app/jobs/1", "https://boards.greenhouse.io/job/1").method, "external_application")
        self.assertEqual(detect_application("", "Himalayas", "https://himalayas.app/jobs/1", "https://company.example/apply").method, "external_application")
        self.assertEqual(detect_application("", "Jobicy", "https://jobicy.com/jobs/1").method, "source_platform")
        self.assertEqual(detect_application("", "Remotive", "https://remotive.com/jobs/1").method, "source_platform")
        self.assertEqual(detect_application("no links", "Jobicy", None).method, "unknown")

    def test_footer_email_without_application_cue_is_ignored(self):
        self.assertEqual(detect_application("Terms of use. legal@example.com", "Jobicy", "https://jobicy.com/jobs/1").method, "source_platform")

    def test_priority_order_and_original_url_are_preserved(self):
        high = ExternalJob("Jobicy", "1", "3D Artist", "full-time", "https://jobicy.com/jobs/1", NOW, salary_min=100, salary_max=100, salary_period="hourly")
        normal_direct = ExternalJob("Jobicy", "2", "3D Artist", "Write to @client3d", "https://jobicy.com/jobs/2", NOW, salary_min=20, salary_max=20, salary_period="hourly")
        priority = {"HIGH": 0, "NORMAL": 1, "UNKNOWN": 2}
        self.assertLess(
            (priority[opportunity_priority(high)], application_rank(detect_application(high.raw_text, high.source, high.url))),
            (priority[opportunity_priority(normal_direct)], application_rank(detect_application(normal_direct.raw_text, normal_direct.source, normal_direct.url))),
        )
        direct = detect_application(normal_direct.raw_text, normal_direct.source, normal_direct.url)
        external = detect_application("", "Himalayas", "https://himalayas.app/jobs/1", "https://company.example/apply")
        self.assertLess(application_rank(direct), application_rank(external))
        card = format_external_card(high, evaluate("3D Artist full-time", "general", "job_board"))
        self.assertIn("https://jobicy.com/jobs/1", card)
        self.assertEqual(high.application_method, "source_platform")
