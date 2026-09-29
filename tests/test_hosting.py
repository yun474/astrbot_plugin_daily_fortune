import asyncio
import json

import pytest
from aiohttp import web

from daily_fortune_test.hosting import ImageHost
from daily_fortune_test.service import FortuneService, default_avatar
from daily_fortune_test.wife import send_markdown
from test_wife import event, ITEM


@pytest.mark.asyncio
async def test_real_multipart_upload_retry_and_disk_cache(tmp_path, monkeypatch):
    calls = []

    async def handler(request):
        form = await request.post()
        calls.append((request.headers.get("Authorization"), form['file'].file.read()))
        if len(calls) < 4:
            return web.json_response({}, status=503)
        return web.json_response({"data": {"links": {"url": "https://cdn.example.com/p.png"}}})

    app = web.Application()
    app.router.add_post('/upload', handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    service = FortuneService({'image_host': {'upload_url': f'http://127.0.0.1:{port}/upload',
                                            'authorization': 'Bearer test-only'}}, tmp_path)
    host = ImageHost(service)

    async def no_sleep(_):
        pass

    monkeypatch.setattr('daily_fortune_test.hosting.asyncio.sleep', no_sleep)
    restarted = ImageHost(service)
    try:
        image = default_avatar()
        result = await asyncio.gather(*(host.upload(image, '2026-09-29') for _ in range(3)))
        assert result == ['https://cdn.example.com/p.png'] * 3
        assert len(calls) == 4
        assert all(auth == 'Bearer test-only' and data == image for auth, data in calls)
        assert await restarted.upload(image, '2026-09-29') == result[0]
        assert len(calls) == 4
        assert 'test-only' not in next(service.cache.rglob('host-*.json')).read_text()
        await service.cleanup('2026-10-10')
        assert not list(service.cache.rglob('host-*.json'))
    finally:
        await host.close()
        await restarted.close()
        await service.close()
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize('status,body', [(401, {}), (200, {'error': 'bad key'}),
                                        (200, {'data': {'links': {'url': 'file:///secret'}}})])
async def test_bad_upload_is_not_cached_or_retried(tmp_path, status, body):
    calls = []

    class Response:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def json(self, **kwargs):
            return body

    Response.status = status

    class Session:
        def post(self, *args, **kwargs):
            calls.append(1)
            return Response()
        async def close(self):
            pass

    service = FortuneService({'image_host': {'upload_url': 'https://example.com/upload'}}, tmp_path)
    host = ImageHost(service)
    host._session = Session()
    with pytest.raises(ValueError):
        await host.upload(default_avatar(), '2026-09-29')
    assert calls == [1] and not list(service.cache.rglob('host-*.json'))
    await host.close()
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('retries', [0, 3, 5])
async def test_qq_image_retries_are_configurable(monkeypatch, retries):
    calls = []

    async def request(route, json):
        calls.append(json)
        raise RuntimeError('image download failed')

    async def no_sleep(_):
        pass

    monkeypatch.setattr('daily_fortune_test.wife.asyncio.sleep', no_sleep)
    with pytest.raises(RuntimeError):
        await send_markdown(event(request), ITEM, retries=retries, fortune=True)
    assert len(calls) == retries + 1
    assert all(x['force_verify_image_resource'] for x in calls)
    assert all(x == calls[0] for x in calls)
    assert '今日运势' in calls[0]['markdown']['content']


def test_configuration_group_is_hidden_by_default():
    from pathlib import Path
    schema = json.loads((Path(__file__).parents[1] / '_conf_schema.json').read_text('utf-8'))
    assert schema['show_image_host']['default'] is False
    assert schema['image_host']['condition'] == {'show_image_host': True}
    assert schema['image_host']['items']['retry_count']['default'] == 3
    assert schema['qq_fortune_markdown']['default'] is False
    assert schema['qq_wife_markdown']['default'] is False


@pytest.mark.asyncio
async def test_unconfigured_host_never_opens_network_session(tmp_path):
    service = FortuneService({}, tmp_path)
    host = ImageHost(service)
    with pytest.raises(ValueError):
        await host.upload(default_avatar(), '2026-09-29')
    assert host._session is None
    assert not list(service.cache.rglob('host-*.json'))
    await service.close()
