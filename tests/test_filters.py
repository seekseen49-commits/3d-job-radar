import unittest

from filters import evaluate, price_is_acceptable, short_technical_matches


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

    def test_job_board_role_is_not_direct_order(self):
        self.assertEqual(evaluate("Looking for a 3D artist, full-time", "general", "job_board").category, "job_vacancy")
        self.assertEqual(evaluate("Freelance 3D Motion Designer (Unreal Engine)", "general", "job_board").category, "freelance_vacancy")

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
        self.assertEqual(evaluate("Senior 3D Environment Artist, full-time", "general", "job_board").category, "job_vacancy")
        self.assertEqual(evaluate("Need a Blender artist to create one product model", "general", "mixed").category, "direct_order")

    def test_platform_promotion_is_not_direct_order(self):
        text = "Calling all 3D artists! On 3D Agora top architectural projects need talented artists like YOU. Apply to render and animate stunning designs. Your next big project is just one click away. https://3dagora.com"
        result = evaluate(text, "general", "job_board")
        self.assertEqual(result.category, "rejected")
        self.assertEqual(result.reason, "реклама платформы или сервиса")

    def test_direct_order_requires_hiring_and_deliverable(self):
        self.assertEqual(evaluate("Ищу дженералиста в Unreal Engine 5 для создания роликов", "general", "mixed").category, "direct_order")
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
