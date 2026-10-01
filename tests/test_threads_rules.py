from threads_client import ThreadPost
from threads_rules import eligible_post


def post(text: str, username: str = "artist") -> ThreadPost:
    return ThreadPost(id="1", text=text, username=username, permalink="https://threads.net/x")


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
