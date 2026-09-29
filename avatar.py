"""QQ adapter field access, without nickname or profile requests."""

from urllib.parse import quote


def field(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def avatar_url(event) -> str | None:
    platform = event.get_platform_name()
    if platform in {"qq_official", "qq_official_webhook"}:
        author = field(event.message_obj.raw_message, "author")
        # Channel messages have their own avatar URL, not a group/friend OpenID.
        avatar = field(author, "avatar")
        if avatar:
            return str(avatar)
        openid = field(author, "member_openid") or field(author, "user_openid")
        adapter = field(field(event, "bot"), "platform")
        appid = field(adapter, "appid")
        if appid and openid:
            return f"https://q.qlogo.cn/qqapp/{quote(str(appid), safe='')}/{quote(str(openid), safe='')}/640"
        return None
    if platform == "aiocqhttp":
        uid = str(event.get_sender_id())
        if uid.isdecimal():
            return f"https://q1.qlogo.cn/g?b=qq&nk={uid}&s=100"
    return None
