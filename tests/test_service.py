import asyncio
import io
import os
import time

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
@pytest.mark.parametrize("days,boundary,expired", [(1, "2026-09-29", "2026-09-28"), (7, "2026-09-23", "2026-09-22")])
async def test_cleanup_retention_and_abandoned_images(tmp_path, days, boundary, expired):
    service = FortuneService({"cache_retention_days": days}, tmp_path)
    for day in {boundary, expired, "2026-09-29"}:
        folder = service.cache / day
        folder.mkdir()
        for name in ("card.png", "wife-card.png", "original-card.png"):
            (folder / name).write_bytes(b"test")
    stale = service.cache / "2026-09-29" / ("a" * 64 + ".tmp.png")
    active = service.cache / "2026-09-29" / ("wife-" + "b" * 64 + ".tmp.png")
    stale.write_bytes(b"partial")
    active.write_bytes(b"partial")
    os.utime(stale, (time.time() - 90000,) * 2)
    unrelated = service.cache / "keep"
    unrelated.mkdir()
    (unrelated / "note.txt").write_text("keep")
    await service.cleanup("2026-09-29")
    assert not (service.cache / expired).exists()
    assert (service.cache / boundary / "original-card.png").exists()
    assert not stale.exists() and active.exists() and unrelated.exists()
    # A later sweep on the same date must still remove newly abandoned files.
    os.utime(active, (time.time() - 90000,) * 2)
    await service.cleanup("2026-09-29")
    assert not active.exists()
    await service.close()


def test_operational_settings_do_not_invalidate_cards(tmp_path):
    first = FortuneService({}, tmp_path)
    second = FortuneService({"cache_retention_days": 1, "render_concurrency": 1,
                             "request_timeout_seconds": 30}, tmp_path)
    assert first._revision == second._revision


@pytest.mark.parametrize("value", [0, -1, 366, True, "7"])
def test_invalid_retention_is_rejected(tmp_path, value):
    with pytest.raises(ValueError, match="cache_retention_days"):
        FortuneService({"cache_retention_days": value}, tmp_path)


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


@pytest.mark.asyncio
async def test_original_keeps_source_resolution_without_redrawing(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (2400, 1800), (25, 100, 210)).save(source)
    service = FortuneService({"background_url": str(source), "hitokoto_api": ""}, tmp_path)

    async def render(html, target):
        target.write_bytes(default_avatar())

    service.renderer.render = render
    assert await service.original("self", "2026-09-29") is None
    await service.card("self", "2026-09-29", None)
    source.unlink()  # Retrieval must use the saved source, never re-fetch a random image.
    result = await service.original("self", "2026-09-29")
    with Image.open(result) as image:
        assert image.size == (2400, 1800)
        assert image.getpixel((100, 100)) == (25, 100, 210)
    assert await service.original("other", "2026-09-29") is None
    assert await service.original("self", "2026-09-30") is None
    restarted = FortuneService({}, tmp_path)
    assert await restarted.original("self", "2026-09-29") == result
    await restarted.close()
    await service.close()


def test_markdown_image_bounds_size_and_preserves_original():
    import io
    import random
    from PIL import Image
    from daily_fortune_test.service import markdown_image
    image = Image.frombytes('RGB', (2400, 1800), random.Random(42).randbytes(2400 * 1800 * 3))
    original = io.BytesIO()
    image.save(original, 'PNG')
    data = original.getvalue()
    result = markdown_image(data)
    assert len(result) <= 1024 * 1024
    assert len(result) < len(data) / 4
    with Image.open(io.BytesIO(result)) as decoded:
        assert decoded.format == 'JPEG'
        assert max(decoded.size) <= 1600
    assert original.getvalue() == data
