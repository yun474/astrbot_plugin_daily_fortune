import asyncio
import logging
from contextlib import suppress

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .avatar import avatar_url
from .fortune import draw, text_result, today
from .service import FortuneService
from .targets import target_user
from .settings import number
from .wife import WifeService, send_markdown

logger = logging.getLogger("astrbot")


@register(
    "astrbot_plugin_daily_fortune", "yun474", "今日运势与二次元老婆", "0.1.0",
    "https://github.com/yun474/astrbot_plugin_daily_fortune",
)
class DailyFortune(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.service = FortuneService(
            config, StarTools.get_data_dir("astrbot_plugin_daily_fortune")
        )
        self.wife = WifeService(self.service)
        self.qq_wife_markdown = bool(config.get("qq_wife_markdown", True))
        self.cleanup_interval = number(config, "cache_cleanup_interval_hours", 6, 1, 168) * 3600
        self._cleanup_task = None

    async def initialize(self):
        await self._cleanup_once()
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_once(self):
        for service in (self.service, self.wife):
            try:
                await service.cleanup(today())
            except Exception:
                logger.exception("%s 缓存清理失败，下个周期重试", type(service).__name__)

    async def _cleanup_loop(self):
        while True:
            await asyncio.sleep(self.cleanup_interval)
            await self._cleanup_once()

    @filter.command("今日老婆", alias={"jrlp", "抽老婆"})
    async def daily_wife(self, event: AstrMessageEvent):
        """抽取当天的二次元角色，QQ 官方群聊和私聊使用原生 Markdown。"""
        try:
            uid = f"{event.get_platform_id()}:{event.get_sender_id()}"
            item = await self.wife.select(uid, today())
        except Exception:
            logger.exception("今日老婆角色获取失败")
            yield event.plain_result("角色图库暂时无法读取，请稍后再试。")
            return
        if self.qq_wife_markdown and event.get_platform_name() in {"qq_official", "qq_official_webhook"}:
            try:
                if await send_markdown(event, item):
                    return
            except Exception:
                logger.exception("今日老婆 Markdown 发送失败")
                yield event.plain_result("今日老婆图片消息发送失败，请稍后重试；当天角色已保留。")
                return
        try:
            image = await self.wife.card(uid, item, avatar_url(event))
        except Exception:
            logger.exception("今日老婆图片生成失败，请检查图源和浏览器配置")
            yield event.plain_result(f"您的今日老婆是：{item['name']}\n作品：{item['work']}\n图片生成失败，请稍后重试。")
            return
        yield event.image_result(str(image))

    @filter.command("今日运势", alias={"jrys", "运势"})
    async def daily_fortune(self, event: AstrMessageEvent):
        """头像、每日吉凶宜忌与一言。"""
        date = today()
        uid = f"{event.get_platform_id()}:{event.get_sender_id()}"
        try:
            image = await self.service.card(uid, date, avatar_url(event))
        except Exception:
            logger.exception("今日运势图片生成失败，请检查浏览器和字体配置")
            yield event.plain_result(text_result(draw(uid, date)))
            return
        yield event.image_result(str(image))

    async def terminate(self):
        if self._cleanup_task:
            self._cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._cleanup_task
        await self.service.close()

    @filter.command("运势原图", alias=set())
    async def fortune_original(self, event: AstrMessageEvent):
        async for result in self._original(event, "运势原图", self.service):
            yield result

    @filter.command("老婆原图", alias=set())
    async def wife_original(self, event: AstrMessageEvent):
        async for result in self._original(event, "老婆原图", self.wife):
            yield result

    async def _original(self, event, command, service):
        try:
            uid = f"{event.get_platform_id()}:{target_user(event, command)}"
            image = await service.original(uid, today())
        except Exception:
            logger.exception("%s读取失败", command)
            return
        if image:
            yield event.image_result(str(image))
