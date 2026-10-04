"""Threads comment assistant: search, draft locally, publish only after Telegram approval."""
from __future__ import annotations

import asyncio
import logging
from html import escape
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from comment_generator import OllamaCommentGenerator
from config import Settings
from database import Database
from threads_client import ThreadPost, ThreadsClient
from threads_rules import eligible_post


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
        self.generator = OllamaCommentGenerator(settings.ollama_base_url, settings.ollama_model)
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
    def _candidate_score(post: ThreadPost) -> tuple[int, int]:
        # Небольшие содержательные посты удобнее для человеческого комментария,
        # чем одно слово или огромная простыня.
        length = len(post.text)
        preferred = 1 if 70 <= length <= 900 else 0
        return preferred, -abs(length - 300)

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
            queries_to_try = min(6, len(self.settings.threads_comment_queries))

            for _ in range(queries_to_try):
                query = self._next_query()
                logging.info("Threads comments: search query=%r", query)

                posts = await self.client.search_recent(query, limit=30)
                candidates = [
                    post
                    for post in posts
                    if not self.db.has_threads_comment_post(post.id)
                    and eligible_post(post, own_username=self.settings.threads_comment_own_username)
                ]
                candidates.sort(key=self._candidate_score, reverse=True)

                if not candidates:
                    logging.info("Threads comments: no new candidates for query=%r", query)
                    continue

                post: ThreadPost | None = None
                for candidate in candidates[:5]:
                    try:
                        if await self.generator.is_relevant(candidate.text):
                            post = candidate
                            break
                    except Exception:
                        logging.exception(
                            "Threads comments: relevance check failed for post=%s",
                            candidate.id,
                        )
                        continue

                if post is None:
                    logging.info(
                        "Threads comments: Ollama rejected all candidates for query=%r",
                        query,
                    )
                    continue

                draft = await self.generator.generate(post.text)
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
                logging.info(
                    "Threads comments: draft created for post=%s query=%r",
                    post.id,
                    query,
                )
                return 1

            logging.info(
                "Threads comments: no suitable post found after %s queries",
                queries_to_try,
            )
            return 0

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
        while not stop_event.is_set():
            try:
                await self.scan_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Threads comment scan failed")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass


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
