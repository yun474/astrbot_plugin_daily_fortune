import asyncio
import json
import os
import time
from types import SimpleNamespace as NS

import pytest

from daily_fortune_test.wife import WifeService, parse_catalog, payload_for, send_markdown
from daily_fortune_test.service import FortuneService, default_avatar


def event(request, group=True):
    raw = NS(group_openid="GROUP" if group else None, author=NS(user_openid="USER"))
    return NS(get_sender_id=lambda: "USER", message_obj=NS(raw_message=raw, message_id="MSG"),
              bot=NS(api=NS(_http=NS(request=request))))


ITEM = {"name": "芙宁娜", "work": "原神", "url": "https://example.com/image.jpg"}


def test_catalog_and_payload():
    assert len(parse_catalog("img2/原神!芙宁娜.jpg\nimg2/原神!芙宁娜.jpg\n../坏!图.png")) == 1
    payload = payload_for(event(None), ITEM)
    assert payload["markdown"]["content"].startswith('<qqbot-at-user id="USER" />')
    assert "作品：原神" in payload["markdown"]["content"]
    assert payload["force_verify_image_resource"] is True
    assert payload["markdown"]["content"].endswith("\n\n> 要好好对她哦~\n")
    assert [b["action"]["data"] for b in payload["keyboard"]["content"]["rows"][0]["buttons"]] == ["今日老婆", "今日运势"]
    fortune = payload_for(event(None), {"url": ITEM["url"]}, fortune=True)
    assert fortune["markdown"]["content"].startswith('<qqbot-at-user id="USER" />')
    assert fortune["markdown"]["content"].endswith("\n\n> 请勿迷信，仅供参考\n")
    assert fortune["keyboard"] == payload["keyboard"]


@pytest.mark.asyncio
async def test_selection_survives_restart_and_catalog_change(tmp_path):
    calls = []

    async def download(*args):
        calls.append(1)
        return "img2/原神!芙宁娜.jpg\nimg1/作品!角色.png".encode()

    service = NS(cache=tmp_path / "cards", config={}, _download=download)
    wife = WifeService(service)
    assert await wife.original("user", "2026-09-29") is None
    results = await asyncio.gather(*(wife.select("user", "2026-09-29") for _ in range(5)))
    assert all(x == results[0] for x in results)
    assert len(calls) == 1
    (wife.folder / "catalog.json").write_text('[]')
    assert await WifeService(service).select("user", "2026-09-29") == results[0]
    assert await WifeService(service).original("user", "2026-09-29") == results[0]["url"]
    assert await wife.original("other", "2026-09-29") is None
    assert await wife.original("user", "2026-09-30") is None
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [True, False])
async def test_retry_explicit_image_failure(monkeypatch, group):
    calls = []

    async def request(route, json):
        calls.append((route.url, json))
        if len(calls) == 1:
            raise RuntimeError("image download failed")
        return {"id": "SENT"}

    async def sleep(_):
        pass

    monkeypatch.setattr("daily_fortune_test.wife.asyncio.sleep", sleep)
    assert await send_markdown(event(request, group), ITEM)
    assert len(calls) == 2 and calls[0] == calls[1]
    assert ("/groups/" if group else "/users/") in calls[0][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError(), RuntimeError("permission denied")])
async def test_no_blind_retry(error):
    calls = []

    async def request(*args, **kwargs):
        calls.append(1)
        raise error

    with pytest.raises(type(error)):
        await send_markdown(event(request), ITEM)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_composite_cache_is_per_user_and_survives_restart(tmp_path):
    service = FortuneService({}, tmp_path)
    calls = []

    async def download(url):
        return default_avatar()

    async def render(html, target):
        calls.append(html)
        await asyncio.sleep(0.01)
        target.write_bytes(default_avatar())

    service._download = download
    service.renderer.render = render
    wife = WifeService(service)
    item = dict(ITEM, day="2026-09-29", name="<角色>&", work="作品<一>")
    results = await asyncio.gather(*(wife.card("user-a", item, None) for _ in range(4)))
    assert len(set(results)) == 1 and len(calls) == 1
    assert "&lt;角色&gt;&amp;" in calls[0] and "作品&lt;一&gt;" in calls[0]
    assert await WifeService(service).card("user-a", item, None) == results[0]
    assert await wife.card("user-b", item, None) != results[0]
    assert len(calls) == 2
    await service.close()


@pytest.mark.asyncio
async def test_failed_composite_can_be_retried(tmp_path):
    service = FortuneService({}, tmp_path)

    async def download(url):
        return default_avatar()

    async def render(html, target):
        target.write_bytes(b"incomplete")
        raise RuntimeError("browser failed")

    service._download = download
    service.renderer.render = render
    wife = WifeService(service)
    with pytest.raises(RuntimeError):
        await wife.card("user", dict(ITEM, day="2026-09-29"), None)
    assert not list(service.cache.rglob("*.png"))
    await service.close()


@pytest.mark.asyncio
async def test_cleanup_records_and_temp_files_preserves_catalog(tmp_path):
    service = FortuneService({"cache_retention_days": 1}, tmp_path)
    wife = WifeService(service)
    old = wife.folder / ("a" * 64 + ".json")
    current = wife.folder / ("b" * 64 + ".json")
    old.write_text(json.dumps({"day": "2026-09-28"}))
    current.write_text(json.dumps({"day": "2026-09-29"}))
    catalog = wife.folder / "catalog.json"
    catalog.write_text("[]")
    stale = wife.folder / "catalog.tmp"
    fresh = wife.folder / ("c" * 64 + ".tmp")
    for path in (stale, fresh):
        path.write_text("partial")
    os.utime(stale, (time.time() - 90000,) * 2)
    unrelated = wife.folder / "notes.json"
    unrelated.write_text("{}")
    await wife.cleanup("2026-09-29")
    assert not old.exists() and not stale.exists()
    assert all(path.exists() for path in (current, catalog, fresh, unrelated))
    await service.close()


async def test_qq_hung_request_times_out_and_logs(monkeypatch, caplog):
    from daily_fortune_test import wife as module
    calls = []

    async def request(*args, **kwargs):
        calls.append(1)
        await asyncio.Event().wait()

    monkeypatch.setattr(module, 'QQ_SEND_TIMEOUT', 0.01)
    with caplog.at_level('INFO', logger='astrbot'):
        with pytest.raises(asyncio.TimeoutError):
            await send_markdown(event(request), ITEM)
    assert calls == [1]
    assert '开始请求' in caplog.text and '请求超时' in caplog.text
