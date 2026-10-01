"""SQLite-хранилище дедупликации, статистики и паузы уведомлений."""
from __future__ import annotations

import sqlite3
from pathlib import Path


class Database:
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                decision TEXT NOT NULL CHECK(decision IN ('accepted', 'rejected')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(channel_id, message_id)
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS threads_comment_posts (
                post_id TEXT PRIMARY KEY,
                username TEXT NOT NULL DEFAULT '',
                post_text TEXT NOT NULL,
                permalink TEXT NOT NULL DEFAULT '',
                draft_text TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'drafted'
                    CHECK(status IN ('drafted', 'sent', 'skipped', 'failed')),
                reply_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self.connection.commit()

    def is_processed(self, channel_id: int, message_id: int) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM messages WHERE channel_id = ? AND message_id = ?", (channel_id, message_id)
        ).fetchone()
        return row is not None

    def record(self, channel_id: int, message_id: int, accepted: bool) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO messages(channel_id, message_id, decision) VALUES (?, ?, ?)",
            (channel_id, message_id, "accepted" if accepted else "rejected"),
        )
        self.connection.commit()

    def stats(self) -> dict[str, int]:
        rows = self.connection.execute("SELECT decision, COUNT(*) AS count FROM messages GROUP BY decision").fetchall()
        result = {"accepted": 0, "rejected": 0}
        result.update({row["decision"]: row["count"] for row in rows})
        result["sent"] = int(self.get_value("sent", "0"))
        return result

    def increment_sent(self) -> None:
        self.connection.execute(
            "INSERT INTO settings(key, value) VALUES ('sent', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1"
        )
        self.connection.commit()

    def get_value(self, key: str, default: str = "") -> str:
        row = self.connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_value(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.connection.commit()

    def notifications_paused(self) -> bool:
        return self.get_value("paused", "0") == "1"

    def set_notifications_paused(self, paused: bool) -> None:
        self.set_value("paused", "1" if paused else "0")

    def has_threads_comment_post(self, post_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM threads_comment_posts WHERE post_id = ?",
            (post_id,),
        ).fetchone()
        return row is not None

    def save_threads_comment_draft(
        self,
        post_id: str,
        username: str,
        post_text: str,
        permalink: str,
        draft_text: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO threads_comment_posts(post_id, username, post_text, permalink, draft_text, status)
            VALUES (?, ?, ?, ?, ?, 'drafted')
            ON CONFLICT(post_id) DO UPDATE SET
                username = excluded.username,
                post_text = excluded.post_text,
                permalink = excluded.permalink,
                draft_text = excluded.draft_text,
                status = 'drafted',
                updated_at = CURRENT_TIMESTAMP
            """,
            (post_id, username, post_text, permalink, draft_text),
        )
        self.connection.commit()

    def get_threads_comment_post(self, post_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM threads_comment_posts WHERE post_id = ?",
            (post_id,),
        ).fetchone()

    def update_threads_comment_draft(self, post_id: str, draft_text: str) -> None:
        self.connection.execute(
            "UPDATE threads_comment_posts SET draft_text = ?, status = 'drafted', "
            "updated_at = CURRENT_TIMESTAMP WHERE post_id = ?",
            (draft_text, post_id),
        )
        self.connection.commit()

    def mark_threads_comment_status(self, post_id: str, status: str, reply_id: str | None = None) -> None:
        if status not in {"drafted", "sent", "skipped", "failed"}:
            raise ValueError(f"Unsupported Threads comment status: {status}")
        self.connection.execute(
            "UPDATE threads_comment_posts SET status = ?, reply_id = COALESCE(?, reply_id), "
            "updated_at = CURRENT_TIMESTAMP WHERE post_id = ?",
            (status, reply_id, post_id),
        )
        self.connection.commit()

    def threads_comment_stats(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM threads_comment_posts GROUP BY status"
        ).fetchall()
        result = {"drafted": 0, "sent": 0, "skipped": 0, "failed": 0}
        result.update({row["status"]: row["count"] for row in rows})
        return result

    def close(self) -> None:
        self.connection.close()
