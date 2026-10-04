from datetime import datetime, timedelta, timezone

from threads_client import ThreadPost
from threads_rules import eligible_post


def iso_hours_ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def post(text: str, username: str = "artist", *, hours_ago: float = 1) -> ThreadPost:
    return ThreadPost(
        id="1",
        text=text,
        username=username,
        permalink="https://threads.net/x",
        timestamp=iso_hours_ago(hours_ago),
    )


def test_skips_too_short_posts() -> None:
    assert not eligible_post(post("nice render"))


def test_skips_own_posts() -> None:
    assert not eligible_post(
        post("Here is a longer post about Blender modeling today.", "Lumetra"),
        own_username="@lumetra",
    )


def test_accepts_normal_3d_discussion() -> None:
    assert eligible_post(
        post("I finally tried geometry nodes for scattering rocks and the setup was way easier than I expected.")
    )


def test_skips_obvious_promo() -> None:
    assert not eligible_post(post("I am hiring a 3D artist for a new project, DM me if interested."))


def test_skips_risky_topic() -> None:
    assert not eligible_post(post("Политическая партия обсуждает выборы и новые решения для страны."))


def test_accepts_organic_learning_discussion() -> None:
    assert eligible_post(
        post("Закончила курс по Blender и наконец поняла, почему раньше так странно ставила свет в Eevee.")
    )


def test_skips_posts_older_than_twenty_four_hours() -> None:
    assert not eligible_post(
        post("Весь день собирал сцену в Blender и наконец дошел до нормального света.", hours_ago=24.5)
    )


def test_accepts_posts_younger_than_twenty_four_hours() -> None:
    assert eligible_post(
        post("Весь день собирал сцену в Blender и наконец дошел до нормального света.", hours_ago=23.5)
    )


def test_skips_post_without_timestamp() -> None:
    candidate = ThreadPost(
        id="2",
        text="Нормальный пост про Blender, но без времени публикации.",
        username="artist",
        permalink="https://threads.net/x",
        timestamp=None,
    )
    assert not eligible_post(candidate)
