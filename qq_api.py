"""QQ 官方开放接口请求：沿用 botpy 的鉴权会话，同时保留 botpy 丢弃的平台错误码。"""
import aiohttp

QQ_SEND_TIMEOUT = 35


class QQSendError(RuntimeError):
    def __init__(self, status, data, trace_id):
        self.status = status
        self.code = str(data.get("err_code", data.get("code", "")))
        super().__init__(f"QQ 请求失败：HTTP {status}，错误码 {self.code or '未知'}："
                         f"{data.get('message', '无错误描述')}，trace_id={trace_id or '无'}")


async def qq_request(http, method, url, payload=None, expected="id", timeout=QQ_SEND_TIMEOUT):
    """expected 为必须返回的字段；为 None 时允许成功响应不带响应体（如素材删除接口）。"""
    await http.check_session()
    async with http._session.request(
        method=method, url=url, headers=http._headers, json=payload,
        timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=False,
    ) as response:
        try:
            result = await response.json(content_type=None)
        except ValueError:
            # 网关错误常返回 HTML；保留状态以便素材上传重试，不伪造平台错误码。
            if response.status >= 500:
                raise QQSendError(response.status, {}, response.headers.get("X-Tps-trace-ID")) from None
            raise RuntimeError(f"QQ 响应不是有效 JSON（HTTP {response.status}），结果不明") from None
        if result is None and expected is None and 200 <= response.status < 300:
            return {}
        if not isinstance(result, dict):
            if response.status >= 500:
                raise QQSendError(response.status, {}, response.headers.get("X-Tps-trace-ID"))
            raise RuntimeError(f"QQ 响应不是 JSON 对象（HTTP {response.status}），结果不明")
        code = result.get("err_code", result.get("code"))
        if not 200 <= response.status < 300 or code not in (None, 0, "0"):
            trace_id = result.get("trace_id") or response.headers.get("X-Tps-trace-ID")
            raise QQSendError(response.status, result, trace_id)
        if expected is not None and not result.get(expected):
            raise RuntimeError(f"QQ 未返回 {expected}，结果不明")
        return result
