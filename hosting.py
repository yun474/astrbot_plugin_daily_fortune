"""Multipart image hosting with persistent per-day URL caching."""
import asyncio
import hashlib
import json
from datetime import date
from urllib.parse import urlsplit

import aiohttp

from .settings import number


def http_url(value):
    if not isinstance(value, str):
        raise ValueError("图床未返回图片直链")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password
            or any(c.isspace() or c in '<>"\\' for c in value)):
        raise ValueError("图床地址必须是 HTTP(S) URL")
    return value


class ImageHost:
    def __init__(self, service):
        self.service = service
        self.config = dict(service.config.get("image_host", {}))
        self.retries = number(self.config, "retry_count", 3, 0, 10)
        self.timeout = number(self.config, "timeout_seconds", 30, 5, 120)
        self._lock = asyncio.Lock()
        self._session = None
        destination = {key: self.config.get(key) for key in (
            "upload_url", "authorization", "file_field", "url_path"
        )}
        self._identity = hashlib.sha256(json.dumps(destination, sort_keys=True).encode()).hexdigest()

    def validate(self):
        http_url(self.config.get("upload_url", ""))
        if not self.config.get("file_field", "file").strip():
            raise ValueError("图床文件字段不能为空")
        if not self.config.get("url_path", "data.links.url").strip():
            raise ValueError("图床图片地址字段不能为空")

    async def upload(self, data, day):
        self.validate()
        date.fromisoformat(day)
        key = hashlib.sha256(self._identity.encode() + data).hexdigest()
        target = self.service.cache / day / f"host-{key}.json"
        async with self._lock:
            if target.is_file():
                return http_url(json.loads(target.read_text("utf-8"))["url"])
            url = await self._upload(data)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            try:
                temporary.write_text(json.dumps({"url": url}), "utf-8")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            return url

    async def _upload(self, data):
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout), trust_env=True
            )
        authorization = self.config.get("authorization", "").strip()
        headers = {"Accept": "application/json"}
        if authorization:
            headers["Authorization"] = authorization
        for attempt in range(self.retries + 1):
            form = aiohttp.FormData()
            form.add_field(self.config.get("file_field", "file"), data,
                           filename=hashlib.sha256(data).hexdigest() + ".png", content_type="image/png")
            try:
                async with self._session.post(self.config["upload_url"], data=form,
                                              headers=headers, allow_redirects=False) as response:
                    if response.status == 429 or response.status >= 500:
                        if attempt < self.retries:
                            await asyncio.sleep(1)
                            continue
                    if not 200 <= response.status < 300:
                        raise ValueError(f"图床上传失败：HTTP {response.status}")
                    result = await response.json(content_type=None)
                    try:
                        for part in self.config.get("url_path", "data.links.url").split("."):
                            result = result[int(part)] if isinstance(result, list) else result[part]
                    except (KeyError, TypeError, IndexError, ValueError):
                        raise ValueError("图床响应缺少配置的图片地址字段") from None
                    return http_url(result)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt >= self.retries:
                    raise RuntimeError("图床上传连接失败或超时") from None
                await asyncio.sleep(1)
        raise RuntimeError("图床上传失败")

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None
