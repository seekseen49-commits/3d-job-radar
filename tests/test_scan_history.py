from collections import Counter
from datetime import datetime, timedelta, timezone
import unittest

from scan_history import Source, render_message, render_summary


class ScanHistoryTests(unittest.TestCase):
    def test_direct_order_has_full_text_and_explanations(self):
        source = Source(-1001234567890, "Канал", "demo", "general", "mixed")
        text = "Ищу специалиста по Blender: нужно смоделировать стол\nПолное ТЗ: сделать модель"
        line, category = render_message(source, 42, datetime(2026, 1, 1, tzinfo=timezone.utc), text)
        self.assertEqual(category, "direct_order")
        self.assertIn("Полное ТЗ", line)
        self.assertIn("hiring_intent_matches:", line)
        self.assertIn("deliverable_matches:", line)
        self.assertIn("self_promo_matches: нет", line)

    def test_five_category_summary(self):
        source = Source(-1001234567890, "Канал", None, "general", "job_board")
        counts = Counter({"direct_order": 1, "freelance_vacancy": 2, "job_vacancy": 3, "self_promo": 4, "rejected": 5})
        summary = render_summary(source, counts, datetime.now(timezone.utc) - timedelta(days=61), 1, 2)
        self.assertIn("НЕАКТИВНЫЙ", summary)
        self.assertIn("Прямые заказы: 1", summary)

    def test_rejection_reason_is_shown_in_report(self):
        source = Source(-1001234567890, "Канал", None, "general", "job_board")
        line, category = render_message(source, 9, None, "Digital Marketing and Paid Ads Specialist, remote part-time")
        self.assertEqual(category, "rejected")
        self.assertIn("нет настоящих признаков 3D", line)
