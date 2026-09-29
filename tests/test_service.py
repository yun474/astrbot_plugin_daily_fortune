import asyncio
import io

import pytest
from PIL import Image

from daily_fortune_test.service import FortuneService, default_avatar, normalize_image


@pytest.mark.asyncio
async def test_concurrent_requests_share_png_and_restart_cache(tmp_path):
    service = FortuneService({}, tmp_path)
    calls = []

    async def quote():
        calls.append("quote")
        return "今天也要开心。", "测试", "测试"

    async def background():
        return default_avatar(), "测试"

    async def avatar(url):
        return default_avatar()

    async def render(html, target):
        calls.append("render")
        await asyncio.sleep(0.01)
        target.write_bytes(default_avatar())

    service._quote, service._background, service._avatar = quote, background, avatar
    service.renderer.render = render
    results = await asyncio.gather(*(service.card("qq:openid", "2026-09-29", None) for _ in range(8)))
    assert len(set(results)) == 1
    assert calls == ["quote", "render"]
    restarted = FortuneService({}, tmp_path)
    assert await restarted.card("qq:openid", "2026-09-29", None) == results[0]
    await service.close()
    await restarted.close()


@pytest.mark.asyncio
async def test_bad_network_data_uses_simple_fallbacks(tmp_path):
    service = FortuneService({"hitokoto_api": "https://example.com", "background_url": "https://example.com"}, tmp_path)

    async def broken(*args):
        return b"not JSON or an image"

    service._download = broken
    quote, background, avatar = await asyncio.gather(service._quote(), service._background(), service._avatar("https://example.com"))
    assert quote[2] == "内置寄语"
    assert background[1] == "妖狐图库"
    assert Image.open(io.BytesIO(background[0])).width > 100
    assert Image.open(io.BytesIO(avatar)).size == (128, 128)
    await service.close()


@pytest.mark.asyncio
async def test_failed_render_does_not_poison_cache(tmp_path):
    service = FortuneService({"hitokoto_api": "", "background_url": "missing.jpg"}, tmp_path)

    async def fail(html, target):
        target.write_bytes(b"partial")
        raise RuntimeError("browser failed")

    service.renderer.render = fail
    with pytest.raises(RuntimeError):
        await service.card("test", "2026-09-29", None)
    assert not list(tmp_path.rglob("*.png"))
    await service.close()


def test_image_validation_and_cache_retention(tmp_path):
    with pytest.raises(OSError):
        normalize_image(b"bad image")
    service = FortuneService({}, tmp_path)
    old = service.cache / "2026-09-01"
    recent = service.cache / "2026-09-28"
    unrelated = service.cache / "keep"
    for path in (old, recent, unrelated):
        path.mkdir()
    service._cleanup("2026-09-29")
    assert not old.exists() and recent.exists() and unrelated.exists()


def test_renderer_change_invalidates_cache(tmp_path, monkeypatch):
    from daily_fortune_test import service as module

    original = FortuneService({}, tmp_path)._revision
    read_bytes = module.Path.read_bytes

    def changed_template(path):
        result = read_bytes(path)
        return result + b"\n# template update" if path.name == "renderer.py" else result

    monkeypatch.setattr(module.Path, "read_bytes", changed_template)
    assert FortuneService({}, tmp_path)._revision != original


@pytest.mark.asyncio
async def test_session_closes_even_when_browser_close_fails(tmp_path):
    service = FortuneService({}, tmp_path)
    closed = []

    class Session:
        async def close(self):
            closed.append(True)

    async def failed_close():
        raise RuntimeError("browser disconnected")

    service._session = Session()
    service.renderer.close = failed_close
    with pytest.raises(RuntimeError):
        await service.close()
    assert closed == [True] and service._session is None
