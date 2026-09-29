import logging

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .avatar import avatar_url
from .fortune import draw, text_result, today
from .service import FortuneService
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
        if event.get_platform_name() in {"qq_official", "qq_official_webhook"}:
            try:
                if await send_markdown(event, item):
                    return
            except Exception:
                logger.exception("今日老婆 Markdown 发送失败")
                yield event.plain_result("今日老婆图片消息发送失败，请稍后重试；当天角色已保留。")
                return
        yield event.plain_result(f"您的今日老婆是：{item['name']}\n作品：{item['work']}")
        yield event.image_result(item["url"])

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
        await self.service.close()
