"""Daily character selection and QQ native Markdown delivery."""
import asyncio
import hashlib
import json
import logging
import re
import time
from html import escape
from datetime import date as Date, timedelta
from pathlib import PurePosixPath
from urllib.parse import quote

from .avatar import field
from .renderer import ASSETS, build_wife_html
from .service import normalize_image
from .settings import number

logger = logging.getLogger("astrbot")
QQ_SEND_TIMEOUT = 35


def parse_catalog(text):
    result = []
    for line in sorted(set(text.splitlines())):
        path = PurePosixPath(line.strip())
        if path.is_absolute() or ".." in path.parts or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        if "!" not in path.stem:
            continue
        work, name = path.stem.split("!", 1)
        if work and name:
            result.append({"path": str(path), "work": work, "name": name})
    if not result:
        raise ValueError("角色列表为空")
    return result


class WifeService:
    def __init__(self, service):
        self.service = service
        self.folder = service.cache.parent / "wife"
        self.folder.mkdir(exist_ok=True)
        self._lock = asyncio.Lock()
        self.retention_days = number(service.config, "cache_retention_days", 7, 1, 365)
        self.catalog_ttl = number(service.config, "wife_catalog_cache_hours", 24, 1, 168) * 3600
        self._card_revision = hashlib.sha256(
            (ASSETS / "wife.css").read_bytes()
            + (ASSETS.parent / "renderer.py").read_bytes()
        ).hexdigest()

    async def card(self, uid, item, avatar):
        service = self.service
        identity = json.dumps([self._card_revision, uid, item, avatar], sort_keys=True)
        key = hashlib.sha256(identity.encode()).hexdigest()
        target = service.cache / item["day"] / f"wife-{key}.png"
        async with service._locks[int(key[:2], 16) % len(service._locks)]:
            if target.is_file():
                return target
            async with service._slots:
                async with service._cleanup_lock:
                    await asyncio.to_thread(service._cleanup, item["day"])
                target.parent.mkdir(parents=True, exist_ok=True)
                picture, portrait = await asyncio.gather(
                    service._download(item["url"]), service._avatar(avatar)
                )
                picture = await asyncio.to_thread(normalize_image, picture)
                html = build_wife_html(item, picture, portrait)
                temporary = target.with_suffix(".tmp.png")
                try:
                    await service.renderer.render(html, temporary)
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
                return target

    async def select(self, uid, day):
        key = hashlib.sha256(uid.encode()).hexdigest()
        target = self.folder / f"{key}.json"
        async with self._lock:
            if target.exists():
                saved = json.loads(target.read_text("utf-8"))
                if saved["day"] == day:
                    return saved
            catalog_file = self.folder / "catalog.json"
            if not catalog_file.exists() or time.time() - catalog_file.stat().st_mtime > self.catalog_ttl:
                url = self.service.config.get("wife_list_url", "https://animewife.dpdns.org/list.txt")
                try:
                    catalog = parse_catalog((await self.service._download(url, 2 * 1024 * 1024)).decode("utf-8-sig"))
                except Exception:
                    if not catalog_file.exists():
                        raise
                    catalog = json.loads(catalog_file.read_text("utf-8"))
                else:
                    self._save(catalog_file, catalog)
            else:
                catalog = json.loads(catalog_file.read_text("utf-8"))
            index = int.from_bytes(hashlib.sha256(f"{uid}:{day}:wife".encode()).digest(), "big") % len(catalog)
            item = dict(catalog[index], day=day)
            base = self.service.config.get("wife_image_base", "https://raw.githubusercontent.com/monbed/wife/main/")
            item["url"] = base.rstrip("/") + "/" + quote(item["path"], safe="/")
            self._save(target, item)
            return item

    @staticmethod
    def _save(path, data):
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        temp.replace(path)

    async def original(self, uid, day):
        key = hashlib.sha256(uid.encode()).hexdigest()
        async with self._lock:
            path = self.folder / f"{key}.json"
            if not path.is_file():
                return None
            item = json.loads(path.read_text("utf-8"))
            return item["url"] if item["day"] == day else None

    async def cleanup(self, day):
        async with self._lock:
            await asyncio.to_thread(self._cleanup, day)

    def _cleanup(self, day):
        cutoff = Date.fromisoformat(day) - timedelta(days=self.retention_days - 1)
        for path in self.folder.iterdir():
            if path.is_symlink() or not path.is_file():
                continue
            if re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
                try:
                    item = json.loads(path.read_text("utf-8"))
                    expired = Date.fromisoformat(item["day"]) < cutoff
                except (ValueError, KeyError, TypeError):
                    # Broken plugin records are removed only after the retention window.
                    expired = Date.fromtimestamp(path.stat().st_mtime) < cutoff
                if expired:
                    path.unlink()
            elif re.fullmatch(r"(?:[0-9a-f]{64}|catalog)\.tmp", path.name):
                if time.time() - path.stat().st_mtime > 86400:
                    path.unlink()


def md_text(value):
    value = str(value).replace("\n", " ").replace("\r", " ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", escape(value))


def payload_for(event, item, fortune=False):
    details = "今日运势\n" if fortune else (
        f"您的今日老婆是：**{md_text(item['name'])}**\n"
        f"作品：{md_text(item['work'])}\n"
    )
    image_url = item['url'].replace('(', '%28').replace(')', '%29')
    ending = "请勿迷信，仅供参考" if fortune else "要好好对她哦~"
    content = (
        f'<qqbot-at-user id="{escape(str(event.get_sender_id()), quote=True)}" />\n'
        + details + f"![图片]({image_url})\n\n> {ending}\n"
    )
    buttons = [{
        "id": key,
        "render_data": {"label": label, "visited_label": label, "style": 1},
        "action": {"type": 2, "permission": {"type": 2}, "data": label,
                   "enter": True, "unsupport_tips": "请手动发送" + label},
    } for key, label in (("wife", "今日老婆"), ("fortune", "今日运势"))]
    return {"msg_type": 2, "msg_id": event.message_obj.message_id, "msg_seq": 1,
            "force_verify_image_resource": True, "markdown": {"content": content},
            "keyboard": {"content": {"rows": [{"buttons": buttons}]}}}


def image_failure(error):
    """Only explicit resource failures are retryable, not ambiguous timeouts."""
    text = str(error).lower()
    return (any(word in text for word in ("image", "图片"))
            and any(word in text for word in ("download", "fetch", "transfer", "verify", "拉取", "转存", "下载", "校验"))
            and any(word in text for word in ("fail", "error", "失败", "不可用")))


def supports_markdown(event):
    if event.get_platform_name() not in {"qq_official", "qq_official_webhook"}:
        return False
    raw = event.message_obj.raw_message
    return bool(field(raw, "group_openid") or field(field(raw, "author"), "user_openid"))


async def send_markdown(event, item, retries=3, fortune=False):
    from botpy.http import Route

    raw = event.message_obj.raw_message
    group = field(raw, "group_openid")
    if group:
        route = Route("POST", "/v2/groups/{group_openid}/messages", group_openid=group)
    elif field(field(raw, "author"), "user_openid"):
        route = Route("POST", "/v2/users/{openid}/messages", openid=event.get_sender_id())
    else:
        return False  # Channel events use ordinary image delivery.
    payload = payload_for(event, item, fortune)
    for attempt in range(retries + 1):
        try:
            logger.info("QQ Markdown：开始请求，尝试 %d/%d，超时 %d 秒", attempt + 1, retries + 1, QQ_SEND_TIMEOUT)
            result = await asyncio.wait_for(
                event.bot.api._http.request(route, json=payload), timeout=QQ_SEND_TIMEOUT
            )
            if not isinstance(result, dict):
                raise RuntimeError("QQ 消息发送结果不明")
            if result.get("code") not in (None, 0):
                raise RuntimeError(str(result))
            if not result.get("id"):
                raise RuntimeError("QQ 未返回消息 ID，发送结果不明")
            logger.info("QQ Markdown：平台已返回消息 ID，发送成功")
            return True
        except asyncio.CancelledError:
            logger.warning("QQ Markdown：请求被取消，发送结果不明")
            raise
        except asyncio.TimeoutError:
            logger.error("QQ Markdown：请求超时，发送结果不明，不自动重发")
            raise
        except Exception as exc:
            logger.error("QQ Markdown：请求失败（%s）：%s", type(exc).__name__, exc)
            if attempt >= retries or not image_failure(exc):
                raise
            await asyncio.sleep(1)
    return False
