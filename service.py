import asyncio
import hashlib
import io
import json
import logging
import re
import shutil
import time
from datetime import date as Date, timedelta
from pathlib import Path

import aiohttp
from PIL import Image, ImageDraw, ImageOps

from .fortune import draw
from .renderer import ASSETS, CardRenderer, build_html
from .settings import number

logger = logging.getLogger("astrbot")
FALLBACK_QUOTE = ("把今天过好，就是给明天最好的礼物。", "今日寄语", "内置寄语")
MAX_DOWNLOAD = 12 * 1024 * 1024


def normalize_image(data: bytes, size=(1600, 1600)) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > 25_000_000:
            raise ValueError("图片尺寸过大")
        image = ImageOps.exif_transpose(image).convert("RGB")
        if size is not None:
            image.thumbnail(size)
        output = io.BytesIO()
        image.save(output, "PNG")
        return output.getvalue()


def default_avatar() -> bytes:
    image = Image.new("RGB", (128, 128), "#eee2e3")
    pen = ImageDraw.Draw(image)
    pen.ellipse((43, 22, 85, 64), fill="#ac8593")
    pen.ellipse((22, 73, 106, 145), fill="#ac8593")
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def markdown_image(data: bytes) -> bytes:
    """Bound MD images to 1600px and 1 MiB without changing original retrieval."""
    with Image.open(io.BytesIO(data)) as source:
        if source.width * source.height > 25_000_000:
            raise ValueError("图片尺寸过大")
        image = ImageOps.exif_transpose(source).convert("RGBA")
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        background = Image.new("RGBA", image.size, "white")
        image = Image.alpha_composite(background, image).convert("RGB")
        for quality in (88, 78, 68):
            output = io.BytesIO()
            image.save(output, "JPEG", quality=quality, optimize=True)
            if output.tell() <= 1024 * 1024:
                return output.getvalue()
            image.thumbnail((int(image.width * .8), int(image.height * .8)), Image.Resampling.LANCZOS)
    raise ValueError("MD 图片压缩后仍超过 1 MiB")


class FortuneService:
    def __init__(self, config, data_dir: Path):
        self.config = dict(config)
        self.retention_days = number(config, "cache_retention_days", 7, 1, 365)
        self.request_timeout = number(config, "request_timeout_seconds", 15, 5, 120)
        self.cache = (Path(data_dir) / "cards").resolve()
        self.cache.mkdir(parents=True, exist_ok=True)
        self.renderer = CardRenderer(str(config.get("browser_executable_path", "")))
        self._session = None
        self._quote_lock = asyncio.Lock()
        self._last_quote_request = 0.0
        self._slots = asyncio.Semaphore(number(config, "render_concurrency", 2, 1, 4))
        self._locks = [asyncio.Lock() for _ in range(32)]
        self._cleaned_day = None
        self._cleanup_lock = asyncio.Lock()
        # Layout/settings changes invalidate PNGs, while the daily draw stays stable.
        visual_config = {key: self.config.get(key) for key in (
            "background_url", "background_credit", "hitokoto_api", "browser_executable_path"
        )}
        fingerprint = hashlib.sha256(json.dumps(visual_config, sort_keys=True).encode())
        for path in (ASSETS / "card.css", ASSETS / "default_background.jpg", Path(__file__).with_name("renderer.py")):
            fingerprint.update(path.read_bytes())
        self._revision = fingerprint.hexdigest()[:12]

    async def _download(self, url: str, limit=MAX_DOWNLOAD) -> bytes:
        if not url.startswith(("https://", "http://")):
            raise ValueError("只支持 HTTP(S) 地址")
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.request_timeout),
                headers={"User-Agent": "Mozilla/5.0 AstrBot-DailyFortune/0.1"},
                trust_env=True,
            )
        async with self._session.get(url) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError("接口响应过大")
            return bytes(data)

    async def _quote(self):
        url = str(self.config.get("hitokoto_api", ""))
        if not url:
            return FALLBACK_QUOTE
        async with self._quote_lock:
            await asyncio.sleep(max(0, 0.55 - (time.monotonic() - self._last_quote_request)))
            self._last_quote_request = time.monotonic()
            try:
                data = json.loads(await self._download(url, 64 * 1024))
                text = data["hitokoto"]
                if not isinstance(text, str) or not text.strip() or len(text) > 100:
                    raise ValueError("一言内容为空或过长")
                source = str(data.get("from") or "一言")[:60]
                return text.strip(), f"《{source}》", "一言"
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError) as exc:
                logger.warning("今日运势一言获取失败，使用内置句子：%s", type(exc).__name__)
                return FALLBACK_QUOTE

    async def _background(self):
        source = str(self.config.get("background_url", "https://acg.yaohud.cn/dm/acg.php"))
        try:
            if source.startswith(("http://", "https://")):
                data = await self._download(source)
            else:
                path = Path(source)
                if not path.is_absolute():
                    path = ASSETS.parent / path
                if path.stat().st_size > MAX_DOWNLOAD:
                    raise ValueError("本地图片过大")
                data = await asyncio.to_thread(path.read_bytes)
            return await asyncio.to_thread(normalize_image, data, None), str(self.config.get("background_credit", "自定义图片"))[:40]
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, Image.DecompressionBombError) as exc:
            logger.warning("今日运势背景获取失败，使用内置插画：%s", type(exc).__name__)
            return await asyncio.to_thread(normalize_image, (ASSETS / "default_background.jpg").read_bytes(), None), "妖狐图库"

    async def _avatar(self, url):
        if url:
            try:
                return await asyncio.to_thread(normalize_image, await self._download(url), (256, 256))
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, Image.DecompressionBombError) as exc:
                logger.warning("今日运势头像获取失败，使用默认头像：%s", type(exc).__name__)
        return default_avatar()

    def _cleanup(self, day: str, force=False):
        if self._cleaned_day == day and not force:
            return
        cutoff = Date.fromisoformat(day) - timedelta(days=self.retention_days - 1)
        for folder in self.cache.iterdir():
            if not folder.is_dir() or folder.is_symlink():
                continue
            try:
                expired = Date.fromisoformat(folder.name) < cutoff
            except ValueError:
                continue
            if expired and folder.resolve().parent == self.cache:
                shutil.rmtree(folder)
            elif folder.resolve().parent == self.cache:
                for path in folder.glob("host-*.tmp"):
                    if (re.fullmatch(r"host-[0-9a-f]{64}\.tmp", path.name)
                            and not path.is_symlink() and path.is_file()
                            and time.time() - path.stat().st_mtime > 86400):
                        path.unlink()
                for path in folder.glob("*.tmp.png"):
                    if (re.fullmatch(r"(?:wife-|original-)?[0-9a-f]{64}\.tmp\.png", path.name)
                            and not path.is_symlink() and path.is_file()
                            and time.time() - path.stat().st_mtime > 86400):
                        path.unlink()
        self._cleaned_day = day

    async def cleanup(self, day: str):
        async with self._cleanup_lock:
            await asyncio.to_thread(self._cleanup, day, True)

    async def card(self, uid: str, day: str, avatar: str | None) -> Path:
        Date.fromisoformat(day)
        key = hashlib.sha256(f"{self._revision}:{uid}".encode()).hexdigest()
        async with self._locks[int(key[:2], 16) % len(self._locks)]:
            target = self.cache / day / f"{key}.png"
            if target.is_file():
                return target
            async with self._slots:
                async with self._cleanup_lock:
                    await asyncio.to_thread(self._cleanup, day)
                target.parent.mkdir(parents=True, exist_ok=True)
                quote, background, portrait = await asyncio.gather(self._quote(), self._background(), self._avatar(avatar))
                view = draw(uid, day)
                view.update(quote=quote[0], quote_source=quote[1], quote_credit=quote[2], background_credit=background[1])
                reduced = await asyncio.to_thread(normalize_image, background[0])
                html = build_html(view, reduced, portrait)
                temporary = target.with_suffix(".tmp.png")
                original = self._original_path(uid, day)
                original_temp = original.with_suffix(".tmp.png")
                try:
                    await self.renderer.render(html, temporary)
                    await asyncio.to_thread(original_temp.write_bytes, background[0])
                    original_temp.replace(original)
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
                    original_temp.unlink(missing_ok=True)
                return target

    def _original_path(self, uid: str, day: str) -> Path:
        Date.fromisoformat(day)
        key = hashlib.sha256(uid.encode()).hexdigest()
        return self.cache / day / f"original-{key}.png"

    async def original(self, uid: str, day: str) -> Path | None:
        key = hashlib.sha256(f"{self._revision}:{uid}".encode()).hexdigest()
        async with self._locks[int(key[:2], 16) % len(self._locks)]:
            path = self._original_path(uid, day)
            return path if path.is_file() else None

    async def close(self):
        try:
            await self.renderer.close()
        finally:
            if self._session:
                await self._session.close()
                self._session = None
