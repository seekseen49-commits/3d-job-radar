from datetime import UTC, datetime, timedelta
import unittest

from strict_filters import evaluate_strict, strict_fit_reasons


class StrictFilterTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

    def evaluate(self, text: str, *, age: timedelta = timedelta(hours=12), **kwargs):
        return evaluate_strict(
            text,
            "general",
            kwargs.pop("source_type", "mixed"),
            published_at=self.now - age,
            now=self.now,
            source="Telegram",
            source_url="https://t.me/example/10",
            **kwargs,
        )

    def test_accepts_fresh_russian_direct_order_with_free_contact(self):
        result = self.evaluate(
            "Ищу 3D-моделлера. Нужно сделать hard-surface корпус по фото и размерам "
            "в Blender, итоговый файл FBX. Оплата 5 000 ₽. Пишите @client_name"
        )
        self.assertEqual(result.category, "direct_order")
        self.assertIn("игровые props / hard-surface", strict_fit_reasons(result.reason + " hard-surface"))

    def test_accepts_russian_job_up_to_seven_days(self):
        result = self.evaluate(
            "Вакансия Junior 3D Artist. Удаленная работа по России. Blender, low-poly props, "
            "UV и текстуры. Зарплата 60 000 ₽. Резюме отправить на hr@example.ru",
            age=timedelta(days=6),
            source_type="job_board",
        )
        self.assertEqual(result.category, "job_vacancy")

    def test_age_limits_are_category_specific(self):
        order = self.evaluate(
            "Ищу Blender-моделлера, нужно сделать STL по размерам. Оплата 5 000 ₽. Пишите @client_name",
            age=timedelta(hours=73),
        )
        job = self.evaluate(
            "Вакансия Junior 3D Artist. Blender, low-poly props. Зарплата 60 000 ₽. Пишите @client_name",
            age=timedelta(days=8),
            source_type="job_board",
        )
        self.assertIn("старше 72 часов", order.reason)
        self.assertIn("старше 7 дней", job.reason)

    def test_missing_or_unverifiable_original_date_is_rejected(self):
        missing = evaluate_strict(
            "Ищу 3D-моделлера. Нужно сделать STL. Оплата 3 000 ₽. Пишите @client_name",
            "general",
            "mixed",
            published_at=None,
            now=self.now,
            source="Telegram",
            source_url="https://t.me/example/10",
        )
        forwarded = self.evaluate(
            "Ищу 3D-моделлера. Нужно сделать STL. Оплата 3 000 ₽. Пишите @client_name",
            forwarded=True,
        )
        self.assertIn("точная дата", missing.reason)
        self.assertIn("оригинала", forwarded.reason)

    def test_english_listing_and_required_english_are_rejected(self):
        english = self.evaluate(
            "Looking for a Blender 3D artist to create one product model. Paid job. Apply hr@example.com"
        )
        required = self.evaluate(
            "Ищем 3D-художника для Blender. Нужно делать low-poly props. Английский B2 обязателен. "
            "Зарплата 80 000 ₽. Пишите @client_name",
            source_type="job_board",
        )
        self.assertIn("не русскоязычное", english.reason)
        self.assertIn("обязательный английский", required.reason)

    def test_paid_platform_and_missing_contact_are_rejected(self):
        paid_platform = self.evaluate(
            "Нужно сделать 3D-модель по фото. Оплата 5 000 ₽. Отклик на https://www.fl.ru/projects/123"
        )
        no_contact = self.evaluate(
            "Ищу Blender-моделлера, нужно сделать STL по размерам. Оплата 5 000 ₽. Откликайтесь"
        )
        self.assertIn("платная площадка", paid_platform.reason)
        self.assertIn("нет бесплатного", no_contact.reason)

    def test_noncommercial_closed_and_foreign_posts_are_rejected(self):
        examples = (
            "Ищем Blender-художника. Конкурс: нужно сделать 3D-ролик, победитель получит 200 $. Пишите @client_name",
            "Ищем 3D-моделлера в инди-команду за процент от прибыли. Нужно делать props. Пишите @client_name",
            "Ищу Blender-моделлера, нужно сделать STL. Оплата 3 000 ₽. Исполнитель найден. Пишите @client_name",
            "Ищем 3D-художника из Европы. Нужно делать low-poly props. Зарплата 1 000 €. Пишите @client_name",
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertEqual(self.evaluate(text).category, "rejected")

    def test_out_of_profile_work_is_rejected_inside_strict_filter(self):
        examples = (
            "Ищем 3D-дизайнера для сумок с тканью и вышивкой. Нужно подготовить модель к печати. Оплата договорная. Пишите @client_name",
            "Ищем Blender-художника. Нужно сделать персонажа, риг и сложную анимацию. Оплата 30 000 ₽. Пишите @client_name",
            "Ищем 3D-художника. Нужно создать большую игровую локацию. Оплата 80 000 ₽. Пишите @client_name",
            "Ищем 3D-моделлера. Обязателен SolidWorks и инженерный расчёт FEM. Оплата 50 000 ₽. Пишите @client_name",
        )
        for text in examples:
            with self.subTest(text=text):
                result = self.evaluate(text)
                self.assertEqual(result.category, "rejected")
                self.assertIn("вне рабочего профиля", result.reason)


if __name__ == "__main__":
    unittest.main()
