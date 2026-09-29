"""HTTP and S3-compatible image hosting with persistent per-day URL caching."""
import asyncio
import hashlib
import json
from copy import deepcopy
from datetime import date
from urllib.parse import quote, urlsplit

import aiohttp
from yarl import URL

from .settings import number


HTTP_PROVIDERS = {"自定义 HTTP", "兰空 Lsky Pro V2"}
# OSS's S3 compatibility uses V2 signing and virtual-hosted addressing.
S3_PROVIDERS = {
    "Cloudflare R2": ("auto", "path", "s3v4"),
    "AWS S3": ("", "virtual", "s3v4"),
    "阿里云 OSS": ("us-east-1", "virtual", "s3"),
    "腾讯云 COS": ("", "virtual", "s3v4"),
    "七牛云 Kodo": ("", "virtual", "s3v4"),
    "MinIO": ("us-east-1", "path", "s3v4"),
    "Backblaze B2": ("", "path", "s3v4"),
    "DigitalOcean Spaces": ("", "virtual", "s3v4"),
    "其他 S3 兼容存储": ("us-east-1", "path", "s3v4"),
}

PROVIDER_SECTIONS = {
    "自定义 HTTP": "http", "兰空 Lsky Pro V2": "lsky", "Cloudflare R2": "r2",
    "AWS S3": "aws", "阿里云 OSS": "oss", "腾讯云 COS": "cos",
    "七牛云 Kodo": "qiniu", "MinIO": "minio", "Backblaze B2": "b2",
    "DigitalOcean Spaces": "spaces", "其他 S3 兼容存储": "s3",
}
HTTP_FIELDS = ("upload_url", "authorization", "file_field", "url_path")
S3_FIELDS = ("endpoint", "bucket", "access_key_id", "secret_access_key", "region",
             "public_base_url", "key_prefix", "addressing_style")


def migrate_host_config(config):
    """Move the old shared fields to the selected provider once, before saving."""
    host = config.get("image_host", {})
    provider = host.get("provider", "自定义 HTTP")
    section = PROVIDER_SECTIONS.get(provider)
    defaults = {key: "" for key in (*HTTP_FIELDS, *S3_FIELDS)}
    defaults.update(file_field="file", url_path="data.links.url",
                    key_prefix="daily-fortune", addressing_style="auto")
    if not section or not any(host.get(key, value) != value for key, value in defaults.items()):
        return False
    fields = HTTP_FIELDS if provider in HTTP_PROVIDERS else S3_FIELDS
    if provider == "兰空 Lsky Pro V2":
        fields = HTTP_FIELDS[:2]
    target = host.setdefault(section, {})
    # Schema defaults may already be present; an existing destination wins.
    if not target.get(fields[0]):
        target.update({key: host[key] for key in fields if key in host})
    host.update(defaults)
    return True


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
        config = {"image_host": deepcopy(service.config.get("image_host", {}))}
        migrate_host_config(config)
        host = config["image_host"]
        self.provider = host.get("provider", "自定义 HTTP")
        self.config = dict(host.get(PROVIDER_SECTIONS.get(self.provider), {}))
        self.config.update(provider=self.provider,
                           retry_count=host.get("retry_count", 3),
                           timeout_seconds=host.get("timeout_seconds", 30))
        self.retries = number(self.config, "retry_count", 3, 0, 10)
        self.timeout = number(self.config, "timeout_seconds", 30, 5, 120)
        self._lock = asyncio.Lock()
        self._session = None
        self._s3 = None
        destination = {key: value for key, value in self.config.items()
                       if key not in {"retry_count", "timeout_seconds"}}
        self._identity = hashlib.sha256(json.dumps(destination, sort_keys=True).encode()).hexdigest()

    def validate(self):
        if self.provider in S3_PROVIDERS:
            for key in ("endpoint", "public_base_url"):
                parsed = urlsplit(http_url(self.config.get(key, "")))
                if parsed.query or parsed.fragment:
                    raise ValueError(f"{key} 不能包含查询参数或片段")
                if key == "endpoint" and parsed.path.strip("/"):
                    raise ValueError("上传端点应为服务地址，不要包含存储桶或路径")
            for key in ("bucket", "access_key_id", "secret_access_key"):
                if not self.config.get(key, "").strip():
                    raise ValueError(f"对象存储配置缺少 {key}")
            if not (self.config.get("region", "").strip() or S3_PROVIDERS[self.provider][0]):
                raise ValueError("请填写存储桶所属区域 region")
            if self.config.get("addressing_style", "auto") not in {"auto", "path", "virtual"}:
                raise ValueError("对象存储寻址方式无效")
            prefix = self.config.get("key_prefix", "daily-fortune").strip("/")
            if any(part in {".", ".."} for part in prefix.split("/")):
                raise ValueError("对象路径前缀不能包含 . 或 .. 路径段")
            return
        if self.provider not in HTTP_PROVIDERS:
            raise ValueError("未知图床类型，请在图床配置中重新选择")
        http_url(self.config.get("upload_url", ""))
        if self.provider == "兰空 Lsky Pro V2":
            return
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
            url = await self._upload(data, day)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            try:
                temporary.write_text(json.dumps({"url": url}), "utf-8")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            return url

    def _signed_upload(self, key):
        # SDK only signs locally; aiohttp owns network I/O, timeouts and retries.
        if self._s3 is None:
            import boto3
            from botocore.config import Config

            region, style, signature = S3_PROVIDERS[self.provider]
            selected_style = self.config.get("addressing_style", "auto")
            if selected_style != "auto" and self.provider != "阿里云 OSS":
                style = selected_style
            self._s3 = boto3.client(
                "s3", endpoint_url=self.config["endpoint"].rstrip("/"),
                region_name=self.config.get("region", "").strip() or region,
                aws_access_key_id=self.config["access_key_id"].strip(),
                aws_secret_access_key=self.config["secret_access_key"].strip(),
                config=Config(signature_version=signature, s3={"addressing_style": style}),
            )
        return self._s3.generate_presigned_url(
            "put_object", Params={"Bucket": self.config["bucket"].strip(),
                                  "Key": key, "ContentType": "image/png"},
            ExpiresIn=300,
        )

    async def _upload(self, data, day):
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout), trust_env=True
            )
        authorization = self.config.get("authorization", "").strip()
        headers = {"Accept": "application/json"}
        if authorization:
            headers["Authorization"] = authorization
        is_s3 = self.provider in S3_PROVIDERS
        filename = hashlib.sha256(data).hexdigest() + ".png"
        prefix = self.config.get("key_prefix", "daily-fortune").strip("/")
        key = "/".join(part for part in (prefix, day, filename) if part)
        public_url = (http_url(self.config["public_base_url"].rstrip("/") + "/" + quote(key, safe="/"))
                      if is_s3 else None)
        for attempt in range(self.retries + 1):
            if is_s3:
                request = self._session.put(
                    URL(self._signed_upload(key), encoded=True), data=data,
                    headers={"Content-Type": "image/png"}, allow_redirects=False,
                )
            else:
                form = aiohttp.FormData()
                field = "file" if self.provider == "兰空 Lsky Pro V2" else self.config.get("file_field", "file")
                form.add_field(field, data, filename=filename, content_type="image/png")
                request = self._session.post(self.config["upload_url"], data=form,
                                             headers=headers, allow_redirects=False)
            try:
                async with request as response:
                    if response.status == 429 or response.status >= 500:
                        if attempt < self.retries:
                            await asyncio.sleep(1)
                            continue
                    if not 200 <= response.status < 300:
                        raise ValueError(f"图床上传失败：HTTP {response.status}")
                    if is_s3:
                        return public_url
                    result = await response.json(content_type=None)
                    try:
                        path = ("data.links.url" if self.provider == "兰空 Lsky Pro V2"
                                else self.config.get("url_path", "data.links.url"))
                        for part in path.split("."):
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
        if self._s3:
            self._s3.close()
            self._s3 = None
        if self._session:
            await self._session.close()
            self._session = None
