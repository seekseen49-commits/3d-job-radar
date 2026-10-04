"""Threads comment assistant: search, draft locally, publish only after Telegram approval."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from html import escape
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from comment_generator import OllamaCommentGenerator
from config import Settings
from database import Database
from threads_client import ThreadPost, ThreadsClient
from threads_rules import ineligibility_reason



CORE_DISCOVERY_QUERIES = (
    "3D", "Blender", "рендер", "анимация", "фриланс", "заказ",
    "заказчик", "клиент", "монтаж", "работа", "проект", "портфолио",
)

EXPANDED_DISCOVERY_QUERIES = (
    # 3D / CG
    "blender3d", "blenderrender", "blenderart", "3dart", "3dartist",
    "3dmodeling", "3d model", "моделирование", "3д моделирование",
    "визуализация", "сцена", "материалы", "текстуры", "UV", "ретопология",
    "скульпт", "low poly", "high poly", "hard surface", "prop art",
    "environment art", "3D Printing", "3dprinting", "3д печать", "STL",
    "Substance Painter", "ZBrush", "Geometry Nodes", "Cycles", "Eevee",
    # GameDev
    "Game Dev", "gamedev", "indie game", "Unreal Engine", "Unity", "Godot",
    "game artist", "environment artist", "technical artist", "level design",
    "game ready", "LOD", "collision", "blueprint", "shader", "asset",
    # Motion / video
    "motion design", "motiondesign", "After Effects", "aftereffects",
    "Premiere Pro", "DaVinci Resolve", "видеомонтаж", "моушн", "ролик",
    "reels", "композ", "compositing",
    # Freelance / work
    "биржа", "правки", "дедлайн", "оплата", "ставка", "ценник", "бриф",
    "ТЗ", "тестовое", "собеседование", "вакансия", "поиск работы",
    "ищу работу", "ищу заказ", "креативная работа",
    # Learning / creative process
    "курс", "туториал", "урок", "обучение", "учусь", "изучаю", "новичок",
    "первая работа", "первый рендер", "практика", "процесс", "сделал",
    "сделала", "делаю", "готово", "проба",
    # AI / creative workflow
    "нейросети", "нейронка", "AI", "ИИ", "prompt", "генеративный ИИ",
    # Work-life
    "выгорание", "мотивация", "дисциплина", "прокрастинация", "творческий кризис",
)

def _keyboard(post_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"tc:send:{post_id}"),
                InlineKeyboardButton(text="♻️ Переписать", callback_data=f"tc:redo:{post_id}"),
            ],
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"tc:skip:{post_id}")],
        ]
    )


def _short(value: str, limit: int) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


class ThreadsCommentAssistant:
    def __init__(self, settings: Settings, db: Database, bot: Bot) -> None:
        if not settings.threads_access_token:
            raise ValueError("THREADS_ACCESS_TOKEN is required for Threads comments")
        self.settings = settings
        self.db = db
        self.bot = bot
        self.client = ThreadsClient(settings.threads_access_token)
        self.generator = OllamaCommentGenerator(
            settings.ollama_base_url,
            settings.ollama_model,
            vision_model=getattr(settings, "ollama_vision_model", None),
        )
        self._scan_lock = asyncio.Lock()

    def is_paused(self) -> bool:
        return self.db.get_value("threads_comments_paused", "0") == "1"

    def pause(self) -> None:
        self.db.set_value("threads_comments_paused", "1")

    def resume(self) -> None:
        self.db.set_value("threads_comments_paused", "0")

    def _next_query(self) -> str:
        queries = self.settings.threads_comment_queries
        index = int(self.db.get_value("threads_comment_query_index", "0") or "0") % len(queries)
        self.db.set_value("threads_comment_query_index", str((index + 1) % len(queries)))
        return queries[index]

    @staticmethod
    def _candidate_score(post: ThreadPost) -> tuple[int, int, int, int]:
        length = len(post.text)
        preferred_length = 1 if 70 <= length <= 900 else 0
        cyrillic = 1 if re.search(r"[А-Яа-яЁё]", post.text) else 0
        freshness = 0
        if post.timestamp:
            try:
                published = datetime.fromisoformat(post.timestamp.replace("Z", "+00:00"))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - published.astimezone(timezone.utc)).total_seconds() / 3600
                freshness = max(0, int(24 - age_hours))
            except ValueError:
                freshness = 0
        return cyrillic, freshness, preferred_length, -abs(length - 300)

    def format_card(self, row: Any, *, note: str | None = None) -> str:
        username = str(row["username"] or "unknown")
        post_text = _short(str(row["post_text"] or ""), 1100)
        draft = _short(str(row["draft_text"] or ""), 700)
        permalink = str(row["permalink"] or "")
        link_line = f'\n<a href="{escape(permalink, quote=True)}">Открыть пост</a>' if permalink else ""
        note_line = f"\n\n<b>{escape(note)}</b>" if note else ""
        return (
            "<b>Черновик комментария Threads</b>\n"
            f"<b>Автор:</b> @{escape(username)}{link_line}\n\n"
            f"<b>Пост:</b>\n{escape(post_text)}\n\n"
            f"<b>Наш комментарий:</b>\n{escape(draft)}"
            f"{note_line}"
        )

    async def scan_once(self) -> int:
        if self.is_paused():
            return 0

        async with self._scan_lock:
            configured = list(self.settings.threads_comment_queries)
            expanded = list(EXPANDED_DISCOVERY_QUERIES)
            rotate_index = int(self.db.get_value("threads_discovery_query_index", "0") or "0")
            rotate_index %= max(1, len(expanded))
            rotated = expanded[rotate_index:] + expanded[:rotate_index]
            self.db.set_value(
                "threads_discovery_query_index",
                str((rotate_index + 28) % max(1, len(expanded))),
            )

            queries: list[str] = []
            for query in (*CORE_DISCOVERY_QUERIES, *configured, *rotated):
                if query not in queries:
                    queries.append(query)
                if len(queries) >= 40:
                    break

            logging.info("Threads comments: scanning queries=%r", queries)
            results = await asyncio.gather(
                *(self.client.search_recent(query, limit=30) for query in queries),
                return_exceptions=True,
            )

            pool: dict[str, ThreadPost] = {}
            total_returned = 0
            rejection_counts: dict[str, int] = {}
            sample_timestamps: list[str] = []
            for query, result in zip(queries, results):
                if isinstance(result, Exception):
                    logging.warning("Threads comments: query=%r failed: %s", query, result)
                    continue
                total_returned += len(result)
                for post in result:
                    if len(sample_timestamps) < 5:
                        sample_timestamps.append(post.timestamp or "<missing>")
                    if self.db.has_threads_comment_post(post.id):
                        rejection_counts["already_seen"] = rejection_counts.get("already_seen", 0) + 1
                        continue
                    reason = ineligibility_reason(
                        post,
                        own_username=self.settings.threads_comment_own_username,
                        max_age_hours=24,
                    )
                    if reason:
                        rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
                        continue
                    pool[post.id] = post

            logging.info(
                "Threads comments: API returned=%s, fresh eligible unique=%s, rejected=%r, timestamp_samples=%r",
                total_returned,
                len(pool),
                rejection_counts,
                sample_timestamps,
            )

            if not pool:
                logging.info("Threads comments: no fresh eligible candidates in priority scan")
                return 0

            candidates = sorted(pool.values(), key=self._candidate_score, reverse=True)[:12]

            reply_results = await asyncio.gather(
                *(
                    self.client.get_replies(post.id, limit=5)
                    if post.has_replies
                    else asyncio.sleep(0, result=[])
                    for post in candidates
                ),
                return_exceptions=True,
            )

            # Визуальный анализ дороже обычного текста, поэтому смотрим максимум
            # первые 8 наиболее перспективных кандидатов. Остальные всё равно
            # получают alt_text и контекст ответов.
            vision_results = await asyncio.gather(
                *(
                    self.generator.describe_visuals(post.image_urls)
                    if index < 8 and post.image_urls
                    else asyncio.sleep(0, result=None)
                    for index, post in enumerate(candidates)
                ),
                return_exceptions=True,
            )

            candidate_contexts: list[str] = []
            extra_context_by_id: dict[str, str] = {}
            for post, replies_result, vision_result in zip(
                candidates,
                reply_results,
                vision_results,
            ):
                parts = [post.text]
                extra_parts: list[str] = []

                if post.alt_text:
                    extra_parts.append(f"Alt-текст медиа: {post.alt_text}")

                if not isinstance(vision_result, Exception) and vision_result:
                    extra_parts.append(f"Что видно на медиа: {vision_result}")

                if not isinstance(replies_result, Exception) and replies_result:
                    replies = replies_result[:5]
                    extra_parts.append("Комментарии под постом: " + " | ".join(replies))

                if extra_parts:
                    extra = "\n".join(extra_parts)
                    extra_context_by_id[post.id] = extra
                    parts.append(extra)

                candidate_contexts.append("\n".join(parts))

            try:
                choice = await self.generator.choose_best(candidate_contexts)
            except Exception:
                logging.exception("Threads comments: batch ranking failed")
                return 0

            if choice is None:
                logging.info("Threads comments: Ollama rejected candidate pool")
                return 0

            post = candidates[choice]
            draft = await self.generator.generate(
                post.text,
                extra_context=extra_context_by_id.get(post.id),
            )
            self.db.save_threads_comment_draft(
                post.id,
                post.username,
                post.text,
                post.permalink,
                draft,
            )
            row = self.db.get_threads_comment_post(post.id)
            if row is None:
                raise RuntimeError("Threads draft disappeared after save")

            await self.bot.send_message(
                self.settings.owner_chat_id,
                self.format_card(row),
                reply_markup=_keyboard(post.id),
                disable_web_page_preview=True,
            )
            logging.info("Threads comments: draft created for post=%s", post.id)
            return 1

    async def diagnostics(self) -> str:
        """Безопасная проверка Threads API и локальных Ollama-моделей."""
        lines = ["<b>Threads-комментатор: проверка</b>"]

        try:
            me = await self.client.me()
            username = str(me.get("username") or "").strip()
            suffix = f" (@{escape(username)})" if username else ""
            lines.append(f"✅ Threads API: подключен{suffix}")
        except Exception as exc:
            lines.append(f"❌ Threads API: {escape(str(exc))}")

        try:
            await self.client.search_recent("Blender", limit=1)
            lines.append("✅ Keyword Search: endpoint доступен")
        except Exception as exc:
            lines.append(f"❌ Keyword Search: {escape(str(exc))}")

        try:
            await self.client.get_my_replies(limit=1)
            lines.append("✅ Чтение replies: доступно")
        except Exception as exc:
            lines.append(f"❌ Чтение replies: {escape(str(exc))}")

        try:
            models = await self.generator.available_models()

            def has_model(required: str) -> bool:
                if required in models:
                    return True
                base = required.split(":", 1)[0]
                return any(name.split(":", 1)[0] == base for name in models)

            if has_model(self.generator.model):
                lines.append(f"✅ Ollama text: {escape(self.generator.model)}")
            else:
                lines.append(f"❌ Ollama text: нет {escape(self.generator.model)}")

            if self.generator.vision_model:
                if has_model(self.generator.vision_model):
                    lines.append(f"✅ Ollama vision: {escape(self.generator.vision_model)}")
                else:
                    lines.append(f"❌ Ollama vision: нет {escape(self.generator.vision_model)}")
            else:
                lines.append("⚪ Ollama vision: выключена")
        except Exception as exc:
            lines.append(f"❌ Ollama: {escape(str(exc))}")

        lines.append(
            "\nНужные scopes токена: "
            "<code>threads_basic</code>, "
            "<code>threads_keyword_search</code>, "
            "<code>threads_read_replies</code>, "
            "<code>threads_content_publish</code>, "
            "<code>threads_manage_replies</code>."
        )
        lines.append(
            "Публикацию специально не тестирую автоматически, чтобы проверка ничего не написала в Threads."
        )
        return "\n".join(lines)

    async def publish(self, post_id: str) -> str:
        row = self.db.get_threads_comment_post(post_id)
        if row is None:
            raise ValueError("Черновик не найден")
        if row["status"] == "sent":
            return str(row["reply_id"] or "")
        draft = str(row["draft_text"] or "").strip()
        if not draft:
            raise ValueError("Пустой комментарий нельзя публиковать")
        reply_id = await self.client.reply(post_id, draft)
        self.db.mark_threads_comment_status(post_id, "sent", reply_id)
        return reply_id

    async def regenerate(self, post_id: str) -> str:
        row = self.db.get_threads_comment_post(post_id)
        if row is None:
            raise ValueError("Черновик не найден")
        previous = str(row["draft_text"] or "")
        new_text = await self.generator.regenerate(str(row["post_text"]), previous)
        self.db.update_threads_comment_draft(post_id, new_text)
        return new_text

    def skip(self, post_id: str) -> None:
        row = self.db.get_threads_comment_post(post_id)
        if row is None:
            raise ValueError("Черновик не найден")
        self.db.mark_threads_comment_status(post_id, "skipped")

    async def run(self, stop_event: asyncio.Event) -> None:
        interval = max(self.settings.threads_comment_scan_minutes, 15) * 60
        # Не запускаем тяжелый поиск сразу при старте. Сначала бот готов принять
        # /threads_scan, а автоматическая проверка начнется после интервала.
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass

            try:
                await self.scan_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Threads comment scan failed")


def register_threads_comment_handlers(
    dp: Dispatcher,
    assistant: ThreadsCommentAssistant,
    settings: Settings,
) -> None:
    def owner_message(message: Message) -> bool:
        return bool(message.chat and message.chat.id == settings.owner_chat_id)

    @dp.message(Command("threads_comments"))
    async def threads_comments_status(message: Message) -> None:
        if not owner_message(message):
            return
        stats = assistant.db.threads_comment_stats()
        state = "пауза" if assistant.is_paused() else "работает"
        await message.answer(
            "Threads-комментатор: "
            f"<b>{state}</b>\n"
            f"Черновики: {stats['drafted']}\n"
            f"Опубликовано: {stats['sent']}\n"
            f"Пропущено: {stats['skipped']}\n"
            f"Ошибки: {stats['failed']}\n"
            f"Проверка каждые {settings.threads_comment_scan_minutes} мин."
        )

    @dp.message(Command("threads_check"))
    async def threads_check(message: Message) -> None:
        if not owner_message(message):
            return
        await message.answer("Проверяю Threads API, replies и Ollama…")
        result = await assistant.diagnostics()
        await message.answer(result)

    @dp.message(Command("threads_scan"))
    async def threads_scan(message: Message) -> None:
        if not owner_message(message):
            return
        await message.answer("Ищу свежий пост и готовлю комментарий…")
        try:
            count = await assistant.scan_once()
        except Exception as exc:
            logging.exception("Manual Threads scan failed")
            await message.answer(f"Не получилось: <code>{escape(str(exc))}</code>")
            return
        if count == 0:
            await message.answer("Нового подходящего поста сейчас не нашла.")

    @dp.message(Command("threads_pause"))
    async def threads_pause(message: Message) -> None:
        if not owner_message(message):
            return
        assistant.pause()
        await message.answer("Поиск комментариев Threads поставлен на паузу.")

    @dp.message(Command("threads_resume"))
    async def threads_resume(message: Message) -> None:
        if not owner_message(message):
            return
        assistant.resume()
        await message.answer("Поиск комментариев Threads снова работает.")

    @dp.callback_query(F.data.startswith("tc:"))
    async def threads_comment_action(callback: CallbackQuery) -> None:
        if callback.from_user.id != settings.owner_chat_id:
            await callback.answer("Недоступно.", show_alert=True)
            return
        data = callback.data or ""
        try:
            _, action, post_id = data.split(":", 2)
        except ValueError:
            await callback.answer("Некорректная команда.", show_alert=True)
            return

        row = assistant.db.get_threads_comment_post(post_id)
        if row is None:
            await callback.answer("Черновик уже не найден.", show_alert=True)
            return

        try:
            if action == "send":
                await callback.answer("Публикую…")
                await assistant.publish(post_id)
                row = assistant.db.get_threads_comment_post(post_id)
                if callback.message and row is not None:
                    await callback.message.edit_text(
                        assistant.format_card(row, note="✅ Опубликовано"),
                        reply_markup=None,
                        disable_web_page_preview=True,
                    )
                return

            if action == "redo":
                await callback.answer("Переписываю…")
                await assistant.regenerate(post_id)
                row = assistant.db.get_threads_comment_post(post_id)
                if callback.message and row is not None:
                    await callback.message.edit_text(
                        assistant.format_card(row),
                        reply_markup=_keyboard(post_id),
                        disable_web_page_preview=True,
                    )
                return

            if action == "skip":
                assistant.skip(post_id)
                row = assistant.db.get_threads_comment_post(post_id)
                await callback.answer("Пропущено.")
                if callback.message and row is not None:
                    await callback.message.edit_text(
                        assistant.format_card(row, note="⏭ Пропущено"),
                        reply_markup=None,
                        disable_web_page_preview=True,
                    )
                return

            await callback.answer("Неизвестное действие.", show_alert=True)
        except Exception as exc:
            logging.exception("Threads comment action failed: %s", action)
            assistant.db.mark_threads_comment_status(post_id, "failed")
            await callback.answer("Не получилось выполнить действие.", show_alert=True)
            if callback.message:
                await callback.message.answer(f"Ошибка Threads: <code>{escape(str(exc))}</code>")
