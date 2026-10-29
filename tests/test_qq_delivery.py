"""Exercise QQ delivery through the SDK's authenticated HTTP session."""
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from botpy.http import BotHttp, Route

from daily_fortune_test.wife import QQSendError, send_markdown
from test_wife import ITEM


@asynccontextmanager
async def qq_server(monkeypatch, handler, group=True):
    app = web.Application()
    app.router.add_post('/v2/{kind}/{openid}/messages', handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(Route, 'SCHEME', 'http')
    monkeypatch.setattr(Route, 'DOMAIN', f'127.0.0.1:{port}')
    http = BotHttp(timeout=35)
    http._token = NS(check_token=AsyncMock(), get_string=lambda: 'QQBot test-only', app_id='123')
    raw = NS(group_openid='GROUP' if group else None, author=NS(user_openid='USER'))
    event = NS(get_sender_id=lambda: 'USER', message_obj=NS(raw_message=raw, message_id='MSG'),
               bot=NS(api=NS(_http=http)))
    try:
        yield event
    finally:
        await http.close()
        await runner.cleanup()


@pytest.mark.parametrize('group', [True, False])
@pytest.mark.parametrize('fortune', [True, False])
@pytest.mark.parametrize('code', [40034004, 40034141])
@pytest.mark.parametrize('prefix', ['', '/', '云云', '云云 '])
async def test_wire_payload_verifies_images_and_retries_platform_error(monkeypatch, caplog, group, fortune, code, prefix):
    calls = []

    async def handler(request):
        body = await request.json()
        calls.append((request.path, body, request.headers['Authorization']))
        # QQ ignores the old, misplaced top-level flag and can accept a missing image.
        if not body['markdown'].get('force_verify_image_resource'):
            return web.json_response({'id': 'ACCEPTED_WITHOUT_IMAGE'})
        if len(calls) == 1:
            return web.Response(text=json.dumps({'err_code': code, 'message': '图片转存失败，请重试',
                                                'trace_id': 'image-transfer-trace'}),
                                status=400, content_type='application/json', charset='utf-8')
        return web.json_response({'id': 'SENT'})

    async def no_sleep(_):
        pass

    monkeypatch.setattr('daily_fortune_test.wife.asyncio.sleep', no_sleep)
    async with qq_server(monkeypatch, handler, group) as event:
        with caplog.at_level('INFO', logger='astrbot'):
            assert await send_markdown(event, ITEM, fortune=fortune, command_prefix=prefix)
    assert len(calls) == 2
    assert calls[0] == calls[1]
    path, body, auth = calls[0]
    assert path == ('/v2/groups/GROUP/messages' if group else '/v2/users/USER/messages')
    assert auth == 'QQBot test-only'
    assert body['markdown']['force_verify_image_resource'] is True
    assert 'force_verify_image_resource' not in body
    assert body['msg_id'] == 'MSG' and body['msg_seq'] == 1
    buttons = body['keyboard']['content']['rows'][0]['buttons']
    for button, label in zip(buttons, ['今日老婆', '今日运势'], strict=True):
        assert button['render_data']['label'] == label
        assert button['render_data']['visited_label'] == label
        assert button['action']['data'] == prefix + label
        assert button['action']['unsupport_tips'] == '请手动发送' + prefix + label
        assert button['action']['enter'] is True
    assert str(code) in caplog.text and 'image-transfer-trace' in caplog.text
    assert '重试' in caplog.text


@pytest.mark.parametrize('status,code_field,code,retries,expected', [
    (400, 'err_code', 40034004, 3, 4),
    (400, 'err_code', 40034141, 10, 11),
    (400, 'code', '40034141', 3, 4),
    (400, 'err_code', 40034141, 0, 1),
    (400, 'code', 304010, 1, 2),
    (200, 'err_code', 40034004, 1, 2),
    (200, 'code', 304010, 0, 1),
    (403, 'err_code', 40034105, 3, 1),
    (200, 'err_code', 40034011, 3, 1),
])
async def test_platform_codes_control_retries_and_failure_is_reported(
        monkeypatch, caplog, status, code_field, code, retries, expected):
    calls = []

    async def handler(request):
        calls.append(await request.json())
        return web.json_response({code_field: code, 'message': 'resource unavailable', 'id': 'NOT_SENT'},
                                 status=status, headers={'X-Tps-trace-ID': 'failure-trace'})

    async def no_sleep(_):
        pass

    monkeypatch.setattr('daily_fortune_test.wife.asyncio.sleep', no_sleep)
    async with qq_server(monkeypatch, handler) as event:
        with caplog.at_level('INFO', logger='astrbot'):
            with pytest.raises(RuntimeError, match=str(code)):
                await send_markdown(event, ITEM, retries=retries)
    assert len(calls) == expected
    assert all(body == calls[0] for body in calls)
    assert str(code) in caplog.text and 'failure-trace' in caplog.text


@pytest.mark.parametrize('body', ['Bad Gateway', 'null', '[]'])
async def test_unknown_gateway_response_never_retries_or_allows_fallback(monkeypatch, tmp_path, body):
    from test_command import load_main
    module, _, _, _ = load_main(monkeypatch, tmp_path)
    calls = []

    async def handler(request):
        calls.append(await request.json())
        return web.Response(status=502, text=body, headers={'X-Tps-trace-ID': 'unknown-trace'})

    async with qq_server(monkeypatch, handler) as event:
        with pytest.raises(QQSendError) as error:
            await send_markdown(event, ITEM, retries=3)
    assert len(calls) == 1
    assert error.value.status == 502 and error.value.code == ''
    assert 'unknown-trace' in str(error.value)
    assert not module.markdown_unsent('QQ 发送', error.value)
