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
from threads_browser_client import ThreadsBrowserClient
from threads_client import ThreadPost, ThreadsClient
from threads_rules import ineligibility_reason



CORE_DISCOVERY_QUERIES = (
    "3д моделирование",
    "Blender",
    "рендер",
    "видеомонтаж",
    "motion design",
    "Unreal Engine",
    "фриланс дизайнер",
    "нейросети дизайнер",
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

def _keyboard(post_id: str, *, manual_only: bool = False) -> InlineKeyboardMarkup:
    if manual_only:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Ответила", callback_data=f"tc:done:{post_id}"),
                    InlineKeyboardButton(text="♻️ Переписать", callback_data=f"tc:redo:{post_id}"),
                ],
                [InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"tc:skip:{post_id}")],
            ]
        )
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
        self.settings = settings
        self.db = db
        self.bot = bot
        self.discovery_mode = getattr(settings, "threads_discovery_mode", "api").strip().lower()
        self.manual_reply_only = bool(getattr(settings, "threads_manual_reply_only", False))

        token = (settings.threads_access_token or "").strip()
        if self.discovery_mode == "api" and not token:
            raise ValueError("THREADS_ACCESS_TOKEN is required when THREADS_DISCOVERY_MODE=api")

        self.client = ThreadsClient(token) if token else None
        self.browser_client = (
            ThreadsBrowserClient(
                getattr(settings, "threads_browser_profile_dir"),
                headless=bool(getattr(settings, "threads_browser_headless", False)),
                channel=str(getattr(settings, "threads_browser_channel", "msedge")),
            )
            if self.discovery_mode == "browser"
            else None
        )
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

            if self.discovery_mode == "browser":
                # Браузерный поиск заметно дороже API. Берём 4 запроса за проход
                # и ротируем их, чтобы /threads_scan отвечал быстро, но темы со
                # временем всё равно покрывались широко.
                browser_index = int(self.db.get_value("threads_browser_query_index", "0") or "0")
                browser_index %= max(1, len(queries))
                rotated_queries = queries[browser_index:] + queries[:browser_index]
                queries = rotated_queries[:4]
                self.db.set_value(
                    "threads_browser_query_index",
                    str((browser_index + 4) % max(1, len(queries) if len(queries) < 4 else 40)),
                )

            logging.info("Threads comments: scanning mode=%s queries=%r", self.discovery_mode, queries)
            if self.discovery_mode == "browser":
                if self.browser_client is None:
                    raise RuntimeError("Browser discovery is not initialized")
                results = []
                for query in queries:
                    try:
                        results.append(await self.browser_client.search_recent(query, limit=12))
                    except Exception as exc:
                        results.append(exc)
            else:
                if self.client is None:
                    raise RuntimeError("Threads API client is not initialized")
                results = await asyncio.gather(
                    *(self.client.search_recent(query, limit=30, max_age_hours=24) for query in queries),
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

            ranked_pool = sorted(pool.values(), key=self._candidate_score, reverse=True)
            russian_pool = [post for post in ranked_pool if re.search(r"[А-Яа-яЁё]", post.text)]
            fallback_pool = [post for post in ranked_pool if post not in russian_pool]

            async def keep_relevant(batch: list[ThreadPost]) -> tuple[list[ThreadPost], list[str]]:
                batch = batch[:10]
                contexts = [
                    "\n".join(
                        part
                        for part in (
                            post.text,
                            f"Alt-текст медиа: {post.alt_text}" if post.alt_text else "",
                        )
                        if part
                    )
                    for post in batch
                ]
                if not batch:
                    return [], []
                logging.info("Threads comments: relevance-filtering %s candidates", len(batch))
                indexes = await self.generator.filter_relevant(contexts)
                return (
                    [batch[index] for index in indexes],
                    [contexts[index] for index in indexes],
                )

            # Сначала проверяем русскоязычные посты. Если среди них ничего реально
            # подходящего нет, только тогда делаем второй проход по английским.
            try:
                candidates, candidate_contexts = await keep_relevant(russian_pool)
                if not candidates:
                    candidates, candidate_contexts = await keep_relevant(fallback_pool)
            except Exception:
                logging.exception("Threads comments: relevance filtering failed")
                return 0

            logging.info(
                "Threads comments: language preference russian=%s, relevant=%s",
                len(russian_pool),
                len(candidates),
            )

            if not candidates:
                logging.info("Threads comments: no candidates matched the account interests")
                return 0

            logging.info("Threads comments: ranking %s relevant candidates by text", len(candidates))
            try:
                choice = await self.generator.choose_best(candidate_contexts)
            except Exception:
                logging.exception("Threads comments: batch ranking failed")
                return 0

            if choice is None:
                logging.info("Threads comments: Ollama rejected relevant candidate pool")
                return 0

            post = candidates[choice]
            logging.info("Threads comments: selected post=%s, enriching one post", post.id)

            extra_parts: list[str] = []
            if post.alt_text:
                extra_parts.append(f"Alt-текст медиа: {post.alt_text}")

            if post.image_urls:
                try:
                    visual = await self.generator.describe_visuals(post.image_urls)
                    if visual:
                        extra_parts.append(f"Что видно на медиа: {visual}")
                except Exception:
                    logging.exception("Threads comments: visual analysis failed for selected post")

            if self.client is not None and post.has_replies and not post.id.startswith("web:"):
                try:
                    replies = await self.client.get_replies(post.id, limit=5)
                    if replies:
                        extra_parts.append("Комментарии под постом: " + " | ".join(replies[:5]))
                except Exception:
                    logging.exception("Threads comments: replies fetch failed for selected post")

            extra_context = "\n".join(extra_parts) if extra_parts else None
            logging.info("Threads comments: generating draft for post=%s", post.id)
            draft = await self.generator.generate(
                post.text,
                extra_context=extra_context,
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
                reply_markup=_keyboard(post.id, manual_only=self.manual_reply_only),
                disable_web_page_preview=True,
            )
            logging.info("Threads comments: draft created for post=%s", post.id)
            return 1

    async def diagnostics(self) -> str:
        """Безопасная проверка поиска и локальных Ollama-моделей."""
        lines = ["<b>Threads-комментатор: проверка</b>"]

        if self.discovery_mode == "browser":
            try:
                if self.browser_client is None:
                    raise RuntimeError("Browser discovery is not initialized")
                found = await self.browser_client.search_recent("Blender", limit=5)
                if found:
                    lines.append(f"✅ Поиск Threads через браузер: найдено {len(found)} постов")
                else:
                    lines.append("⚠️ Поиск Threads через браузер работает, но по Blender сейчас пусто")
            except Exception as exc:
                lines.append(f"❌ Поиск Threads через браузер: {escape(str(exc))}")
            lines.append("✅ Режим ответа: вручную по готовому тексту")
        else:
            if self.client is None:
                lines.append("❌ Threads API: токен не задан")
            else:
                try:
                    me = await self.client.me()
                    username = str(me.get("username") or "").strip()
                    suffix = f" (@{escape(username)})" if username else ""
                    lines.append(f"✅ Threads API: подключен{suffix}")
                except Exception as exc:
                    lines.append(f"❌ Threads API: {escape(str(exc))}")

                try:
                    found = await self.client.search_recent("Blender", limit=10, max_age_hours=24)
                    if found:
                        lines.append(f"✅ Keyword Search: свежих найдено {len(found)}")
                    else:
                        lines.append("⚠️ Keyword Search: за 24 ч результатов не вернул")
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

        if self.discovery_mode == "browser" and self.manual_reply_only:
            lines.append(
                "\nMeta App Review для этого режима не нужен: бот только находит пост, "
                "готовит черновик и даёт ссылку. Ответ в Threads вы публикуете сами."
            )
        else:
            lines.append(
                "\nДля API-режима нужны соответствующие Threads permissions из Meta."
            )
        return "\n".join(lines)

    async def publish(self, post_id: str) -> str:
        if self.manual_reply_only:
            raise RuntimeError("Автопубликация отключена: ответьте вручную по готовому тексту.")
        if self.client is None:
            raise RuntimeError("Threads API client is not configured")
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

    def mark_done(self, post_id: str) -> None:
        row = self.db.get_threads_comment_post(post_id)
        if row is None:
            raise ValueError("Черновик не найден")
        self.db.mark_threads_comment_status(post_id, "sent", "manual")

    async def close(self) -> None:
        if self.browser_client is not None:
            await self.browser_client.close()

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

            if action == "done":
                assistant.mark_done(post_id)
                row = assistant.db.get_threads_comment_post(post_id)
                await callback.answer("Отмечено.")
                if callback.message and row is not None:
                    await callback.message.edit_text(
                        assistant.format_card(row, note="✅ Отмечено как отвечено вручную"),
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
                        reply_markup=_keyboard(post_id, manual_only=assistant.manual_reply_only),
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
