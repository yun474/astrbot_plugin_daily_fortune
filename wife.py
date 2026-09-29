"""Daily character selection and QQ native Markdown delivery."""
import asyncio
import hashlib
import json
import re
import time
from html import escape
from pathlib import PurePosixPath
from urllib.parse import quote

from .avatar import field


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

    async def select(self, uid, day):
        key = hashlib.sha256(uid.encode()).hexdigest()
        target = self.folder / f"{key}.json"
        async with self._lock:
            if target.exists():
                saved = json.loads(target.read_text("utf-8"))
                if saved["day"] == day:
                    return saved
            catalog_file = self.folder / "catalog.json"
            if not catalog_file.exists() or time.time() - catalog_file.stat().st_mtime > 86400:
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


def md_text(value):
    value = str(value).replace("\n", " ").replace("\r", " ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", escape(value))


def payload_for(event, item):
    content = (
        f'<qqbot-at-user id="{escape(str(event.get_sender_id()), quote=True)}" />\n'
        f"您的今日老婆是：**{md_text(item['name'])}**\n"
        f"作品：{md_text(item['work'])}\n"
        f"![角色图片]({item['url']})\n"
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


async def send_markdown(event, item):
    from botpy.http import Route

    raw = event.message_obj.raw_message
    group = field(raw, "group_openid")
    if group:
        route = Route("POST", "/v2/groups/{group_openid}/messages", group_openid=group)
    elif field(field(raw, "author"), "user_openid"):
        route = Route("POST", "/v2/users/{openid}/messages", openid=event.get_sender_id())
    else:
        return False  # Channel events use ordinary image delivery.
    payload = payload_for(event, item)
    for attempt in range(2):
        try:
            result = await event.bot.api._http.request(route, json=payload)
            if not isinstance(result, dict):
                raise RuntimeError("QQ 消息发送结果不明")
            if result.get("code") not in (None, 0):
                raise RuntimeError(str(result))
            if not result.get("id"):
                raise RuntimeError("QQ 未返回消息 ID，发送结果不明")
            return True
        except Exception as exc:
            if attempt or not image_failure(exc):
                raise
            await asyncio.sleep(1)
    return False
