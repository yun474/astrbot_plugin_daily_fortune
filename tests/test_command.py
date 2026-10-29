import asyncio
import importlib
import sys
from types import ModuleType
from types import SimpleNamespace as NS

import pytest
from daily_fortune_test.material import MaterialUnavailable
from daily_fortune_test.service import default_avatar


def load_main(monkeypatch, tmp_path):
    # Minimal public AstrBot contracts; this is not a live platform send test.
    api = ModuleType("astrbot.api")
    api.AstrBotConfig = dict
    event_api = ModuleType("astrbot.api.event")
    event_api.AstrMessageEvent = object
    commands = []
    priorities = {}
    admins = set()

    def command(name, alias, *, priority=0):
        commands.append((name, alias))

        def register(fn):
            priorities[fn.__name__] = priority
            return fn

        return register

    def permission_type(kind, raise_error=True):
        def register(fn):
            admins.add(fn.__name__)
            fn.permission_raise_error = raise_error
            return fn

        return register

    event_api.filter = NS(command=command, permission_type=permission_type,
                          PermissionType=NS(ADMIN="admin"))
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
    # 每个测试都用当次的替身重新导入插件入口。
    monkeypatch.delitem(sys.modules, "daily_fortune_test.main", raising=False)
    module = importlib.import_module("daily_fortune_test.main")
    return module, commands, priorities, admins


@pytest.mark.asyncio
async def test_command_returns_image_and_plain_fallback_without_nickname(
    monkeypatch, tmp_path
):
    module, commands, priorities, admins = load_main(monkeypatch, tmp_path)
    plugin = module.DailyFortune(None, {"qq_command_prefix": "/"})
    assert ("运势素材库", set()) in commands and admins == {"material_usage"}
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
        return {"name": "芙宁娜", "work": "原神", "url": "https://example.com/wife.jpg", "day": "2026-10-21"}

    plugin.wife.select = fail
    assert await dispatch(plugin.daily_wife) == [
        ("plain", "角色图库暂时无法读取，请稍后再试。")
    ]

    sent = []

    async def send_md(event, item, **kwargs):
        assert kwargs["command_prefix"] == "/"
        sent.append(item)
        return True

    plugin.wife.select = select
    plugin.qq_wife_markdown = True
    event.message_obj.raw_message.group_openid = "GROUP"
    uploads = []
    downloads = []

    async def download(url):
        downloads.append(url)
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

    async def fallback_card(uid, item, avatar):
        return tmp_path / "wife.png"

    plugin.wife.card = fallback_card
    plugin.image_host.upload = upload_fail
    # 发送前失败时消息必然未发出，改发普通图片
    assert await dispatch(plugin.daily_fortune) == [("image", str(tmp_path / "card.png"))]
    assert await dispatch(plugin.daily_wife) == [("image", str(tmp_path / "wife.png"))]
    assert len(sent) == 2  # Never send the original remote URL after upload failure.
    # 当天压缩图已缓存，再次发送老婆 MD 不重新下载原图
    assert downloads == ["https://example.com/wife.jpg"]

    def invalid_host():
        raise ValueError("图床未配置")

    plugin.image_host.validate = invalid_host
    assert await dispatch(plugin.daily_wife) == [("image", str(tmp_path / "wife.png"))]
    plugin.image_host.validate = lambda: None
    plugin.image_host.upload = upload

    async def rejected(*args, **kwargs):
        raise module.QQSendError(400, {"code": 304010, "message": "转存失败"}, "TRACE")

    async def unknown(*args, **kwargs):
        raise asyncio.TimeoutError

    async def bad_gateway(*args, **kwargs):
        # 网关 502 只带描述、没有平台错误码，无法确认消息是否已送达
        raise module.QQSendError(502, {"message": "Bad Gateway"}, None)

    async def unsupported(*args, **kwargs):
        return False

    for handler, image in ((plugin.daily_fortune, "card.png"), (plugin.daily_wife, "wife.png")):
        # 平台明确拒绝或事件不支持 MD 时同样改发普通图片
        for send in (rejected, unsupported):
            monkeypatch.setattr(module, "send_markdown", send)
            assert await dispatch(handler) == [("image", str(tmp_path / image))]
        # 发送结果不明时只提示，不再补发图片，避免重复消息
        for send in (unknown, bad_gateway):
            monkeypatch.setattr(module, "send_markdown", send)
            result = await dispatch(handler)
            assert len(result) == 1 and result[0][0] == "plain"
            assert "结果不明" in result[0][1]
    monkeypatch.setattr(module, "send_markdown", send_md)
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


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("admin", [False, True])
async def test_material_permission_gate_keeps_disabled_command_silent(monkeypatch, tmp_path, enabled, admin):
    from unittest.mock import AsyncMock
    module, _, _, _ = load_main(monkeypatch, tmp_path)
    plugin = module.DailyFortune(None, {"image_host": {"qq": {"usage_command": enabled}}})
    plugin.material.purge = AsyncMock(return_value={"deleted": 0, "failed": 0, "skipped": 0, "remaining": 0})
    event = NS(get_platform_name=lambda: "qq_official", stopped=False, send=AsyncMock(),
               should_call_llm=lambda _: None, plain_result=lambda text: text)
    event.stop_event = lambda: setattr(event, "stopped", True)
    handler = plugin.material_usage
    results = []
    try:
        # 权限过滤发生在处理器执行之前；普通用户不能绕过权限执行删除。
        if not admin:
            if handler.permission_raise_error:
                await event.send("权限不足")
                event.stop_event()
        else:
            results = [reply async for reply in handler(event, "删除全部")]
        event.send.assert_not_awaited()
        if enabled and admin:
            plugin.material.purge.assert_awaited_once_with(event, True)
            assert results and event.stopped
        else:
            plugin.material.purge.assert_not_awaited()
            assert not results and not event.stopped
    finally:
        await plugin.terminate()


async def collect(handler, *args):
    return [result async for result in handler(*args)]


async def test_material_markdown_releases_images_and_falls_back(monkeypatch, tmp_path):
    module, *_ = load_main(monkeypatch, tmp_path)
    plugin = module.DailyFortune(None, {
        "qq_fortune_markdown": True, "qq_wife_markdown": True,
        "image_host": {"provider": "QQ 素材库", "qq": {"cache_minutes": 5}},
    })
    assert plugin.material.cache_minutes == 5 and plugin.material_command
    event = NS(
        get_platform_id=lambda: "official-1",
        get_platform_name=lambda: "qq_official",
        get_sender_id=lambda: "OPENID",
        message_obj=NS(raw_message=NS(group_openid="GROUP", author=NS(member_openid="OPENID"))),
        bot=NS(platform=NS(appid="123")),
        image_result=lambda path: ("image", path),
        plain_result=lambda text: ("plain", text),
        should_call_llm=lambda value: None,
        stop_event=lambda: None,
    )
    (tmp_path / "card.png").write_bytes(default_avatar())

    async def card(*args):
        return tmp_path / "card.png"

    async def wife_card(*args):
        return tmp_path / "wife.png"

    async def select(uid, day):
        return {"name": "芙宁娜", "work": "原神", "url": "https://example.com/wife.jpg", "day": day}

    async def download(url):
        return default_avatar()

    async def host_upload(*args):
        raise AssertionError("选用素材库时不应调用自建图床")

    log = []

    async def upload(event, data):
        log.append(("upload", len(data)))
        return f"IMG{len(log)}", f"qqbot-media://image/v1/{len(log)}"

    async def release(event, image_id):
        log.append(("release", image_id))

    plugin.service.card = card
    plugin.wife.card = wife_card
    plugin.wife.select = select
    plugin.service._download = download
    plugin.image_host.upload = host_upload
    plugin.material.upload = upload
    plugin.material.release = release
    plugin.material.forget = lambda event, image_id: log.append(("forget", image_id))
    sent = []

    async def send_md(event, item, **kwargs):
        sent.append(item["url"])
        # 发送时图片仍被占用，发送结束后才回收。
        assert log[-1][0] == "upload"
        return True

    monkeypatch.setattr(module, "send_markdown", send_md)
    assert await collect(plugin.daily_fortune, event) == []
    assert await collect(plugin.daily_wife, event) == []
    assert sent == ["qqbot-media://image/v1/1", "qqbot-media://image/v1/3"]
    assert [entry[0] for entry in log] == ["upload", "release"] * 2
    assert log[1] == ("release", "IMG1") and log[3] == ("release", "IMG3")

    async def invalid(*args, **kwargs):
        raise module.QQSendError(400, {"code": 40035009, "message": "expired"}, "TRACE")

    # 引用失效：作废缓存并回收，改发普通图片。
    log.clear()
    monkeypatch.setattr(module, "send_markdown", invalid)
    assert await collect(plugin.daily_fortune, event) == [("image", str(tmp_path / "card.png"))]
    assert log == [("upload", log[0][1]), ("forget", "IMG1"), ("release", "IMG1")]

    async def unavailable(*args):
        raise MaterialUnavailable("QQ 素材库已满")

    # 素材库不可用时消息必然未发出，改发普通图片。
    plugin.material.upload = unavailable
    monkeypatch.setattr(module, "send_markdown", send_md)
    assert await collect(plugin.daily_wife, event) == [("image", str(tmp_path / "wife.png"))]
    assert len(sent) == 2

    async def usage(event):
        return {"total": 7, "ours": 2, "cached": 1, "stale": 0, "others": 5}

    purges = []

    async def purge(event, everything=False):
        purges.append(everything)
        return {"deleted": 3, "failed": 0, "skipped": 0}

    plugin.material.usage = usage
    plugin.material.purge = purge
    result = await collect(plugin.material_usage, event)
    assert "7/100" in result[0][1] and "5 分钟" in result[0][1]
    assert "删除 3 张" in (await collect(plugin.material_usage, event, "删除全部"))[0][1]
    assert "本插件的素材" in (await collect(plugin.material_usage, event, "删除缓存"))[0][1]
    assert purges == [True, False]
    assert "用法" in (await collect(plugin.material_usage, event, "随便"))[0][1]

    async def limited(event):
        raise module.QQSendError(429, {"message": "too many"}, None)

    plugin.material.usage = limited
    assert "频率限制" in (await collect(plugin.material_usage, event))[0][1]
    event.get_platform_name = lambda: "aiocqhttp"
    assert "QQ 官方机器人" in (await collect(plugin.material_usage, event))[0][1]
    plugin.material_command = False
    assert await collect(plugin.material_usage, event) == []
    await plugin.terminate()
