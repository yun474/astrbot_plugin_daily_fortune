import importlib
import sys
from types import ModuleType, SimpleNamespace as NS

import pytest


@pytest.mark.asyncio
async def test_command_returns_image_and_plain_fallback_without_nickname(monkeypatch, tmp_path):
    # Minimal public AstrBot contracts; this is not a live platform send test.
    api = ModuleType("astrbot.api")
    api.AstrBotConfig = dict
    event_api = ModuleType("astrbot.api.event")
    event_api.AstrMessageEvent = object
    commands = []

    def command(name, alias):
        commands.append((name, alias))
        return lambda fn: fn

    event_api.filter = NS(command=command)
    star_api = ModuleType("astrbot.api.star")

    class Star:
        def __init__(self, context):
            self.context = context

    star_api.Star = Star
    star_api.Context = object
    star_api.StarTools = NS(get_data_dir=lambda _: tmp_path)
    star_api.register = lambda *args: lambda cls: cls
    monkeypatch.setitem(sys.modules, "astrbot", ModuleType("astrbot"))
    for module in (api, event_api, star_api):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    module = importlib.import_module("daily_fortune_test.main")
    plugin = module.DailyFortune(None, {})
    assert commands == [("今日运势", {"jrys", "运势"})]
    event = NS(
        get_platform_id=lambda: "official-1",
        get_platform_name=lambda: "qq_official",
        get_sender_id=lambda: "OPENID",
        message_obj=NS(raw_message=NS(author=NS(member_openid="OPENID"))),
        bot=NS(platform=NS(appid="123")),
        image_result=lambda path: ("image", path),
        plain_result=lambda text: ("plain", text),
    )
    calls = []

    async def card(uid, day, avatar):
        calls.append((uid, avatar))
        return tmp_path / "card.png"

    plugin.service.card = card
    result = [x async for x in plugin.daily_fortune(event)]
    assert result == [("image", str(tmp_path / "card.png"))]
    assert calls == [("official-1:OPENID", "https://q.qlogo.cn/qqapp/123/OPENID/640")]

    async def fail(*args):
        raise RuntimeError("browser unavailable")

    plugin.service.card = fail
    result = [x async for x in plugin.daily_fortune(event)]
    assert result[0][0] == "plain"
    assert "宜：" in result[0][1] and "忌：" in result[0][1]
    await plugin.terminate()
