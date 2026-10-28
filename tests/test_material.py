"""用本地 HTTP 服务模拟 QQ 素材接口与预签名上传，验证上传、回收、缓存与降级。"""
import asyncio
import hashlib
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from botpy.http import BotHttp

from daily_fortune_test import material as module
from daily_fortune_test.material import MaterialLibrary, MaterialUnavailable, purge_text, usage_text
from daily_fortune_test.service import default_avatar, markdown_image

JPEG = markdown_image(default_avatar())


class FakeQQ:
    def __init__(self):
        self.images = {}
        self.calls = []
        self.prepare_errors = []
        self.delete_errors = []
        self.counter = 0

    def add(self, name, age, image_id=None):
        self.counter += 1
        image_id = image_id or f"OLD{self.counter}"
        self.images[image_id] = {"image_id": image_id, "file_name": name,
                                 "created_at": int(time.time()) - age}
        return image_id

    async def prepare(self, request):
        body = await request.json()
        self.calls.append(("prepare", body, request.headers.get("Authorization")))
        if self.prepare_errors:
            status, code = self.prepare_errors.pop(0)
            return web.json_response({"code": code, "message": "rejected"}, status=status)
        if len(self.images) >= 100:
            return web.json_response({"code": 40035007, "message": "full"}, status=400)
        self.pending = body
        port = request.url.port
        return web.json_response({"upload_id": "UP", "expires_in": 300,
                                  "presigned_url": f"http://127.0.0.1:{port}/cos/a%2Fb?sig=x%2By"})

    async def put(self, request):
        data = await request.read()
        # 预签名地址必须原样使用，否则签名会失效。
        assert request.raw_path == "/cos/a%2Fb?sig=x%2By"
        assert request.headers["Content-Type"] == self.pending["content_type"]
        self.calls.append(("put", len(data), None))
        return web.Response(headers={"ETag": f'"{hashlib.md5(data).hexdigest()}"'})

    async def complete(self, request):
        body = await request.json()
        self.calls.append(("complete", body, None))
        assert body["md5"] == self.pending["md5"]
        self.counter += 1
        image_id = f"IMG{self.counter}"
        self.images[image_id] = {"image_id": image_id, "file_name": self.pending["file_name"],
                                 "created_at": int(time.time())}
        return web.json_response({"image_id": image_id, "url": f"qqbot-media://image/v1/{image_id}"})

    async def listing(self, request):
        self.calls.append(("list", None, None))
        return web.json_response({"images": list(self.images.values()), "next_cursor": "",
                                  "total": len(self.images)})

    async def delete(self, request):
        image_id = request.match_info["image_id"]
        self.calls.append(("delete", image_id, None))
        if self.delete_errors:
            status, code = self.delete_errors.pop(0)
            return web.json_response({"code": code}, status=status)
        if self.images.pop(image_id, None) is None:
            return web.json_response({"code": 40035008}, status=404)
        return web.Response(status=200)

    def count(self, kind):
        return sum(call[0] == kind for call in self.calls)


@asynccontextmanager
async def qq(monkeypatch, cache_minutes=0, retries=1):
    fake = FakeQQ()
    app = web.Application()
    app.router.add_post("/v2/media-images/prepare", fake.prepare)
    app.router.add_put("/cos/{key}", fake.put)
    app.router.add_post("/v2/media-images/complete", fake.complete)
    app.router.add_get("/v2/media-images", fake.listing)
    app.router.add_delete("/v2/media-images/{image_id}/delete", fake.delete)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(module, "API_DOMAIN", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(module, "RETRY_DELAY", 0)
    http = BotHttp(timeout=35)
    http._token = NS(check_token=AsyncMock(), get_string=lambda: "QQBot test-only", app_id="123")
    event = NS(bot=NS(api=NS(_http=http)))
    library = MaterialLibrary(cache_minutes, retries, 5)
    try:
        yield fake, library, event
    finally:
        await library.close()
        await http.close()
        await runner.cleanup()


async def test_upload_send_and_delete_right_away(monkeypatch):
    async with qq(monkeypatch) as (fake, library, event):
        image_id, url = await library.upload(event, JPEG)
        assert url == f"qqbot-media://image/v1/{image_id}"
        _, body, auth = fake.calls[1]
        assert auth == "QQBot test-only"
        assert body["file_name"].startswith("fortune-") and body["file_name"].endswith(".jpg")
        assert body["content_type"] == "image/jpeg" and body["file_size"] == len(JPEG)
        assert body["md5"] == hashlib.md5(JPEG).hexdigest()
        assert body["sha1"] == hashlib.sha1(JPEG).hexdigest()
        assert image_id in fake.images
        await library.release(event, image_id)
        assert fake.images == {}
        # 第二次上传不再重复扫描素材库。
        await library.release(event, (await library.upload(event, JPEG))[0])
        assert fake.count("list") == 1 and fake.images == {}


async def test_startup_sweep_only_removes_own_old_leftovers(monkeypatch):
    async with qq(monkeypatch) as (fake, library, event):
        old = fake.add("fortune-0123456789abcdef.jpg", 3600)
        fresh = fake.add("fortune-fedcba9876543210.jpg", 10)
        other = fake.add("piggy-0123456789abcdef.png", 3600)
        image_id, _ = await library.upload(event, JPEG)
        assert old not in fake.images
        assert {fresh, other, image_id} <= fake.images.keys()
        await library.release(event, image_id)


@pytest.mark.parametrize("status,code,full", [(429, 0, False), (400, 40035007, True),
                                              (403, 11253, False)])
async def test_unavailable_material_library_is_reported_for_fallback(monkeypatch, status, code, full):
    async with qq(monkeypatch, retries=3) as (fake, library, event):
        fake.prepare_errors = [(status, code)]
        with pytest.raises(MaterialUnavailable) as error:
            await library.upload(event, JPEG)
        assert error.value.full is full
        assert fake.count("prepare") == 1


async def test_retryable_upload_errors_are_retried(monkeypatch):
    async with qq(monkeypatch, retries=2) as (fake, library, event):
        fake.prepare_errors = [(500, 40035006), (400, 40035003)]
        image_id, _ = await library.upload(event, JPEG)
        assert fake.count("prepare") == 3
        await library.release(event, image_id)
        fake.prepare_errors = [(400, 40035001)]
        with pytest.raises(module.QQSendError):
            await library.upload(event, JPEG)
        assert fake.count("prepare") == 4


async def test_cache_reuses_image_and_full_library_releases_cache(monkeypatch):
    async with qq(monkeypatch, cache_minutes=10) as (fake, library, event):
        first, _ = await library.upload(event, JPEG)
        await library.release(event, first)
        second, _ = await library.upload(event, JPEG)
        assert second == first and fake.count("prepare") == 1
        await library.release(event, second)
        assert first in fake.images
        for index in range(99):
            fake.add(f"other-{index}.png", 10)
        # 额度已满：先删掉本插件闲置的缓存，再上传新图片。
        image_id, _ = await library.upload(event, default_avatar())
        assert first not in fake.images and image_id in fake.images
        assert fake.count("prepare") == 3
        await library.release(event, image_id)
        # 缓存到期后转入删除队列。
        for cached in library.accounts["123"].cache.values():
            cached.expires = 0
        await library.release(event, (await library.upload(event, JPEG))[0])
        assert image_id not in fake.images


async def test_failed_delete_retries_and_close_removes_cache(monkeypatch):
    async with qq(monkeypatch, cache_minutes=10) as (fake, library, event):
        plain = MaterialLibrary(0, 1, 5)
        try:
            image_id, _ = await plain.upload(event, JPEG)
            fake.delete_errors = [(500, 0)]
            await plain.release(event, image_id)
            account = plain.accounts["123"]
            assert image_id in fake.images and image_id in account.pending
            account.pending[image_id].due = 0
            await plain._maintain(account, 1, scan=False)
            assert image_id not in fake.images and not account.pending
        finally:
            await plain.close()
        cached, _ = await library.upload(event, JPEG)
        await library.release(event, cached)
        assert cached in fake.images
        await library.close()
        assert cached not in fake.images
        with pytest.raises(RuntimeError):
            await library.upload(event, JPEG)
        # 停用后的回收调用直接跳过，不影响已经发出的消息。
        await library.release(event, cached)


async def test_usage_and_purge_skip_images_in_flight(monkeypatch):
    async with qq(monkeypatch, cache_minutes=10) as (fake, library, event):
        cached, _ = await library.upload(event, JPEG)
        await library.release(event, cached)
        sending, _ = await library.upload(event, default_avatar())
        stale = fake.add("fortune-aaaaaaaaaaaaaaaa.jpg", 3600)
        other = fake.add("piggy-0123456789abcdef.png", 3600)
        usage = await library.usage(event)
        assert usage == {"total": 4, "ours": 3, "cached": 2, "stale": 1, "others": 1}
        text = usage_text(usage, True, 10)
        assert "4/100" in text and "10 分钟" in text
        result = await library.purge(event)
        assert result == {"deleted": 2, "failed": 0, "skipped": 1, "remaining": 0}
        assert fake.images.keys() == {sending, other} and stale not in fake.images
        result = await library.purge(event, everything=True)
        assert result == {"deleted": 1, "failed": 0, "skipped": 1, "remaining": 0}
        assert "跳过正在发送的 1 张" in purge_text(result, True)
        await library.release(event, sending)
        assert fake.images == {}


async def test_background_discovers_orphans_without_another_upload(monkeypatch):
    monkeypatch.setattr(module, "MAINTENANCE_INTERVAL", 0.01)
    async with qq(monkeypatch, cache_minutes=10) as (fake, library, event):
        sending, _ = await library.upload(event, JPEG)
        fake.images[sending]["created_at"] -= 3600
        old = fake.add("fortune-orphan.jpg", 3600)
        fresh = fake.add("fortune-fresh.jpg", 1)
        other = fake.add("piggy-old.png", 3600)
        library.accounts["123"].next_sweep = 0

        async def wait_for_cleanup():
            while old in fake.images:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_for_cleanup(), 2)
        assert fake.count("prepare") == 1 and fake.count("list") >= 2
        assert {fresh, other, sending} <= fake.images.keys()
        await library.release(event, sending)


async def test_usage_and_manual_purge_do_not_enable_background_sweeping(monkeypatch):
    async with qq(monkeypatch) as (fake, library, event):
        old = fake.add("fortune-old.jpg", 3600)
        assert (await library.usage(event))["stale"] == 1
        assert library.worker is None and old in fake.images
        assert not library.accounts["123"].sweep_enabled
        assert (await library.purge(event))["deleted"] == 1
        assert library.worker is None


async def test_close_exits_even_when_inflight_maintenance_swallows_cancellation(monkeypatch):
    monkeypatch.setattr(module, "MAINTENANCE_INTERVAL", 0.01)
    library = MaterialLibrary()
    entered = asyncio.Event()
    calls = 0

    async def finishing_request(*args, **kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # 模拟 Python 3.10 请求完成与 wait_for 取消同时发生的边界。
            return

    monkeypatch.setattr(library, "_maintain", finishing_request)
    library.accounts['test'] = module.MaterialAccount(None, sweep_enabled=True)
    library.worker = asyncio.create_task(library._run_maintenance())
    await asyncio.wait_for(entered.wait(), 1)
    shutdown = asyncio.create_task(library.close())
    done, _ = await asyncio.wait({shutdown}, timeout=1)
    try:
        assert shutdown in done, "维护结束后必须退出，不能在关闭状态下继续轮询"
        shutdown.result()
        assert calls == 1 and library.worker is None and not library.accounts
    finally:
        if not shutdown.done():
            # 失败时终止替身任务，避免旧实现的无限循环拖住测试退出。
            monkeypatch.setattr(library, "_maintain", AsyncMock(side_effect=asyncio.CancelledError))
            await asyncio.wait_for(shutdown, 1)


@pytest.mark.parametrize("stage", ["prepare", "complete"])
@pytest.mark.parametrize("status", [502, 503])
async def test_non_json_gateway_errors_retry_material_upload(monkeypatch, stage, status):
    original = getattr(FakeQQ, stage)
    calls = 0

    async def flaky(self, request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return web.Response(status=status, text="Bad Gateway", content_type="text/html")
        return await original(self, request)

    monkeypatch.setattr(FakeQQ, stage, flaky)
    async with qq(monkeypatch, retries=3) as (fake, library, event):
        image_id, _ = await library.upload(event, JPEG)
        assert calls == 2
        await library.release(event, image_id)
        assert not fake.images


async def test_purge_has_one_deadline_and_reports_unprocessed_images(monkeypatch):
    async with qq(monkeypatch) as (fake, library, event):
        for index in range(10):
            fake.add(f"fortune-{index}.jpg", 3600)
        monkeypatch.setattr(module, "ADMIN_BUDGET", 0.15)
        calls = []

        async def blocked_delete(account, image_id, deadline=None):
            calls.append(image_id)
            # 先模拟一张完成，再让下一张耗尽整次操作预算。
            if len(calls) == 1:
                fake.images.pop(image_id)
                return
            timeout = library.timeout if deadline is None else max(0, deadline - time.monotonic())
            await asyncio.wait_for(asyncio.Event().wait(), timeout)

        monkeypatch.setattr(library, "_delete", blocked_delete)
        result = await asyncio.wait_for(library.purge(event), 1)
        assert result == {"deleted": 1, "failed": 1, "skipped": 0, "remaining": 8}
        assert len(calls) == 2 and not library.accounts["123"].lock.locked()
        assert "剩余 8 张" in purge_text(result, False)


async def test_purge_lock_wait_uses_the_same_budget_without_evicting_cache(monkeypatch):
    async with qq(monkeypatch, cache_minutes=10) as (fake, library, event):
        image_id, _ = await library.upload(event, JPEG)
        await library.release(event, image_id)
        account = library.accounts["123"]
        monkeypatch.setattr(module, "ADMIN_BUDGET", 0.05)
        await account.lock.acquire()
        try:
            result = await asyncio.wait_for(library.purge(event), 1)
            assert result == {"deleted": 0, "failed": 0, "skipped": 0, "remaining": 1}
            assert account.cache and image_id in fake.images
            assert account.lock.locked()
        finally:
            account.lock.release()


@pytest.mark.parametrize("operation", ["usage", "purge"])
async def test_admin_pagination_cannot_reset_total_timeout(monkeypatch, operation):
    calls = 0

    async def endless_list(self, request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return web.json_response({"images": [], "next_cursor": str(calls)})

    monkeypatch.setattr(FakeQQ, "listing", endless_list)
    monkeypatch.setattr(module, "ADMIN_BUDGET", 0.12)
    async with qq(monkeypatch) as (_, library, event):
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(getattr(library, operation)(event), 1)
        assert calls < 10 and library.worker is None
