"""Генерация коротких комментариев локальной моделью Ollama."""
from __future__ import annotations

import re

import httpx


CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")

SYSTEM_PROMPT = """Ты пишешь комментарии от лица 3D-фрилансера в Threads.

Правила:
- Пиши как живой человек, не как SMM-щик и не как ChatGPT.
- Одна понятная мысль, обычно 1-3 коротких предложения.
- Не пересказывай исходный пост.
- Комментарий должен что-то добавлять: конкретное наблюдение, полезную мысль, реакцию или действительно интересный вопрос.
- Не задавай вопрос только ради вовлечения.
- Никаких списков, хэштегов, продаж, мотивационных клише и пустой похвалы.
- Пиши на языке исходного поста.
- В русском можно lowercase, скобочки и умеренный мат, только когда он реально звучит естественно.
- В английском пиши просто и естественно.
- Не выдумывай личный опыт автора аккаунта.
- Не спорь агрессивно и не провоцируй людей.
- Не комментируй политические, медицинские, сексуальные, опасные или явно конфликтные темы.
- До 420 символов.
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

    async def generate(self, post_text: str) -> str:
        language = _language_for(post_text)
        prompt = (
            f"Напиши один качественный комментарий в Threads на {language} языке.\n\n"
            f"Исходный пост:\n{post_text.strip()}"
        )
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "options": {"temperature": 0.75},
                },
            )
        if response.is_error:
            raise CommentGenerationError(f"Ollama returned HTTP {response.status_code}")

        payload = response.json()
        text = str(payload.get("message", {}).get("content") or "").strip()
        text = text.strip('"').strip()
        if not text:
            raise CommentGenerationError("Ollama returned an empty comment")
        if len(text) > 420:
            text = text[:417].rstrip() + "..."
        return text
