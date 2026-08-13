import unittest
from datetime import UTC, datetime, timedelta

from filters import evaluate, price_is_acceptable, short_technical_matches
from strict_filters import evaluate_strict


class FilterTests(unittest.TestCase):
    def test_price_rules_remain(self):
        for text, expected in [("Оплата 500 ₽", False), ("Бюджет 500–1500 ₽", True), ("от 500 ₽", True), ("Размер 500x500", True)]:
            with self.subTest(text=text): self.assertIs(price_is_acceptable(text)[0], expected)

    def test_self_promo_has_priority(self):
        examples = [
            "Всем привет, меня зовут Сергей, я CG Generalist / 3D Motion Designer. Подключаюсь к проектам как внешний специалист. Есть опыт работы по ТЗ. Behance, Instagram, контакты, #резюме",
            "Мы ИИ-студия, создаём визуал. Что мы делаем: рекламные ролики, карточки товаров, сайты. Есть задача? Напишите нам, пришлём варианты, сроки и стоимость. Контакт для связи. #резюме",
            "Работаю по ТЗ, соблюдаю дедлайны, портфолио по ссылке",
            "Мы студия визуализации. Есть задача? Напишите нам",
            "Беру проекты по Blender, стоимость обсуждается",
        ]
        for text in examples:
            with self.subTest(text=text): self.assertEqual(evaluate(text, "general", "mixed").category, "self_promo")

    def test_direct_orders_with_concrete_deliverables(self):
        examples = [
            "Нужно смоделировать стол по чертежам, срок до пятницы",
            "Ищу Blender-специалиста для создания трёх рендеров",
            "Требуется подготовить STL-модель для печати",
            "Need an artist for UV unwrapping of a 3D model",
            "Looking for a 3D artist for one paid product model",
        ]
        for text in examples:
            with self.subTest(text=text): self.assertEqual(evaluate(text, "general", "mixed").category, "direct_order")

    def test_russian_threads_style_direct_orders(self):
        examples = [
            "Ищу 3D-визуализатора, нужно сделать интерьерные рендеры. Пишите в личку.",
            "Нужен 3д дизайнер. Нужно сделать визуализацию мебели по фото.",
            "Кто может сделать 3D-модель упаковки в Blender? Оплата договорная.",
        ]
        for text in examples:
            with self.subTest(text=text):
                self.assertEqual(evaluate(text, "general", "mixed").category, "direct_order")

    def test_job_board_role_is_not_direct_order(self):
        self.assertEqual(evaluate("Looking for a 3D artist, full-time", "general", "job_board").category, "job_vacancy")
        self.assertEqual(evaluate("Freelance 3D Motion Designer (Unreal Engine)", "general", "job_board").category, "rejected")

    def test_job_board_requires_real_3d_relevance(self):
        rejected = [
            "Digital Marketing and Paid Ads Specialist, remote part-time",
            "Social Media Host, part-time, Addis Ababa",
            "Graphic Designer, remote freelance",
            "2D Animator, freelance",
        ]
        for text in rejected:
            with self.subTest(text=text):
                result = evaluate(text, "general", "job_board")
                self.assertEqual(result.category, "rejected")
                self.assertEqual(result.reason, "нет настоящих признаков 3D")

        self.assertEqual(evaluate("Freelance Blender 3D Modeler, remote", "general", "job_board").category, "freelance_vacancy")
        self.assertEqual(evaluate("Senior 3D Environment Artist, full-time", "general", "job_board").category, "rejected")
        self.assertEqual(evaluate("Need a Blender artist to create one product model", "general", "mixed").category, "direct_order")

    def test_platform_promotion_is_not_direct_order(self):
        text = "Calling all 3D artists! On 3D Agora top architectural projects need talented artists like YOU. Apply to render and animate stunning designs. Your next big project is just one click away. https://3dagora.com"
        result = evaluate(text, "general", "job_board")
        self.assertEqual(result.category, "rejected")
        self.assertEqual(result.reason, "реклама платформы или сервиса")

    def test_direct_order_requires_hiring_and_deliverable(self):
        self.assertEqual(evaluate("Ищу дженералиста в Unreal Engine 5 для создания роликов", "general", "mixed").category, "rejected")
        self.assertEqual(evaluate("Нужен Blender-моделлер для создания одной модели продукта", "general", "mixed").category, "direct_order")
        self.assertEqual(evaluate("Calling all 3D artists. Join our platform and find your next project", "general", "job_board").category, "rejected")
        self.assertEqual(evaluate("Freelance Blender Artist, remote contract", "general", "job_board").category, "freelance_vacancy")

    def test_short_keys_do_not_match_inside_words(self):
        self.assertNotIn("uv", short_technical_matches("YouTube"))
        self.assertNotIn("ai", short_technical_matches("paid"))
        self.assertNotIn("ar", short_technical_matches("career"))

    def test_technical_tokens_are_found_as_tokens(self):
        text = "UV unwrapping, UV-развёртка, 3D-модель, UE5, Unreal Engine, C4D, CAD model, ARKit, VR project"
        found = short_technical_matches(text)
        for keyword in ("uv", "3d", "ue", "c4d", "cad", "ar", "vr"):
            self.assertIn(keyword, found)

    def test_2d_only_youtube_post_is_rejected(self):
        self.assertEqual(evaluate("2D Animator / Motion Designer for a YouTube channel", "general", "mixed").category, "rejected")

    def test_game_prop_asset_work_is_high_priority(self):
        examples = [
            ("3D Prop Artist, full-time", "Model and texture production-ready game assets"),
            ("Freelance hard-surface 3D modeler", "Create low-poly game-ready props for Unity"),
        ]
        for title, description in examples:
            with self.subTest(title=title):
                result = evaluate(f"{title}\n{description}", "general", "job_board")
                self.assertNotEqual(result.reason, "исключено: assets")
                self.assertIn(result.category, {"freelance_vacancy", "job_vacancy", "direct_order"})
                self.assertEqual(result.profile_priority, "HIGH")

    def test_tasks_outside_darya_profile_are_rejected(self):
        examples = (
            "Senior 3D Character Artist, full-time. Create characters and creatures",
            "Environment Artist, full-time. Build a complete game location",
            "Need a 3D Motion Designer for VFX and video animation",
            "Ищем специалиста: нужно сделать риггинг и скиннинг персонажа",
            "Нужно смоделировать изделие. Обязателен SolidWorks и инженерные расчеты FEM",
            "Need a 3D modeler. 3ds Max is required; Blender files are not accepted",
            "Maya 3D Artist, full-time. Create production models",
            "Нужно разработать 3D-игру целиком на Unity",
        )
        for text in examples:
            with self.subTest(text=text):
                result = evaluate(text, "general", "job_board")
                self.assertEqual(result.category, "rejected")
                self.assertIn("вне рабочего профиля", result.reason)

    def test_3d_model_for_marketplace_product_is_not_mistaken_for_asset_store(self):
        result = evaluate(
            "Нужен Blender-моделлер, нужно сделать 3D-модель товара для маркетплейса",
            "general",
            "mixed",
        )
        self.assertEqual(result.category, "direct_order")

    def test_target_tasks_have_high_profile_priority(self):
        examples = (
            "Нужен Blender-моделлер, нужно сделать hard-surface игровой предмет в FBX",
            "Требуется подготовить STL-модель под FDM-печать",
            "Ищу 3D специалиста, нужно сделать GLB-модель товара по фото и размерам",
        )
        for text in examples:
            with self.subTest(text=text):
                result = evaluate(text, "general", "mixed")
                self.assertTrue(result.accepted)
                self.assertEqual(result.profile_priority, "HIGH")
                self.assertTrue(result.profile_reasons)

    def test_marketplace_3d_project_is_a_direct_order_without_hiring_phrase(self):
        result = evaluate("Доработка 3Д\nНужно доработать 3д макет, файл Blender предоставлю", "general", "marketplace")
        self.assertEqual(result.category, "direct_order")
        self.assertEqual(result.profile_priority, "HIGH")

    def test_asset_pack_and_marketplace_promotion_remain_rejected(self):
        for text in (
            "Download our free 3D asset pack",
            "Buy this environment asset pack",
            "New assets available in our marketplace",
        ):
            with self.subTest(text=text):
                self.assertEqual(evaluate(text, "general", "job_board").category, "rejected")


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

    def test_accepts_russian_job_up_to_seven_days(self):
        result = self.evaluate(
            "Вакансия Junior 3D Artist. Удаленная работа по России. Blender, low-poly props, "
            "UV и текстуры. Зарплата 60 000 ₽. Резюме отправить на hr@example.ru",
            age=timedelta(days=6),
            source_type="job_board",
        )
        self.assertEqual(result.category, "job_vacancy")

    def test_direct_order_older_than_72_hours_is_rejected(self):
        result = self.evaluate(
            "Ищу Blender-моделлера, нужно сделать STL по размерам. Оплата 5 000 ₽. Пишите @client_name",
            age=timedelta(hours=73),
        )
        self.assertIn("старше 72 часов", result.reason)

    def test_job_older_than_seven_days_is_rejected(self):
        result = self.evaluate(
            "Вакансия Junior 3D Artist. Blender, low-poly props. Зарплата 60 000 ₽. Пишите @client_name",
            age=timedelta(days=8),
            source_type="job_board",
        )
        self.assertIn("старше 7 дней", result.reason)

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

    def test_uses_original_forward_date_for_age(self):
        result = self.evaluate(
            "Ищу Blender-моделлера, нужно сделать STL по размерам. Оплата 5 000 ₽. Пишите @client_name",
            forwarded=True,
            original_published_at=self.now - timedelta(days=5),
        )
        self.assertIn("старше 72 часов", result.reason)

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

    def test_contests_unpaid_work_revshare_and_unpaid_tests_are_rejected(self):
        examples = (
            "Ищем Blender-художника. Конкурс: нужно сделать 3D-ролик, победитель получит 200 $. Пишите @client_name",
            "Ищем 3D-моделлера в инди-команду за процент от прибыли. Нужно делать props. Пишите @client_name",
            "Ищем Blender-моделлера. Работа волонтерская, нужно создавать STL. Пишите @client_name",
            "Ищем 3D-художника. Нужно выполнить неоплачиваемое тестовое задание. Пишите @client_name",
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertEqual(self.evaluate(text).category, "rejected")

    def test_closed_posts_and_foreign_barriers_are_rejected(self):
        examples = (
            "Ищу Blender-моделлера, нужно сделать STL. Оплата 3 000 ₽. Исполнитель найден. Пишите @client_name",
            "Ищем 3D-художника из Европы. Нужно делать low-poly props. Зарплата 1 000 €. Пишите @client_name",
            "Ищем 3D-моделлера. Нужен иностранный счет и возможность выставлять invoice. Оплата договорная. Пишите @client_name",
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertEqual(self.evaluate(text).category, "rejected")

    def test_fabric_clothing_and_complex_character_work_are_rejected(self):
        examples = (
            "Ищем 3D-дизайнера для сумок с тканью и вышивкой. Нужно подготовить модель к печати. Оплата договорная. Пишите @client_name",
            "Ищем Blender-художника. Нужно сделать персонажа, риг и сложную анимацию. Оплата 30 000 ₽. Пишите @client_name",
        )
        for text in examples:
            with self.subTest(text=text):
                self.assertEqual(self.evaluate(text).category, "rejected")
