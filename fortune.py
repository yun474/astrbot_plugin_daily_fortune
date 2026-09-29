"""Daily deterministic draws; no account, nickname or sign-in state."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))
FORTUNES = (
    (0, "凶", "慢一点，也可以抵达。"),
    (15, "末吉", "给好事一点发生的时间。"),
    (35, "小吉", "小小的幸运，正在路上。"),
    (60, "中吉", "顺着心意，把今天过得闪闪发亮。"),
    (85, "大吉", "今天的风，也站在你这边。"),
)
EVENTS = (
    ("散步", "去吹吹风，灵感会自己找来。", "别走太远，留点力气回家。"),
    ("追番", "喜欢的故事，值得慢慢看。", "先停一集，别忘了眼前的事。"),
    ("整理房间", "清出一点空间，也清出好心情。", "今天先别翻箱倒柜。"),
    ("主动聊天", "一句问候，可能换来小惊喜。", "不想说话，就安静待一会儿。"),
    ("尝试新事物", "迈出第一步，就已经很棒了。", "先把手边的事做好。"),
    ("熬夜", "今晚留一点时间给自己。", "早点睡，明天再续下一章。"),
    ("购物", "挑一件真正喜欢的小东西。", "购物车放一晚再决定。"),
    ("打游戏", "和朋友一起，快乐会加倍。", "连败就收手，别和自己较劲。"),
    ("听歌", "给今天配一首喜欢的背景乐。", "别把音量开得太大。"),
    ("做计划", "把想做的事写下来。", "不必把每一分钟都填满。"),
)


def today() -> str:
    return datetime.now(CST).date().isoformat()


def draw(uid: str, date: str) -> dict:
    def digest(label):
        text = json.dumps([uid, date, label], ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(text.encode()).digest()

    score = int.from_bytes(digest("fortune")[:4], "big") % 101
    _, fortune, tip = next(item for item in reversed(FORTUNES) if score >= item[0])
    indexes = sorted(range(len(EVENTS)), key=lambda i: digest(f"event:{i}"))[:4]
    return {
        "date": date,
        "fortune": fortune,
        "tip": tip,
        "good": [(EVENTS[i][0], EVENTS[i][1]) for i in indexes[:2]],
        "bad": [(EVENTS[i][0], EVENTS[i][2]) for i in indexes[2:]],
    }


def text_result(view: dict) -> str:
    lines = [f"{view['date']} 今日运势：{view['fortune']}", view["tip"]]
    for key, label in (("good", "宜"), ("bad", "忌")):
        lines.append(f"{label}：" + "；".join(f"{title} · {desc}" for title, desc in view[key]))
    lines.append("仅供娱乐")
    return "\n".join(lines)
