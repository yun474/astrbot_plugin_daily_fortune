import logging

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .avatar import avatar_url
from .fortune import draw, text_result, today
from .service import FortuneService

logger = logging.getLogger("astrbot")


@register(
    "astrbot_plugin_daily_fortune", "yun474", "头像与二次元每日运势卡片", "0.1.0",
    "https://github.com/yun474/astrbot_plugin_daily_fortune",
)
class DailyFortune(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.service = FortuneService(
            config, StarTools.get_data_dir("astrbot_plugin_daily_fortune")
        )

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
