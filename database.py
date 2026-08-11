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

    def notifications_paused(self) -> bool:
        return self.get_value("paused", "0") == "1"

    def set_notifications_paused(self, paused: bool) -> None:
        self.connection.execute(
            "INSERT INTO settings(key, value) VALUES ('paused', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("1" if paused else "0",),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
