"""Passive bridge: Threads browser -> GitHub inbox -> ChatGPT automation.

This script does NOT generate comments and does NOT publish to Threads.
It only collects fresh public Threads posts into state/threads_inbox/*.json
and pushes them to this GitHub repository. ChatGPT handles selection/replies.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from threads_browser_client import ThreadsBrowserClient


BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = BASE_DIR / "state"
INBOX_DIR = STATE_DIR / "threads_inbox"
SEEN_PATH = STATE_DIR / "threads_collector_seen.json"

QUERIES = (
    "3д моделирование",
    "Blender",
    "3D artist",
    "рендер",
    "визуализация 3д",
    "видеомонтаж",
    "монтажер",
    "motion design",
    "After Effects",
    "Unreal Engine",
    "gamedev artist",
    "фриланс дизайнер",
    "портфолио дизайнер",
    "нейросети дизайнер",
    "Canva дизайнер",
    "3D printing",
)


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default
    return value if value > 0 else default


def _run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=BASE_DIR,
        text=True,
        capture_output=True,
        check=check,
    )


def _sync_from_remote() -> None:
    result = _run_git("pull", "--rebase", check=False)
    if result.returncode != 0:
        logging.warning("git pull --rebase failed: %s", (result.stderr or result.stdout).strip())


def _push_inbox() -> bool:
    _run_git("add", "state/threads_inbox", "state/threads_collector_seen.json", check=False)
    diff = _run_git("diff", "--cached", "--quiet", check=False)
    if diff.returncode == 0:
        return False

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    commit = _run_git("commit", "-m", f"Queue Threads candidates {stamp}", check=False)
    if commit.returncode != 0:
        logging.warning("git commit failed: %s", (commit.stderr or commit.stdout).strip())
        return False

    push = _run_git("push", check=False)
    if push.returncode != 0:
        logging.warning("git push failed: %s", (push.stderr or push.stdout).strip())
        return False
    return True


def _load_seen() -> set[str]:
    if not SEEN_PATH.exists():
        return set()
    try:
        data = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    items = data.get("ids", []) if isinstance(data, dict) else []
    return {str(item) for item in items if str(item)}


def _save_seen(seen: set[str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Keep the file bounded; browser IDs are hashes so order is not meaningful.
    payload = {"ids": sorted(seen)[-5000:]}
    SEEN_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _fresh(timestamp: str | None, max_age_hours: int) -> bool:
    if not timestamp:
        return False
    try:
        published = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - published.astimezone(timezone.utc)
    return -timedelta(minutes=5) <= age <= timedelta(hours=max_age_hours)


def _safe_filename(post_id: str, permalink: str) -> str:
    raw = f"{post_id}|{permalink}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24] + ".json"


async def collect_once(client: ThreadsBrowserClient, *, max_age_hours: int, max_new: int) -> int:
    _sync_from_remote()
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    seen = _load_seen()

    query_index_path = STATE_DIR / "threads_bridge_query_index.txt"
    try:
        query_index = int(query_index_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        query_index = 0

    rotated = QUERIES[query_index:] + QUERIES[:query_index]
    query_batch = rotated[:6]
    query_index = (query_index + len(query_batch)) % len(QUERIES)
    query_index_path.write_text(str(query_index), encoding="utf-8")

    new_count = 0
    for query in query_batch:
        try:
            posts = await client.search_recent(query, limit=12)
        except Exception:
            logging.exception("Threads search failed for %r", query)
            continue

        for post in posts:
            if post.id in seen:
                continue
            seen.add(post.id)

            text = post.text.strip()
            if len(text) < 25 or not _fresh(post.timestamp, max_age_hours):
                continue
            if not post.permalink:
                continue

            payload = {
                "id": post.id,
                "username": post.username,
                "text": text,
                "permalink": post.permalink,
                "timestamp": post.timestamp,
                "query": query,
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
            target = INBOX_DIR / _safe_filename(post.id, post.permalink)
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            new_count += 1
            if new_count >= max_new:
                break

        if new_count >= max_new:
            break

    _save_seen(seen)
    pushed = _push_inbox()
    logging.info("Threads bridge: queued=%s, pushed=%s", new_count, pushed)
    return new_count


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one collection cycle and exit.")
    args = parser.parse_args()

    load_dotenv(BASE_DIR / ".env", override=True)
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")

    profile_raw = os.getenv("THREADS_BROWSER_PROFILE_DIR", "threads_browser_profile").strip() or "threads_browser_profile"
    profile_dir = Path(profile_raw)
    if not profile_dir.is_absolute():
        profile_dir = BASE_DIR / profile_dir

    client = ThreadsBrowserClient(
        profile_dir,
        headless=_bool("THREADS_BROWSER_HEADLESS", False),
        channel=os.getenv("THREADS_BROWSER_CHANNEL", "msedge").strip() or "msedge",
    )

    interval_minutes = _int("THREADS_CHATGPT_COLLECT_MINUTES", 30)
    max_age_hours = _int("THREADS_CHATGPT_MAX_AGE_HOURS", 72)
    max_new = _int("THREADS_CHATGPT_MAX_NEW_PER_SCAN", 12)

    try:
        while True:
            await collect_once(client, max_age_hours=max_age_hours, max_new=max_new)
            if args.once:
                break
            await asyncio.sleep(interval_minutes * 60)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
