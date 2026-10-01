"""Генерация коротких комментариев локальной моделью Ollama."""
from __future__ import annotations

import re

import httpx


CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")

SYSTEM_PROMPT = """Ты пишешь комментарии от лица 3D-фрилансера в Threads.

Стиль:
- Пиши просто, коротко и по-человечески.
- Не пересказывай исходный пост.
- Не начинай с пустой похвалы вроде "круто", "вау", "отличная работа", если дальше нечего добавить.
- Один комментарий = одна нормальная мысль.
- Вопрос добавляй только если он реально интересный и вытекает из поста.
- Русский можно писать lowercase. Скобочки )) уместны. Мат допустим редко и только когда фраза от него реально смешнее или живее.
- Не используй длинные тире, стрелочки, хэштеги и рекламные формулировки.
- Не пиши канцеляритом и не используй конструкции вроде "это не просто X, а Y".
- На английском пиши так же просто, без корпоративного тона.
- Не выдумывай личный опыт автора аккаунта, клиентов, навыки или проекты.
- Не спорь агрессивно и не провоцируй людей.
- Не комментируй политические, медицинские, сексуальные, опасные или явно конфликтные темы.
- До 360 символов.

Хороший комментарий должен звучать так, будто человек реально остановился на посте, заметил одну конкретную штуку и написал про неё.
Верни только готовый комментарий без кавычек и пояснений.
"""


def _language_for(text: str) -> str:
    return "русском" if CYRILLIC_RE.search(text) else "английском"


class CommentGenerationError(RuntimeError):
    pass


class OllamaCommentGenerator:
    def __init__(self, base_url: str, model: str, *, timeout: float = 90.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
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

    @staticmethod
    def _trim(text: str) -> str:
        text = " ".join(line.strip() for line in text.splitlines() if line.strip())
        if len(text) > 360:
            text = text[:357].rstrip() + "..."
        return text

    async def generate(self, post_text: str) -> str:
        language = _language_for(post_text)
        draft = await self._chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Напиши один качественный комментарий в Threads на {language} языке.\n\n"
                        f"Исходный пост:\n{post_text.strip()}"
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
