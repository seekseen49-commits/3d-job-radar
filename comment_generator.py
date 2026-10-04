"""Генерация коротких комментариев локальной моделью Ollama."""
from __future__ import annotations

import base64
import re

import httpx


CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")

INTEREST_PROFILE = """
Какие посты этому аккаунту обычно интересны:
- фриланс, поиск заказов, биржи, клиенты и заказчики;
- работа в креативных профессиях, портфолио, собеседования, рынок и карьера;
- видеомонтаж, motion design, After Effects;
- Blender, 3D, моделирование, рендер, материалы, анимация и 3D-печать;
- GameDev, Unreal Engine и пайплайны 3D -> движок;
- обучение новому софту, курсы и туториалы;
- ИИ в работе художников, дизайнеров, монтажеров и 3D-специалистов;
- выгорание, дисциплина и бытовая сторона творческой работы.

Не нужны случайные новости, политика, медицина, отношения, провокационный контент и темы,
к которым у аккаунта нет естественного отношения.
""".strip()

SYSTEM_PROMPT = f"""Ты пишешь комментарии от лица креативного фрилансера в Threads.

{INTEREST_PROFILE}

Как звучат хорошие комментарии этого аккаунта:
- Коротко и разговорно. Обычно 1-3 предложения.
- Не пересказывай исходный пост.
- Не пиши пустую похвалу. Если хвалишь, замечай конкретную деталь.
- Часто комментарий строится как: конкретное наблюдение + своя мысль.
- Вопрос нужен только если на него самой реально было бы интересно узнать ответ.
- Можно легко пошутить или использовать неожиданное бытовое сравнение.
- Русский можно писать lowercase. Скобочки )) уместны.
- Мат допустим редко и только если он естественно усиливает шутку или эмоцию.
- Не злоупотребляй словами "вайб", "буквально", "реально", "очень".
- Не используй длинные тире, стрелочки, хэштеги, канцелярит и рекламный тон.
- Не используй шаблон "это не просто X, а Y".
- Не заканчивай каждый комментарий вопросом.
- Не выдумывай личный опыт, которого нет в исходном посте или в этом профиле интересов.
- Не спорь агрессивно.
- До 360 символов.

Примеры нужной логики и тона:

Пост: человек сделал первый интерьер, хотя начинал с одной лампы.
Комментарий: "обожаю как в 3д это работает)) заходишь сделать лампу, а через пару часов уже строишь ей целую комнату. для первого интерьера вообще очень уверенно выглядит, особенно свет и дерево"

Пост: человек напечатал и собрал сложную радиоуправляемую модель.
Комментарий: "вот это уже тот уровень 3д печати, ради которого реально хочется купить принтер)) а кузов ты сам с нуля моделил под всю начинку или брал готовую модель и переделывал?"

Пост: человек жалуется, что биржи показывают один и тот же мусор.
Комментарий: "вот да, у меня ровно такое ощущение, что дело уже не в конкретной нише, а сами биржи превратились в какой то фильтр мусора)) автоотклики в такой ситуации начинают звучать все логичнее"

Пост: потенциальный клиент рассказывает про огромный проект и большие деньги, но не говорит про оплату исполнителя.
Комментарий: "чем больше в начале разговоров про миллионы, большие планы и почти готовый успех, тем сильнее хочется спросить одну скучную вещь. а мне то за работу сколько и когда?))"

Пост: автор показывает сложный проп с гравировкой.
Комментарий: "гравировка тут прям очень хорошо сработала. ты ее геометрией делал или через normal/height? на финальном рендере вообще неочевидно, сколько там настоящей геометрии)"

Главное: комментарий должен ощущаться как нормальная реакция человека, который остановился на посте,
заметил одну конкретную штуку и решил что-то добавить.
Верни только готовый комментарий без кавычек и пояснений.
"""

RELEVANCE_PROMPT = f"""Реши, подходит ли пост для комментария от этого аккаунта.

{INTEREST_PROFILE}

Ответь только YES или NO.

YES если:
- тема входит в интересы аккаунта;
- есть что добавить по делу, спросить или нормально отреагировать;
- это не прямая реклама, вакансия или бессодержательный пост.

NO если:
- тема случайная и не связана с интересами;
- комментарий пришлось бы натягивать ради активности;
- пост слишком конфликтный, рискованный или рекламный.
"""


RANK_PROMPT = f"""Выбери ОДИН пост, под которым этому аккаунту естественно оставить хороший комментарий.

{INTEREST_PROFILE}

Приоритет:
- пост реально цепляет тему аккаунта;
- есть конкретная деталь, к которой можно привязать мысль;
- можно добавить опытный/любопытный угол, а не просто похвалить;
- живые посты обычных людей лучше рекламы, вакансий и продаж.

Верни только номер поста. Если все варианты слабые, верни NONE.
"""


def _language_for(text: str) -> str:
    return "русском" if CYRILLIC_RE.search(text) else "английском"


class CommentGenerationError(RuntimeError):
    pass


class OllamaCommentGenerator:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        vision_model: str | None = None,
        timeout: float = 90.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.vision_model = (vision_model or "").strip()
        self.timeout = timeout
        if not self.model:
            raise ValueError("OLLAMA_MODEL is required")

    async def _chat(self, messages: list[dict[str, str]], *, temperature: float) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "think": False,
                    "messages": messages,
                    "options": {"temperature": temperature},
                },
            )
        if response.is_error:
            raise CommentGenerationError(f"Ollama returned HTTP {response.status_code}")
        payload = response.json()
        text = str(payload.get("message", {}).get("content") or "").strip()
        text = text.strip('"').strip()
        if not text:
            raise CommentGenerationError("Ollama returned an empty comment")
        return text

    async def _download_images(self, urls: tuple[str, ...], *, limit: int = 2) -> list[str]:
        images: list[str] = []
        if not urls:
            return images

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            for url in urls[:limit]:
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                except httpx.HTTPError:
                    continue

                content_type = response.headers.get("content-type", "").lower()
                if content_type and not content_type.startswith("image/"):
                    continue
                data = response.content
                if not data or len(data) > 10 * 1024 * 1024:
                    continue
                images.append(base64.b64encode(data).decode("ascii"))
        return images

    async def describe_visuals(self, image_urls: tuple[str, ...]) -> str | None:
        if not self.vision_model or not image_urls:
            return None

        images = await self._download_images(image_urls)
        if not images:
            return None

        async with httpx.AsyncClient(timeout=max(self.timeout, 120.0)) as client:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.vision_model,
                    "stream": False,
                    "think": False,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "Кратко опиши, что видно на изображениях из поста Threads. "
                                "Особенно отметь признаки 3D/CG, GameDev, монтажа, motion design, "
                                "фриланса или рабочего процесса. Не додумывай то, чего не видно. "
                                "Нужны 1-3 коротких предложения на русском."
                            ),
                            "images": images,
                        }
                    ],
                    "options": {"temperature": 0.1},
                },
            )

        if response.status_code == 404:
            return None
        if response.is_error:
            raise CommentGenerationError(
                f"Ollama vision returned HTTP {response.status_code}"
            )
        payload = response.json()
        text = str(payload.get("message", {}).get("content") or "").strip()
        return text or None

    async def available_models(self) -> set[str]:
        """Вернуть имена моделей, которые сейчас видит локальная Ollama."""
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"{self.base_url}/api/tags")
        if response.is_error:
            raise CommentGenerationError(f"Ollama tags returned HTTP {response.status_code}")
        payload = response.json()
        names: set[str] = set()
        for item in payload.get("models", []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("model") or "").strip()
            if name:
                names.add(name)
        return names

    @staticmethod
    def _trim(text: str) -> str:
        text = " ".join(line.strip() for line in text.splitlines() if line.strip())
        if len(text) > 360:
            text = text[:357].rstrip() + "..."
        return text

    async def is_relevant(self, post_text: str) -> bool:
        result = await self._chat(
            [
                {"role": "system", "content": RELEVANCE_PROMPT},
                {"role": "user", "content": post_text.strip()},
            ],
            temperature=0.05,
        )
        return result.strip().upper().startswith("YES")

    async def choose_best(self, post_texts: list[str]) -> int | None:
        if not post_texts:
            return None
        numbered = []
        for index, text in enumerate(post_texts, start=1):
            compact = " ".join(text.split())
            if len(compact) > 520:
                compact = compact[:517].rstrip() + "..."
            numbered.append(f"{index}. {compact}")
        result = await self._chat(
            [
                {"role": "system", "content": RANK_PROMPT},
                {"role": "user", "content": "\n\n".join(numbered)},
            ],
            temperature=0.1,
        )
        if result.strip().upper().startswith("NONE"):
            return None
        match = re.search(r"\b(\d{1,2})\b", result)
        if not match:
            return None
        choice = int(match.group(1)) - 1
        return choice if 0 <= choice < len(post_texts) else None

    async def generate(self, post_text: str, *, extra_context: str | None = None) -> str:
        language = _language_for(post_text)
        context = ""
        if extra_context:
            context = (
                "\n\nДополнительный контекст для понимания поста "
                "(не пересказывай его и не отвечай напрямую комментаторам):\n"
                f"{extra_context.strip()}"
            )
        draft = await self._chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Напиши один качественный комментарий в Threads на {language} языке.\n\n"
                        f"Исходный пост:\n{post_text.strip()}{context}"
                    ),
                },
            ],
            temperature=0.78,
        )
        return self._trim(draft)

    async def regenerate(self, post_text: str, previous: str) -> str:
        language = _language_for(post_text)
        draft = await self._chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Перепиши комментарий на {language} языке проще, короче и естественнее. "
                        "Не сохраняй формулировки только ради сохранения. Если вопрос слабый, убери его.\n\n"
                        f"Пост:\n{post_text.strip()}\n\n"
                        f"Предыдущий вариант:\n{previous.strip()}"
                    ),
                },
            ],
            temperature=0.9,
        )
        return self._trim(draft)
