"""QQ 官方素材库：图片随 MD 消息上传、发送后回收，删除失败时在后台重试，尽量少占共用额度。"""
import asyncio
import contextlib
import hashlib
import logging
import time
from dataclasses import dataclass, field
from urllib.parse import quote

import aiohttp
from yarl import URL

from .qq_api import QQSendError, qq_request

logger = logging.getLogger("astrbot")

QQ_MATERIAL = "QQ 素材库"
API_DOMAIN = "https://api.bot.qq.com"
SANDBOX_DOMAIN = "https://sandbox.api.sgroup.qq.com"
NAME_PREFIX = "fortune-"
# 只清理足够旧的残留图片，避免误删其他请求正在使用的素材。
SWEEP_AGE = 600
SWEEP_INTERVAL = 600
MAINTENANCE_INTERVAL = 30
RETRY_DELAY = 1
FOREGROUND_BUDGET = 1.0
BACKGROUND_BUDGET = 5.0
CLOSE_BUDGET = 3.0
ADMIN_BUDGET = 30.0
FULL = "40035007"
MISSING = "40035008"
UNAVAILABLE = {
    FULL: "QQ 素材库已满 100 张（与其他插件共用），请清理后重试。",
    "11253": "当前机器人暂无 QQ 素材库接口权限。",
}
# 上传任务失效、ETag 不一致或服务端内部错误时，重新申请即可。
RETRYABLE = {"40035003", "40035006"}
# 发送时素材引用已失效，作废缓存后下次重新上传。
INVALID_REFERENCE = {"40035009", "40035010"}


class MaterialUnavailable(RuntimeError):
    """素材库暂时无法使用（限流、额度已满或无权限），由调用方改发普通图片。"""

    def __init__(self, message, *, full=False):
        super().__init__(message)
        self.full = full


@dataclass
class PendingDelete:
    attempts: int = 0
    due: float = 0.0


@dataclass
class CachedImage:
    image_id: str
    url: str
    expires: float


@dataclass
class MaterialAccount:
    http: object
    next_sweep: float = 0.0
    # 只有实际使用过素材上传的账号才启用自动扫描；查询和手动清理不启用。
    sweep_enabled: bool = False
    pending: dict = field(default_factory=dict)
    # 引用计数：每条发送中的消息和每个缓存条目各占一次，归零后才进入删除队列。
    active: dict = field(default_factory=dict)
    # 按图片内容 MD5 缓存的素材，只在本进程内有效。
    cache: dict = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def describe(error):
    """把素材库异常转换成可以直接回复给管理员的提示。"""
    if isinstance(error, QQSendError):
        if error.status == 429:
            return "QQ 素材库接口触发频率限制，请稍后重试。"
        if error.code in UNAVAILABLE:
            return UNAVAILABLE[error.code]
        return f"QQ 素材库请求失败（错误码 {error.code or '未知'}，HTTP {error.status}），请稍后重试。"
    if isinstance(error, (asyncio.TimeoutError, aiohttp.ClientError)):
        return "QQ 素材库请求超时或连接中断，请稍后重试。"
    return "QQ 素材库操作失败，请查看日志。"


class MaterialLibrary:
    def __init__(self, cache_minutes=0, retries=3, timeout=30):
        self.cache_minutes = cache_minutes
        self.retries = retries
        self.timeout = timeout
        self.accounts = {}
        self.worker = None
        self.closed = False
        self._session = None

    def _account(self, event, *, sweep=False):
        if self.closed:
            raise RuntimeError("素材库正在关闭，请稍后重试")
        http = event.bot.api._http
        # 多个 QQ 机器人各有独立的素材库，按 AppID 分开管理。
        account = self.accounts.setdefault(str(http._token.app_id), MaterialAccount(http))
        account.http = http
        if sweep:
            account.sweep_enabled = True
        if sweep and self.worker is None:
            self.worker = asyncio.create_task(self._run_maintenance())
        return account

    async def _run_maintenance(self):
        # 底层请求恰好完成时可能未传播取消，关闭状态仍须阻止进入下一轮。
        while not self.closed:
            await asyncio.sleep(MAINTENANCE_INTERVAL)
            for account in list(self.accounts.values()):
                if self.closed:
                    return
                if account.sweep_enabled:
                    await self._maintain(account, BACKGROUND_BUDGET, scan=True)

    async def close(self):
        self.closed = True
        if self.worker is not None:
            self.worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.worker
            self.worker = None
        # 停用后缓存不再复用，尽量当场删除；没删完的由下次启动后的扫描回收。
        deadline = time.monotonic() + CLOSE_BUDGET
        for account in self.accounts.values():
            self._evict(account)
            with contextlib.suppress(Exception):
                await self._delete_pending(account, deadline)
        self.accounts.clear()
        if self._session is not None:
            await self._session.close()
            self._session = None

    @staticmethod
    def _hold(account, image_id):
        account.active[image_id] = account.active.get(image_id, 0) + 1

    @staticmethod
    def _release(account, image_id):
        remaining = account.active.get(image_id, 1) - 1
        if remaining > 0:
            account.active[image_id] = remaining
        else:
            account.active.pop(image_id, None)
            account.pending.setdefault(image_id, PendingDelete())

    def _uncache(self, account, digest):
        cached = account.cache.pop(digest, None)
        if cached is not None:
            self._release(account, cached.image_id)

    def _evict(self, account, *, expired_only=False):
        now = time.monotonic()
        evicted = []
        for digest, cached in list(account.cache.items()):
            if not expired_only or cached.expires <= now:
                evicted.append(cached.image_id)
                self._uncache(account, digest)
        return evicted

    async def _call(self, account, method, path, payload=None, expected=None, deadline=None):
        timeout = self.timeout
        if deadline is not None:
            timeout = min(timeout, deadline - time.monotonic())
            if timeout <= 0:
                raise asyncio.TimeoutError
        http = account.http
        domain = SANDBOX_DOMAIN if http.is_sandbox else API_DOMAIN
        return await asyncio.wait_for(
            qq_request(http, method, domain + path, payload, expected, timeout), timeout
        )

    async def upload(self, event, data):
        """上传一张图片并占用一次引用，返回 (image_id, url)；用完必须调用 release。"""
        account = self._account(event, sweep=True)
        await self._maintain(account, FOREGROUND_BUDGET, scan=True)
        md5 = hashlib.md5(data).hexdigest()
        ttl = self.cache_minutes * 60
        cached = account.cache.get(md5) if ttl else None
        if cached is not None and cached.expires <= time.monotonic():
            self._uncache(account, md5)
            cached = None
        if cached is not None:
            self._hold(account, cached.image_id)
            return cached.image_id, cached.url
        try:
            image_id, url = await self._transfer(account, data, md5)
        except MaterialUnavailable as exc:
            # 额度已满时先释放本插件闲置的缓存，再重试一次。
            if not exc.full or not self._evict(account):
                raise
            await self._maintain(account, FOREGROUND_BUDGET, scan=False)
            image_id, url = await self._transfer(account, data, md5)
        self._hold(account, image_id)
        if ttl:
            self._uncache(account, md5)
            self._hold(account, image_id)
            account.cache[md5] = CachedImage(image_id, url, time.monotonic() + ttl)
        return image_id, url

    async def _transfer(self, account, data, md5):
        jpeg = data.startswith(b"\xff\xd8\xff")
        content_type, suffix = ("image/jpeg", ".jpg") if jpeg else ("image/png", ".png")
        body = {
            "file_name": f"{NAME_PREFIX}{md5[:16]}{suffix}",
            "file_size": len(data),
            "content_type": content_type,
            "md5": md5,
            "sha1": hashlib.sha1(data).hexdigest(),
        }
        for attempt in range(self.retries + 1):
            try:
                prepared = await self._call(
                    account, "POST", "/v2/media-images/prepare", body, "presigned_url"
                )
                etag = await self._put(prepared["presigned_url"], data, content_type)
                if etag == md5:
                    done = await self._call(
                        account, "POST", "/v2/media-images/complete",
                        {"upload_id": prepared.get("upload_id", ""), "md5": etag}, "image_id",
                    )
                    if done.get("url"):
                        return done["image_id"], done["url"]
                    raise RuntimeError("QQ 素材库没有返回图片引用地址")
                logger.warning("QQ 素材上传校验不一致，重新申请上传")
            except QQSendError as exc:
                if exc.status == 429:
                    raise MaterialUnavailable("QQ 素材库接口触发频率限制，请稍后重试。") from None
                if exc.code in UNAVAILABLE:
                    raise MaterialUnavailable(UNAVAILABLE[exc.code], full=exc.code == FULL) from None
                if exc.code not in RETRYABLE and exc.status < 500 or attempt >= self.retries:
                    raise
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt >= self.retries:
                    raise RuntimeError("QQ 素材上传连接失败或超时") from None
            if attempt < self.retries:
                await asyncio.sleep(RETRY_DELAY)
        raise RuntimeError("QQ 素材上传失败")

    async def _put(self, url, data, content_type):
        """上传到预签名地址，不携带机器人鉴权头，返回去掉引号的 ETag。"""
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout), trust_env=True
            )
        # 预签名地址已经编码过，原样使用以免签名失效。
        async with self._session.put(
            URL(url, encoded=True), data=data,
            headers={"Content-Type": content_type}, allow_redirects=False,
        ) as response:
            await response.read()
            if response.status == 429 or response.status >= 500:
                raise aiohttp.ClientError(f"HTTP {response.status}")
            if response.status != 200:
                raise RuntimeError(f"QQ 素材上传被拒绝（HTTP {response.status}）")
            return response.headers.get("ETag", "").strip('"').lower()

    def _existing(self, event):
        # 回收只针对已上传过的账号；插件停用后账号已清空，残留图片由下次启动的扫描回收。
        return self.accounts.get(str(event.bot.api._http._token.app_id))

    def forget(self, event, image_id):
        """发送时引用已失效：作废指向该图片的缓存，下次重新上传。"""
        account = self._existing(event)
        if account is None:
            return
        for digest, cached in list(account.cache.items()):
            if cached.image_id == image_id:
                self._uncache(account, digest)

    async def release(self, event, image_id):
        account = self._existing(event)
        if account is None:
            return
        # 先在第一次 await 前登记待删除，取消或超时时也不会漏删。
        self._release(account, image_id)
        await self._maintain(account, FOREGROUND_BUDGET, scan=False)

    async def _maintain(self, account, budget, *, scan):
        # 到期缓存先转入删除队列；这一步不涉及网络，无需等锁。
        self._evict(account, expired_only=True)
        # 同账号只跑一轮清理，并发请求无需排队等待。
        if account.lock.locked():
            return
        async with account.lock:
            deadline = time.monotonic() + budget
            await self._delete_pending(account, deadline)
            if self.closed:
                return
            if scan and time.monotonic() >= account.next_sweep:
                await self._sweep(account, deadline)
                await self._delete_pending(account, deadline)

    async def _delete(self, account, image_id, deadline=None):
        await self._call(
            account, "DELETE", f"/v2/media-images/{quote(image_id, safe='')}/delete",
            deadline=deadline,
        )

    async def _delete_pending(self, account, deadline):
        # 按到期时间排序，避免一直失败的旧条目占满每轮预算。
        for image_id, pending in sorted(account.pending.items(), key=lambda item: item[1].due):
            now = time.monotonic()
            if now >= deadline:
                break
            if image_id in account.active or pending.due > now:
                continue
            pending.attempts += 1
            pending.due = now + min(30 * 2 ** min(pending.attempts - 1, 4), 300)
            try:
                await self._delete(account, image_id, deadline)
            except QQSendError as exc:
                if exc.code == MISSING:
                    account.pending.pop(image_id, None)
                else:
                    logger.warning("QQ 素材删除失败：错误码 %s，HTTP %s，稍后重试",
                                   exc.code, exc.status)
            except Exception as exc:
                logger.warning("QQ 素材删除失败（%s），稍后重试", type(exc).__name__)
            else:
                account.pending.pop(image_id, None)

    async def _list(self, account, deadline=None):
        images, total, cursor = [], None, ""
        for _ in range(10):
            path = "/v2/media-images" + (f"?cursor={quote(cursor, safe='')}" if cursor else "")
            page = await self._call(account, "GET", path, deadline=deadline)
            if total is None and isinstance(page.get("total"), int):
                total = page["total"]
            images += [item for item in page.get("images") or [] if isinstance(item, dict)]
            cursor = page.get("next_cursor") or ""
            if not cursor:
                break
        return images, len(images) if total is None else total

    def _stale(self, account, item):
        return (str(item.get("file_name", "")).startswith(NAME_PREFIX)
                and isinstance(item.get("created_at"), int)
                and item["created_at"] < time.time() - SWEEP_AGE
                and bool(item.get("image_id"))
                and str(item["image_id"]) not in account.active)

    async def _sweep(self, account, deadline):
        """发现中断遗留的本插件旧图，跳过新图、其他插件及仍在发送的图片。"""
        # 失败或预算耗尽时稍后再试，不能当成已经扫描成功。
        account.next_sweep = time.monotonic() + MAINTENANCE_INTERVAL
        try:
            images, _ = await self._list(account, deadline)
        except Exception as exc:
            logger.warning("QQ 素材库列表读取失败（%s），稍后重试", type(exc).__name__)
            return
        for item in images:
            if self._stale(account, item):
                account.pending.setdefault(str(item["image_id"]), PendingDelete())
        account.next_sweep = time.monotonic() + SWEEP_INTERVAL

    async def usage(self, event):
        """统计机器人素材库占用；本插件的图片按文件名前缀区分。"""
        account = self._account(event)
        images, total = await self._list(account, time.monotonic() + ADMIN_BUDGET)
        cached = {item.image_id for item in account.cache.values()}
        ours = [item for item in images if str(item.get("file_name", "")).startswith(NAME_PREFIX)]
        return {
            "total": total,
            "ours": len(ours),
            "cached": sum(str(item.get("image_id", "")) in cached for item in ours),
            "stale": sum(self._stale(account, item) for item in ours),
            "others": max(total - len(ours), 0),
        }

    async def purge(self, event, everything=False):
        """管理员清理：默认只删本插件素材，everything 时清空整个素材库，跳过发送中的图片。"""
        account = self._account(event)
        deadline = time.monotonic() + ADMIN_BUDGET
        images, _ = await self._list(account, deadline)
        images = [item for item in images if item.get("image_id") and (
            everything or str(item.get("file_name", "")).startswith(NAME_PREFIX))]
        result = {"deleted": 0, "failed": 0, "skipped": 0, "remaining": len(images)}
        # 等锁、分页和整批删除共用预算，不能每张图重新获得完整超时。
        try:
            await asyncio.wait_for(account.lock.acquire(), max(0, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            return result
        try:
            self._evict(account)
            for item in images:
                if time.monotonic() >= deadline:
                    break
                image_id = str(item["image_id"])
                result["remaining"] -= 1
                if image_id in account.active:
                    result["skipped"] += 1
                    continue
                try:
                    await self._delete(account, image_id, deadline)
                except QQSendError as exc:
                    if exc.code != MISSING:
                        result["failed"] += 1
                        continue
                except Exception:
                    result["failed"] += 1
                    continue
                account.pending.pop(image_id, None)
                result["deleted"] += 1
        finally:
            account.lock.release()
        return result


def usage_text(usage, active, cache_minutes):
    cleanup = "插件会自动清理" if active else "当前未选用 QQ 素材库，插件不会自动清理"
    cache = f"{cache_minutes} 分钟" if cache_minutes else "即用即删"
    return (
        f"QQ 素材库占用：{usage['total']}/100\n"
        f"本插件：{usage['ours']} 张，其中缓存中的图片 {usage['cached']} 张（缓存时间：{cache}）\n"
        f"超过 10 分钟的残留：{usage['stale']} 张，{cleanup}\n"
        f"其他插件或手动上传：{usage['others']} 张\n"
        "可发送「运势素材库 删除缓存」或「运势素材库 删除全部」清理。"
    )


def purge_text(result, everything):
    scope = "整个素材库（含其他插件的素材）" if everything else "本插件的素材"
    text = f"已清理{scope}：删除 {result['deleted']} 张"
    if result["skipped"]:
        text += f"，跳过正在发送的 {result['skipped']} 张"
    if result["failed"]:
        text += f"，{result['failed']} 张删除失败，请稍后重试"
    if result.get("remaining"):
        text += f"，操作时限已到，剩余 {result['remaining']} 张尚未处理，请再次发送清理指令"
    return text + "。"
