import asyncio
import hashlib
import hmac
import json
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from aiohttp import web

from daily_fortune_test.hosting import (
    HTTP_PROVIDERS, S3_PROVIDERS, PROVIDER_SECTIONS, ImageHost, migrate_host_config,
)
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


def storage_config(**changes):
    return dict(provider='Cloudflare R2', endpoint='https://storage.example.com',
                bucket='test-bucket', access_key_id='test-access',
                secret_access_key='test-secret', public_base_url='https://cdn.example.com',
                **changes)


def verify_v4(request):
    """Independently check the signature received by the HTTP server."""
    query = dict(request.query)
    signature = query.pop('X-Amz-Signature')
    signed_headers = query['X-Amz-SignedHeaders'].split(';')
    canonical_query = '&'.join(f'{quote(k, safe="-_.~")}={quote(v, safe="-_.~")}'
                               for k, v in sorted(query.items()))
    canonical_headers = ''.join(f'{key}:{request.headers[key]}\n' for key in signed_headers)
    canonical = '\n'.join(['PUT', request.raw_path.split('?')[0], canonical_query,
                           canonical_headers, ';'.join(signed_headers), 'UNSIGNED-PAYLOAD'])
    _, day, region, service, terminator = query['X-Amz-Credential'].split('/')
    signing_key = b'AWS4test-secret'
    for part in (day, region, service, terminator):
        signing_key = hmac.new(signing_key, part.encode(), hashlib.sha256).digest()
    scope = '/'.join((day, region, service, terminator))
    to_sign = '\n'.join(['AWS4-HMAC-SHA256', query['X-Amz-Date'], scope,
                         hashlib.sha256(canonical.encode()).hexdigest()])
    expected = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(signature, expected)


@pytest.mark.parametrize('status', [200, 403, 404, 429, 503])
@pytest.mark.parametrize('jpeg', [False, True])
async def test_signed_put_retry_cache_and_public_url(tmp_path, monkeypatch, status, jpeg):
    calls = []
    image = default_avatar()
    if jpeg:
        from daily_fortune_test.service import markdown_image
        image = markdown_image(image)

    async def handler(request):
        verify_v4(request)
        assert request.headers['Content-Type'] == ('image/jpeg' if jpeg else 'image/png')
        assert 'Authorization' not in request.headers
        assert await request.read() == image
        calls.append(request.path)
        if status == 404:
            return web.Response(status=404, text='<Error><Code>NoSuchBucket</Code></Error>')
        return web.Response(status=status if len(calls) == 1 else 200)

    async def no_sleep(_):
        pass

    monkeypatch.setattr('daily_fortune_test.hosting.asyncio.sleep', no_sleep)
    app = web.Application()
    app.router.add_put('/{tail:.*}', handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    config = storage_config(key_prefix='运势/space +')
    config['endpoint'] = f'http://127.0.0.1:{port}'
    service = FortuneService({'image_host': config}, tmp_path)
    host = ImageHost(service)
    restarted = ImageHost(service)
    try:
        if status in (403, 404):
            with pytest.raises(ValueError, match='NoSuchBucket' if status == 404 else '403'):
                await host.upload(image, '2026-09-29')
            assert len(calls) == 1
            assert not list(service.cache.rglob('host-*.json'))
        else:
            result = await host.upload(image, '2026-09-29')
            assert result == ('https://cdn.example.com/' + quote('运势/space +', safe='/')
                              + '/2026-09-29/' + hashlib.sha256(image).hexdigest()
                              + ('.jpg' if jpeg else '.png'))
            assert await restarted.upload(image, '2026-09-29') == result
            assert restarted._session is None
            assert len(calls) == (2 if status in (429, 503) else 1)
            assert len(set(calls)) == 1
            assert 'test-secret' not in next(service.cache.rglob('host-*.json')).read_text()
    finally:
        await host.close()
        await restarted.close()
        await service.close()
        await runner.cleanup()


@pytest.mark.parametrize('provider', S3_PROVIDERS)
async def test_provider_signing_and_addressing(tmp_path, provider):
    config = storage_config(region='example-region')
    config['provider'] = provider
    service = FortuneService({'image_host': config}, tmp_path)
    host = ImageHost(service)
    try:
        host.validate()
        parsed = urlsplit(host._signed_upload('test/a b.png'))
        query = parse_qs(parsed.query)
        if provider == '阿里云 OSS':
            assert query['AWSAccessKeyId'] == ['test-access']
            assert 'Signature' in query
        else:
            assert query['X-Amz-Algorithm'] == ['AWS4-HMAC-SHA256']
            assert '/example-region/s3/aws4_request' in query['X-Amz-Credential'][0]
        if S3_PROVIDERS[provider][1] == 'virtual':
            assert parsed.hostname == 'test-bucket.storage.example.com'
            assert parsed.path == '/test/a%20b.png'
        else:
            assert parsed.hostname == 'storage.example.com'
            assert parsed.path == '/test-bucket/test/a%20b.png'
    finally:
        await host.close()
        await service.close()


@pytest.mark.parametrize('changes', [
    {'bucket': ''}, {'access_key_id': ''}, {'secret_access_key': ''},
    {'public_base_url': ''}, {'public_base_url': 'https://cdn.example.com/?token=x'},
    {'endpoint': 'https://storage.example.com/bucket'},
    {'provider': 'AWS S3', 'region': ''}, {'provider': '不存在'},
    {'key_prefix': '../private'}, {'addressing_style': 'invalid'},
])
async def test_invalid_storage_config_before_network(tmp_path, changes):
    config = storage_config()
    config.update(changes)
    service = FortuneService({'image_host': config}, tmp_path)
    host = ImageHost(service)
    with pytest.raises(ValueError):
        await host.upload(default_avatar(), '2026-09-29')
    assert host._session is None and host._s3 is None
    await service.close()


@pytest.mark.parametrize('key,value', [
    ('provider', 'MinIO'), ('bucket', 'other'), ('endpoint', 'https://other.example.com'),
    ('public_base_url', 'https://other.example.com'), ('key_prefix', 'other'),
    ('access_key_id', 'other'), ('secret_access_key', 'other'),
])
def test_storage_configuration_changes_invalidate_cache(tmp_path, key, value):
    config = storage_config()
    first = ImageHost(FortuneService({'image_host': config}, tmp_path))
    config[key] = value
    second = ImageHost(FortuneService({'image_host': config}, tmp_path))
    assert first._identity != second._identity


def test_provider_dropdown_covers_all_adapters():
    from pathlib import Path
    schema = json.loads((Path(__file__).parents[1] / '_conf_schema.json').read_text('utf-8'))
    provider = schema['image_host']['items']['provider']
    assert provider['default'] == '自定义 HTTP'
    assert set(provider['options']) == HTTP_PROVIDERS | S3_PROVIDERS.keys()


@pytest.mark.parametrize('provider,section', PROVIDER_SECTIONS.items())
def test_only_selected_provider_settings_are_visible(provider, section):
    from pathlib import Path
    schema = json.loads((Path(__file__).parents[1] / '_conf_schema.json').read_text('utf-8'))
    items = schema['image_host']['items']
    # AstrBot evaluates conditions against siblings using exact equality.
    visible = {key for key, item in items.items() if not item.get('invisible')
               and all({'provider': provider}.get(k) == v
                       for k, v in item.get('condition', {}).items())}
    assert visible == {'provider', section, 'retry_count', 'timeout_seconds'}
    fields = set(items[section]['items'])
    if provider in S3_PROVIDERS:
        assert 'endpoint' in fields and 'upload_url' not in fields
        assert 'authorization' not in fields
    else:
        assert 'upload_url' in fields and 'endpoint' not in fields
        assert ('file_field' in fields) == (provider == '自定义 HTTP')


def test_provider_switch_preserves_settings_and_cache_identity(tmp_path):
    host = {'provider': 'Cloudflare R2', 'r2': storage_config(),
            'http': {'upload_url': 'https://http.example.com/upload'}}
    config = {'image_host': host}
    first = ImageHost(FortuneService(config, tmp_path))
    host['provider'] = '自定义 HTTP'
    http = ImageHost(FortuneService(config, tmp_path))
    assert http.config['upload_url'] == 'https://http.example.com/upload'
    assert 'endpoint' not in http.config
    host['http']['authorization'] = 'Bearer another-token'
    host['provider'] = 'Cloudflare R2'
    switched_back = ImageHost(FortuneService(config, tmp_path))
    assert switched_back.config['endpoint'] == 'https://storage.example.com'
    assert 'authorization' not in switched_back.config
    assert switched_back._identity == first._identity


@pytest.mark.parametrize('provider,section', PROVIDER_SECTIONS.items())
def test_legacy_migration_is_saved_once_without_overwriting_other_providers(provider, section):
    from pathlib import Path
    schema = json.loads((Path(__file__).parents[1] / '_conf_schema.json').read_text('utf-8'))
    defaults = {k: item['default']
                for k, item in schema['image_host']['items'][section]['items'].items()}
    host = storage_config()
    host.update(provider=provider, upload_url='https://old.example.com/upload',
                authorization='Bearer old-token', **{section: defaults})
    config = {'image_host': host}
    assert migrate_host_config(config)
    if provider in S3_PROVIDERS:
        assert host[section]['secret_access_key'] == 'test-secret'
        assert host[section]['endpoint'] == 'https://storage.example.com'
    else:
        assert host[section]['upload_url'] == 'https://old.example.com/upload'
        assert host[section]['authorization'] == 'Bearer old-token'
    assert host['endpoint'] == host['upload_url'] == host['secret_access_key'] == ''
    assert not migrate_host_config(config)
    host['provider'] = 'MinIO' if section != 'minio' else 'Cloudflare R2'
    assert not migrate_host_config(config)


def test_legacy_migration_does_not_replace_configured_section():
    host = storage_config()
    host['r2'] = {'endpoint': 'https://new.example.com', 'bucket': 'new-bucket'}
    assert migrate_host_config({'image_host': host})
    assert host['r2'] == {'endpoint': 'https://new.example.com', 'bucket': 'new-bucket'}


@pytest.mark.parametrize('body,expected', [
    (b'<Error><Code>NoSuchBucket</Code><Message>secret-credential</Message></Error>', 'NoSuchBucket'),
    (b'<Error xmlns="urn:s3"><Code>SignatureDoesNotMatch</Code></Error>', 'SignatureDoesNotMatch'),
    (b'<Error><Code>secret-credential</Code></Error>', 'S3 API'),
    (b'<html>secret-credential</html>', 'S3 API'),
    (b'', 'S3 API'),
])
async def test_upload_diagnostics_never_echo_response_credentials(tmp_path, body, expected):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    service = FortuneService({'image_host': storage_config()}, tmp_path)
    host = ImageHost(service)
    response = SimpleNamespace(status=404, content=SimpleNamespace(read=AsyncMock(return_value=body)))
    error = str(await host._upload_error(response))
    assert expected in error and 'Cloudflare R2' in error and '404' in error
    assert 'secret-credential' not in error
    await service.close()
