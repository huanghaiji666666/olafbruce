import asyncio
from types import SimpleNamespace

import pytest

from piao.bot.policy import ALIAS, AT, RANDOM, ReplyPolicy
from piao.bot.responder import Responder
from piao.bot.runtime import PiaoBot
from piao.config import BotConfig, Config, LLMConfig
from piao.llm import FALLBACK_BETA, ClaudeChat
from piao.persona.prompt import ReplyLine


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make_policy(rng_value=0.0, **kw):
    cfg = BotConfig(groups=[100], ignore_users=[999], **kw)
    clock = Clock()
    return ReplyPolicy(cfg, ["飘", "飘哥"], rng=lambda: rng_value, clock=clock), clock


def test_policy_priorities():
    p, _ = make_policy(rng_value=0.99)
    assert p.decide(100, 1, "张三", "在吗", at_me=True, reply_to_me=False).priority == AT
    assert p.decide(100, 1, "张三", "在吗", at_me=False, reply_to_me=True).priority == AT
    assert p.decide(100, 1, "张三", "飘哥在吗", False, False) is None  # 0.99 > alias 概率
    p2, _ = make_policy(rng_value=0.0)
    assert p2.decide(100, 1, "张三", "飘哥在吗", False, False).priority == ALIAS
    assert p2.decide(100, 1, "张三", "随便聊聊", False, False).priority == RANDOM


def test_policy_filters():
    p, _ = make_policy(rng_value=0.0)
    assert p.decide(200, 1, "张三", "飘", True, False) is None  # 不在白名单的群
    assert p.decide(100, 999, "bot", "飘", True, False) is None  # 忽略的人
    p.muted.add(100)
    assert p.decide(100, 1, "张三", "飘", True, False) is None


def test_policy_cooldown_and_rate_limit():
    p, clock = make_policy(rng_value=0.0, cooldown_seconds=30, max_replies_per_minute=2)
    p.record_reply(100)
    assert p.decide(100, 1, "张三", "随便聊聊", False, False) is None  # 冷却中
    clock.t += 31
    assert p.decide(100, 1, "张三", "随便聊聊", False, False) is not None
    p.record_reply(100)
    assert p.decide(100, 1, "张三", "@", True, False) is None  # 一分钟内已回 2 次
    clock.t += 61
    assert p.decide(100, 1, "张三", "@", True, False) is not None


class FakeClient:
    def __init__(self):
        self.sent = []

    async def call(self, action, **params):
        self.sent.append((action, params))
        return {"message_id": 555 + len(self.sent)}


class FakeResponder:
    def __init__(self, lines):
        self.lines = lines
        self.calls = []

    async def respond(self, history, self_id, trigger, now):
        self.calls.append((list(history), trigger))
        return self.lines


def make_bot(lines, **bot_kw):
    cfg = Config(bot=BotConfig(groups=[100], debounce_seconds=0, admins=[1], **bot_kw))
    client, responder = FakeClient(), FakeResponder(lines)
    bot = PiaoBot(cfg, client, responder)
    bot.self_id = "42"
    bot.typing_delay = lambda text, first: 0
    return bot, client, responder


def group_event(text_segments, user_id=7, name="张三", message_id=9):
    return {
        "post_type": "message",
        "message_type": "group",
        "self_id": 42,
        "group_id": 100,
        "user_id": user_id,
        "message_id": message_id,
        "time": 1760620000,
        "sender": {"card": name, "nickname": "nick"},
        "message": text_segments,
    }


async def drain(bot):
    while bot._tasks:
        await asyncio.gather(*list(bot._tasks))


def test_render_segments():
    bot, _, _ = make_bot([])
    bot.names[100]["7"] = "张三"
    text, at_me, reply = bot.render(
        100,
        [
            {"type": "reply", "data": {"id": "77"}},
            {"type": "at", "data": {"qq": "42"}},
            {"type": "text", "data": {"text": " 23466m345p11s5678p 切啥"}},
            {"type": "image", "data": {}},
            {"type": "at", "data": {"qq": "7"}},
        ],
    )
    assert at_me and reply == "77"
    assert text == "@轻轻的飘  23466m345p11s5678p 切啥[图片]@张三"
    text, at_me, reply = bot.render(100, "[CQ:reply,id=5][CQ:at,qq=42] 在吗")
    assert at_me and reply == "5" and text == "@轻轻的飘  在吗"


def test_at_triggers_reply_with_quote_and_at_mapping():
    async def run():
        bot, client, responder = make_bot([ReplyLine("@张三 切5p"), ReplyLine("没什么好说的")], self_disclose=True)
        await bot.on_event(group_event([{"type": "at", "data": {"qq": "42"}}, {"type": "text", "data": {"text": "切啥"}}]))
        await drain(bot)
        return bot, client, responder

    bot, client, responder = asyncio.run(run())
    history, trigger = responder.calls[0]
    assert history[-1].sender_name == "张三（@你）" and "@了你" in trigger
    (a1, p1), (a2, p2) = client.sent
    assert a1 == "send_group_msg" and p1["group_id"] == 100
    assert p1["message"][0] == {"type": "reply", "data": {"id": "9"}}
    assert p1["message"][1] == {"type": "at", "data": {"qq": "7"}}
    assert p1["message"][2]["data"]["text"] == " 切5p"
    assert p2["message"] == [{"type": "text", "data": {"text": "没什么好说的"}}]
    assert list(bot.my_message_ids) == ["556", "557"]
    assert [m.text for m in bot.history[100]][-2:] == ["@张三 切5p", "没什么好说的"]


def test_reply_to_own_message_triggers():
    async def run():
        bot, client, responder = make_bot([ReplyLine("嗯")])
        bot.my_message_ids.append("556")
        await bot.on_event(group_event([{"type": "reply", "data": {"id": "556"}}, {"type": "text", "data": {"text": "真的吗"}}]))
        await drain(bot)
        return responder

    responder = asyncio.run(run())
    assert "回复了你" in responder.calls[0][1]


def test_ignores_other_groups_and_self():
    async def run():
        bot, client, responder = make_bot([ReplyLine("嗯")])
        ev = group_event([{"type": "at", "data": {"qq": "42"}}])
        ev["group_id"] = 200
        await bot.on_event(ev)
        await bot.on_event(group_event([{"type": "text", "data": {"text": "我自己"}}], user_id=42))
        await drain(bot)
        return bot, client

    bot, client = asyncio.run(run())
    assert client.sent == [] and len(bot.history[100]) == 0


def test_admin_mute():
    async def run():
        bot, client, responder = make_bot([ReplyLine("嗯")])
        await bot.on_event(group_event([{"type": "text", "data": {"text": "/飘 off"}}], user_id=1))
        await bot.on_event(group_event([{"type": "at", "data": {"qq": "42"}}]))
        await drain(bot)
        return bot, client, responder

    bot, client, responder = asyncio.run(run())
    assert 100 in bot.policy.muted
    assert len(client.sent) == 1 and responder.calls == []


def test_burst_is_debounced_into_one_reply():
    async def run():
        bot, client, responder = make_bot([ReplyLine("在")])
        bot.cfg.bot.debounce_seconds = 0.05
        await bot.on_event(group_event([{"type": "at", "data": {"qq": "42"}}, {"type": "text", "data": {"text": "飘"}}]))
        await bot.on_event(group_event([{"type": "at", "data": {"qq": "42"}}, {"type": "text", "data": {"text": "在吗"}}], message_id=10))
        await drain(bot)
        return responder

    responder = asyncio.run(run())
    assert len(responder.calls) == 1 and len(responder.calls[0][0]) == 2


def test_responder_adds_mahjong_hint():
    class LLM:
        async def complete(self, system, user):
            self.user = user
            return "切5p"

    llm = LLM()
    from datetime import datetime

    from piao.chatlog.models import Message

    r = Responder(llm, "sys", "轻轻的飘", "2044715303")
    history = [Message(datetime(2026, 10, 16, 21), "7", "张三", "23466m345p11s5678p 何切")]
    lines = asyncio.run(r.respond(history, "42", "张三 @了你。", datetime(2026, 10, 16, 21)))
    assert lines[0].text == "切5p"
    assert "<牌效计算>" in llm.user and "切5p：听牌" in llm.user


# ------------------------------------------------------------ Claude 调用


class FakeMessages:
    def __init__(self, resp):
        self.resp = resp
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.resp


def fake_anthropic(stop_reason="end_turn", text="确实"):
    resp = SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category="cyber") if stop_reason == "refusal" else None,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=1),
    )
    msgs = FakeMessages(resp)
    return SimpleNamespace(messages=msgs, beta=SimpleNamespace(messages=msgs)), msgs


def test_claude_request_shape():
    client, msgs = fake_anthropic()
    chat = ClaudeChat(LLMConfig(), client=client)
    assert asyncio.run(chat.complete("SYSTEM", "USER")) == "确实"
    kw = msgs.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["betas"] == [FALLBACK_BETA] and kw["fallbacks"] == "default"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["output_config"] == {"effort": "low"}
    assert kw["messages"] == [{"role": "user", "content": "USER"}]
    assert "thinking" not in kw and "temperature" not in kw


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_claude_bad_stops_are_silent(stop):
    client, _ = fake_anthropic(stop_reason=stop)
    assert asyncio.run(ClaudeChat(LLMConfig(), client=client).complete("s", "u")) == ""


def test_haiku_has_no_fallbacks():
    client, msgs = fake_anthropic()
    asyncio.run(ClaudeChat(LLMConfig(model="claude-haiku-5-5"), client=client).complete("s", "u"))
    assert "fallbacks" not in msgs.kwargs
