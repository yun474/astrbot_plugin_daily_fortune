from types import SimpleNamespace as NS

import pytest

from daily_fortune_test.targets import target_user


@pytest.mark.parametrize("tag", [
    '<qqbot-at-user id="TARGET" />', "<qqbot-at-user id='TARGET'/>",
    "<@TARGET>", "<@!TARGET>",
])
@pytest.mark.parametrize("as_object", [False, True])
def test_official_mentions_after_command(tag, as_object):
    raw = {"content": f'<@BEFORE> <@BOT> /运势原图 <@BOT> {tag}',
           "mentions": [{"id": "BOT", "is_you": True}]}
    if as_object:
        raw["mentions"] = [NS(**x) for x in raw["mentions"]]
        raw = NS(**raw)
    event = NS(message_obj=NS(raw_message=raw, self_id="BOT"), get_sender_id=lambda: "SELF")
    assert target_user(event, "运势原图") == "TARGET"


def test_component_mentions_skip_wakeup_and_reply():
    message = NS(raw_message=None, self_id="BOT", message=[
        NS(type="At", qq="BOT"),
        NS(type="Reply", chain=[NS(type="At", qq="QUOTED")]),
        NS(type="Plain", text="/老婆原图"),
        NS(type="At", qq="123456"), NS(type="At", qq="SECOND"),
    ])
    event = NS(message_obj=message, get_sender_id=lambda: "SELF")
    assert target_user(event, "老婆原图") == "123456"


@pytest.mark.parametrize("content", ["/老婆原图", "<@BOT> /老婆原图", "/老婆原图 <@BOT>"])
def test_no_target_defaults_to_sender(content):
    event = NS(message_obj=NS(raw_message={"content": content}, self_id="BOT"),
               get_sender_id=lambda: "SELF")
    assert target_user(event, "老婆原图") == "SELF"


def test_custom_wake_prefix_uses_framework_normalized_text():
    event = NS(message_str='老婆原图 <qqbot-at-user id="OTHER" />',
               message_obj=NS(raw_message={'content': '云云老婆原图 <qqbot-at-user id="OTHER" />'}),
               get_sender_id=lambda: 'SELF')
    assert target_user(event, '老婆原图') == 'OTHER'
