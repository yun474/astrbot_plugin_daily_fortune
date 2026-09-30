import asyncio
import importlib
import sys
from types import ModuleType
from types import SimpleNamespace as NS

import pytest
from daily_fortune_test.service import default_avatar


@pytest.mark.asyncio
async def test_command_returns_image_and_plain_fallback_without_nickname(
    monkeypatch, tmp_path
):
    # Minimal public AstrBot contracts; this is not a live platform send test.
    api = ModuleType("astrbot.api")
    api.AstrBotConfig = dict
    event_api = ModuleType("astrbot.api.event")
    event_api.AstrMessageEvent = object
    commands = []
    priorities = {}

    def command(name, alias, *, priority=0):
        commands.append((name, alias))

        def register(fn):
            priorities[fn.__name__] = priority
            return fn

        return register

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
    event.should_call_llm = lambda value: setattr(event, "call_llm", value)
    event.stop_event = lambda: setattr(event, "stopped", True)

    async def dispatch(handler):
        # Model a default-priority listener registered before this plugin. It
        # requests an LLM explicitly, bypassing should_call_llm's default gate.
        event.call_llm = False
        event.stopped = False
        results = []
        llm_requests = []

        async def chat_listener(event):
            llm_requests.append("plugin request")
            yield ("plain", "unexpected LLM reply")

        handlers = [(0, chat_listener), (priorities[handler.__name__], handler)]
        for _, callback in sorted(handlers, key=lambda item: -item[0]):
            if event.stopped:
                break
            async for result in callback(event):
                # Stopping before yield would swallow this reply in AstrBot.
                assert not event.stopped
                results.append(result)
        if not event.stopped and not results and not event.call_llm:
            llm_requests.append("default request")
        assert llm_requests == []
        assert event.call_llm is True
        assert event.stopped
        return results

    calls = []

    async def card(uid, day, avatar):
        calls.append((uid, avatar))
        return tmp_path / "card.png"

    plugin.service.card = card
    result = await dispatch(plugin.daily_fortune)
    assert result == [("image", str(tmp_path / "card.png"))]
    assert calls == [("official-1:OPENID", "https://q.qlogo.cn/qqapp/123/OPENID/640")]

    async def fail(*args):
        raise RuntimeError("browser unavailable")

    plugin.service.card = fail
    result = await dispatch(plugin.daily_fortune)
    assert result[0][0] == "plain"
    assert "宜：" in result[0][1] and "忌：" in result[0][1]

    async def select(uid, day):
        return {"name": "芙宁娜", "work": "原神", "url": "https://example.com/wife.jpg"}

    plugin.wife.select = fail
    assert await dispatch(plugin.daily_wife) == [
        ("plain", "角色图库暂时无法读取，请稍后再试。")
    ]

    sent = []

    async def send_md(event, item, **kwargs):
        sent.append(item)
        return True

    plugin.wife.select = select
    plugin.qq_wife_markdown = True
    event.message_obj.raw_message.group_openid = "GROUP"
    uploads = []

    async def download(url):
        return default_avatar()

    async def upload(data, day):
        uploads.append(data)
        return "https://host.example.com/image.png"

    plugin.service._download = download
    plugin.image_host.validate = lambda: None
    plugin.image_host.upload = upload
    monkeypatch.setattr(module, "send_markdown", send_md)
    assert await dispatch(plugin.daily_wife) == []
    assert sent[0]["work"] == "原神"
    assert sent[0]["url"] == "https://host.example.com/image.png"
    plugin.qq_fortune_markdown = True
    (tmp_path / "card.png").write_bytes(default_avatar())
    plugin.service.card = card
    assert await dispatch(plugin.daily_fortune) == []
    assert sent[-1] == {
        "url": "https://host.example.com/image.png",
        "width": 128,
        "height": 128,
    }
    assert sent[0]["width"] == sent[0]["height"] == 128
    assert len(uploads) == 2

    async def upload_fail(*args):
        raise ValueError("图床未配置")

    plugin.image_host.upload = upload_fail
    failed_fortune = await dispatch(plugin.daily_fortune)
    failed_wife = await dispatch(plugin.daily_wife)
    assert failed_fortune[0][0] == failed_wife[0][0] == "plain"
    assert "图床配置" in failed_fortune[0][1] and "图床配置" in failed_wife[0][1]
    assert len(sent) == 2  # Never send the original remote URL after upload failure.
    event.get_platform_name = lambda: "aiocqhttp"
    event.get_sender_id = lambda: "123456"

    async def wife_card(uid, item, avatar):
        assert uid == "official-1:123456"
        assert item["name"] == "芙宁娜"
        assert avatar == "https://q1.qlogo.cn/g?b=qq&nk=123456&s=100"
        return tmp_path / "wife.png"

    plugin.wife.card = wife_card
    result = await dispatch(plugin.daily_wife)
    assert result == [("image", str(tmp_path / "wife.png"))]
    event.get_platform_name = lambda: "qq_official"
    event.message_obj.raw_message.author.member_openid = "123456"
    plugin.qq_wife_markdown = False

    async def official_card(uid, item, avatar):
        return tmp_path / "wife.png"

    plugin.wife.card = official_card
    assert await dispatch(plugin.daily_wife) == [("image", str(tmp_path / "wife.png"))]
    assert len(sent) == 2
    plugin.wife.card = fail
    result = await dispatch(plugin.daily_wife)
    assert len(result) == 1 and result[0][0] == "plain"
    assert "芙宁娜" in result[0][1] and "图片生成失败" in result[0][1]

    original_calls = []

    async def original(uid, day):
        original_calls.append(uid)
        return tmp_path / "original.png"

    plugin.service.original = plugin.wife.original = original
    event.get_platform_name = lambda: "qq_official"
    event.message_obj.raw_message = {
        "content": '<@BOT> /运势原图 <qqbot-at-user id="OTHER" />'
    }
    event.message_obj.self_id = "BOT"
    assert await dispatch(plugin.fortune_original) == [
        ("image", str(tmp_path / "original.png"))
    ]
    assert original_calls[-1] == "official-1:OTHER"
    event.message_obj.raw_message = {"content": "<@BOT> /老婆原图"}
    assert await dispatch(plugin.wife_original) == [
        ("image", str(tmp_path / "original.png"))
    ]
    assert original_calls[-1] == "official-1:123456"

    async def missing(*args):
        return None

    plugin.wife.original = missing
    assert await dispatch(plugin.wife_original) == []
    plugin.service.original = missing
    assert await dispatch(plugin.fortune_original) == []
    plugin.wife.original = fail
    assert await dispatch(plugin.wife_original) == []
    plugin.service.original = fail
    assert await dispatch(plugin.fortune_original) == []
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
