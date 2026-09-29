import importlib
import asyncio
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
    assert ("今日运势", {"jrys", "运势"}) in commands
    assert ("今日老婆", {"jrlp", "抽老婆"}) in commands
    assert ("运势原图", set()) in commands
    assert ("老婆原图", set()) in commands
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

    async def select(uid, day):
        return {"name": "芙宁娜", "work": "原神", "url": "https://example.com/wife.jpg"}

    sent = []

    async def send_md(event, item):
        sent.append(item)
        return True

    plugin.wife.select = select
    monkeypatch.setattr(module, "send_markdown", send_md)
    assert [x async for x in plugin.daily_wife(event)] == []
    assert sent[0]["work"] == "原神"
    event.get_platform_name = lambda: "aiocqhttp"
    event.get_sender_id = lambda: "123456"

    async def wife_card(uid, item, avatar):
        assert uid == "official-1:123456"
        assert item["name"] == "芙宁娜"
        assert avatar == "https://q1.qlogo.cn/g?b=qq&nk=123456&s=100"
        return tmp_path / "wife.png"

    plugin.wife.card = wife_card
    result = [x async for x in plugin.daily_wife(event)]
    assert result == [("image", str(tmp_path / "wife.png"))]
    event.get_platform_name = lambda: "qq_official"
    event.message_obj.raw_message.author.member_openid = "123456"
    plugin.qq_wife_markdown = False

    async def official_card(uid, item, avatar):
        return tmp_path / "wife.png"

    plugin.wife.card = official_card
    assert [x async for x in plugin.daily_wife(event)] == [("image", str(tmp_path / "wife.png"))]
    assert len(sent) == 1
    plugin.wife.card = fail
    result = [x async for x in plugin.daily_wife(event)]
    assert len(result) == 1 and result[0][0] == "plain"
    assert "芙宁娜" in result[0][1] and "图片生成失败" in result[0][1]

    original_calls = []

    async def original(uid, day):
        original_calls.append(uid)
        return tmp_path / "original.png"

    plugin.service.original = plugin.wife.original = original
    event.get_platform_name = lambda: "qq_official"
    event.message_obj.raw_message = {"content": '<@BOT> /运势原图 <qqbot-at-user id="OTHER" />'}
    event.message_obj.self_id = "BOT"
    assert [x async for x in plugin.fortune_original(event)] == [("image", str(tmp_path / "original.png"))]
    assert original_calls[-1] == "official-1:OTHER"
    event.message_obj.raw_message = {"content": '<@BOT> /老婆原图'}
    assert [x async for x in plugin.wife_original(event)] == [("image", str(tmp_path / "original.png"))]
    assert original_calls[-1] == "official-1:123456"

    async def missing(*args):
        return None

    plugin.wife.original = missing
    assert [x async for x in plugin.wife_original(event)] == []
    plugin.service.original = fail
    assert [x async for x in plugin.fortune_original(event)] == []
    sweeps = []
    completed = asyncio.Event()

    async def cleanup(day):
        sweeps.append(day)
        if len(sweeps) >= 4:
            completed.set()

    plugin.service.cleanup = plugin.wife.cleanup = cleanup
    plugin.cleanup_interval = 0.01
    await plugin.initialize()
    assert len(sweeps) == 2  # Startup, before any new request.
    await asyncio.wait_for(completed.wait(), timeout=1)
    await plugin.terminate()
    assert plugin._cleanup_task.done()
