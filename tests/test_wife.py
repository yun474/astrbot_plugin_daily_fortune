import asyncio
from types import SimpleNamespace as NS

import pytest

from daily_fortune_test.wife import WifeService, parse_catalog, payload_for, send_markdown


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
    assert [b["action"]["data"] for b in payload["keyboard"]["content"]["rows"][0]["buttons"]] == ["今日老婆", "今日运势"]


@pytest.mark.asyncio
async def test_selection_survives_restart_and_catalog_change(tmp_path):
    calls = []

    async def download(*args):
        calls.append(1)
        return "img2/原神!芙宁娜.jpg\nimg1/作品!角色.png".encode()

    service = NS(cache=tmp_path / "cards", config={}, _download=download)
    wife = WifeService(service)
    results = await asyncio.gather(*(wife.select("user", "2026-09-29") for _ in range(5)))
    assert all(x == results[0] for x in results)
    assert len(calls) == 1
    (wife.folder / "catalog.json").write_text('[]')
    assert await WifeService(service).select("user", "2026-09-29") == results[0]


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
