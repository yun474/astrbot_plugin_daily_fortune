"""Resolve the first user mention after an original-image command."""
import re

from .avatar import field


MENTION = re.compile(
    r'''<qqbot-at-user\b[^>]*\bid\s*=\s*["']([^"'\s<>]+)["'][^>]*>|<@!?([^\s<>]+)>'''
)


def target_user(event, command):
    message = event.message_obj
    raw = field(message, "raw_message")
    ignored = {"qq_official", "all", str(field(message, "self_id", ""))}
    for mention in field(raw, "mentions", []) or []:
        if field(mention, "is_you", False):
            ignored.add(str(field(mention, "id", "")))

    # Official adapters can leave user mentions as markup inside Plain text.
    texts = [field(raw, "content", ""), field(message, "message_str", "")]
    parts = []
    for component in field(message, "message", []) or []:
        if field(component, "type") == "Plain":
            parts.append(str(field(component, "text", "")))
        elif field(component, "type") == "At":
            parts.append(f'<@{field(component, "qq", "")}>')
    texts.append(" ".join(parts))
    for text in texts:
        match = re.search(rf"(?:^|[\s/]){re.escape(command)}(?=\s|<|$)", text or "")
        if not match:
            continue
        for mention in MENTION.finditer(text[match.end():]):
            user = mention.group(1) or mention.group(2)
            if user not in ignored:
                return user
    return str(event.get_sender_id())
