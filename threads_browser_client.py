"""Локальный поиск публичных Threads-постов через обычный браузер.

Не публикует комментарии, не ставит реакции и не обходит защиту Threads.
Использует отдельный постоянный профиль Playwright; вход выполняется пользователем вручную.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import quote_plus

from playwright.async_api import BrowserContext, Page, async_playwright

from threads_client import ThreadPost


class ThreadsBrowserError(RuntimeError):
    pass


class ThreadsBrowserLoginRequired(ThreadsBrowserError):
    pass


class ThreadsBrowserClient:
    def __init__(
        self,
        profile_dir: Path,
        *,
        headless: bool = True,
        channel: str = "msedge",
        timeout_ms: int = 30000,
    ) -> None:
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.channel = channel.strip() or "msedge"
        self.timeout_ms = timeout_ms
        self._playwright = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def _ensure_started(self) -> Page:
        if self._page is not None:
            return self._page

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            channel=self.channel,
            headless=self.headless,
            viewport={"width": 1440, "height": 1000},
            locale="ru-RU",
        )
        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()
        self._page.set_default_timeout(self.timeout_ms)

        # Не загружаем картинки/видео в поисковом браузере: так профиль не раздувает
        # кэш на диске. URL изображений остаются в DOM, а выбранную картинку при
        # необходимости vision-модель забирает отдельно в RAM через httpx.
        async def block_heavy_media(route):
            if route.request.resource_type in {"image", "media", "font"}:
                await route.abort()
            else:
                await route.continue_()

        await self._page.route("**/*", block_heavy_media)
        return self._page

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._page = None
        self._context = None
        self._playwright = None

    async def _assert_logged_in(self, page: Page) -> None:
        url = page.url.lower()
        if "/login" in url:
            raise ThreadsBrowserLoginRequired(
                "Threads просит вход. Запустите python threads_browser_login.py и войдите вручную."
            )
        body = (await page.locator("body").inner_text()).lower()
        login_markers = ("log in", "войти", "continue with instagram", "продолжить через instagram")
        if any(marker in body for marker in login_markers) and "/search" not in url:
            raise ThreadsBrowserLoginRequired(
                "Threads просит вход. Запустите python threads_browser_login.py и войдите вручную."
            )

    @staticmethod
    def _stable_id(permalink: str) -> str:
        digest = hashlib.sha256(permalink.encode("utf-8")).hexdigest()[:24]
        return f"web:{digest}"

    async def search_recent(self, query: str, *, limit: int = 12) -> list[ThreadPost]:
        page = await self._ensure_started()
        search_url = (
            "https://www.threads.com/search?"
            f"q={quote_plus(query)}&serp_type=default"
        )
        try:
            await page.goto(search_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(900)
            await self._assert_logged_in(page)
        except ThreadsBrowserLoginRequired:
            raise
        except Exception as exc:
            raise ThreadsBrowserError(f"Не удалось открыть поиск Threads: {exc}") from exc

        # Одной короткой прокрутки достаточно для первой пачки результатов.
        await page.mouse.wheel(0, 1100)
        await page.wait_for_timeout(350)

        raw_items = await page.evaluate(
            """(maxItems) => {
                const anchors = Array.from(document.querySelectorAll('a[href*="/post/"]'));
                const out = [];
                const seen = new Set();

                for (const anchor of anchors) {
                    const href = anchor.href || "";
                    if (!href || seen.has(href)) continue;
                    seen.add(href);

                    let node = anchor;
                    let container = null;
                    for (let i = 0; i < 9 && node; i += 1, node = node.parentElement) {
                        const text = (node.innerText || "").trim();
                        if (text.length >= 25 && node.querySelector("time")) {
                            container = node;
                            break;
                        }
                    }
                    container = container || anchor.closest("article") || anchor.parentElement;
                    if (!container) continue;

                    const text = (container.innerText || "").trim();
                    if (text.length < 20) continue;

                    const timeEl = container.querySelector("time");
                    const timestamp = timeEl ? (timeEl.getAttribute("datetime") || "") : "";
                    const images = Array.from(container.querySelectorAll("img"))
                        .filter(img => {
                            const rect = img.getBoundingClientRect();
                            return rect.width >= 120 && rect.height >= 80;
                        })
                        .map(img => img.currentSrc || img.src || "")
                        .filter(src => src && !src.startsWith("data:"))
                        .filter((src, index, arr) => arr.indexOf(src) === index)
                        .slice(0, 3);

                    out.push({href, text, timestamp, images});
                    if (out.length >= maxItems) break;
                }
                return out;
            }""",
            max(limit * 2, 20),
        )

        posts: list[ThreadPost] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            permalink = str(item.get("href") or "").strip()
            text = str(item.get("text") or "").strip()
            if not permalink or not text:
                continue

            match = re.search(r"/@([^/]+)/post/", permalink)
            username = match.group(1) if match else ""
            # Убираем очевидные служебные строки, но не пытаемся "угадывать" сам пост.
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if lines and username and lines[0].lstrip("@").lower() == username.lower():
                lines = lines[1:]

            # Убираем интерфейсный мусор Threads: "49 мин.", "2", счетчики и т.п.
            cleaned_lines: list[str] = []
            ui_noise = {
                "перевести",
                "translate",
                "подробнее",
                "see more",
                "/",
                "к сожалению, воспроизвести это видео не удается.",
                "sorry, we're having trouble playing this video.",
            }
            for line in lines:
                low = line.lower()
                if low in ui_noise:
                    continue
                if re.fullmatch(r"\d+\s*(?:мин\.?|ч\.?|дн\.?|день|дня|дней|m|h|d)", low):
                    continue
                if re.fullmatch(r"\d{1,4}", line):
                    continue
                cleaned_lines.append(line)

            cleaned = "\n".join(cleaned_lines).strip()
            if not cleaned:
                continue

            images_raw = item.get("images")
            images = tuple(
                str(url).strip()
                for url in (images_raw if isinstance(images_raw, list) else [])
                if str(url).strip()
            )[:3]

            posts.append(
                ThreadPost(
                    id=self._stable_id(permalink),
                    text=cleaned,
                    username=username,
                    permalink=permalink,
                    timestamp=str(item.get("timestamp") or "").strip() or None,
                    media_type="IMAGE" if images else None,
                    media_url=images[0] if images else None,
                    image_urls=images,
                )
            )
            if len(posts) >= limit:
                break
        return posts
