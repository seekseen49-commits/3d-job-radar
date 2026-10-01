from pathlib import Path

from database import Database


def test_threads_comment_draft_lifecycle(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    try:
        db.save_threads_comment_draft(
            "123",
            "artist",
            "A useful Blender post with enough text.",
            "https://threads.net/t/123",
            "нормальный комментарий)",
        )
        row = db.get_threads_comment_post("123")
        assert row is not None
        assert row["status"] == "drafted"
        assert row["draft_text"] == "нормальный комментарий)"

        db.update_threads_comment_draft("123", "второй вариант")
        assert db.get_threads_comment_post("123")["draft_text"] == "второй вариант"

        db.mark_threads_comment_status("123", "sent", "reply-1")
        row = db.get_threads_comment_post("123")
        assert row["status"] == "sent"
        assert row["reply_id"] == "reply-1"
        assert db.threads_comment_stats()["sent"] == 1
    finally:
        db.close()


def test_threads_comment_pause_setting(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    try:
        assert db.get_value("threads_comments_paused", "0") == "0"
        db.set_value("threads_comments_paused", "1")
        assert db.get_value("threads_comments_paused") == "1"
    finally:
        db.close()
