from types import SimpleNamespace as NS

import pytest

from daily_fortune_test.avatar import avatar_url
from daily_fortune_test.fortune import draw
from daily_fortune_test.renderer import build_html
from daily_fortune_test.service import default_avatar


def event(platform, author, uid="OPENID", appid="12345"):
    return NS(
        get_platform_name=lambda: platform,
        get_sender_id=lambda: uid,
        message_obj=NS(raw_message=NS(author=author)),
        bot=NS(platform=NS(appid=appid)),
    )


def test_daily_draw_matches_approved_preview():
    view = draw("github:yun474", "2026-09-29")
    assert view["fortune"] == "中吉"
    assert [x[0] for x in view["good"]] == ["熬夜", "听歌"]
    assert [x[0] for x in view["bad"]] == ["尝试新事物", "购物"]
    assert view == draw("github:yun474", "2026-09-29")


def test_draw_distribution_and_disjoint_events():
    views = [draw(f"qq:{i}", "2026-09-29") for i in range(200)]
    assert {v["fortune"] for v in views} == {"凶", "末吉", "小吉", "中吉", "大吉"}
    for view in views:
        assert len({x[0] for x in view["good"] + view["bad"]}) == 4
    assert views != [draw(f"qq:{i}", "2026-09-30") for i in range(200)]


@pytest.mark.parametrize("platform", ["qq_official", "qq_official_webhook"])
@pytest.mark.parametrize("key", ["member_openid", "user_openid"])
def test_official_openid(platform, key):
    assert avatar_url(event(platform, NS(**{key: "ABC"}))) == "https://q.qlogo.cn/qqapp/12345/ABC/640"


def test_channel_avatar_and_no_qq_number_guess():
    assert avatar_url(event("qq_official", {"avatar": "https://example.com/avatar.png"})) == "https://example.com/avatar.png"
    assert avatar_url(event("qq_official", NS(id="123456"))) is None
    assert avatar_url(event("qq_official", NS(user_openid="ABC"), appid=None)) is None
    assert avatar_url(event("aiocqhttp", None, uid="123456")) == "https://q1.qlogo.cn/g?b=qq&nk=123456&s=100"


def test_remote_text_cannot_inject_html():
    view = draw("test", "2026-09-29")
    view.update(quote='<img src=x onerror="alert(1)">', quote_source="<script>", quote_credit="一言", background_credit="自定义")
    html = build_html(view, default_avatar(), default_avatar())
    assert '<img src=x' not in html
    assert '&lt;script&gt;' in html
    assert "Content-Security-Policy" in html
    assert "收好" not in html and "GOOD TO DO" not in html
