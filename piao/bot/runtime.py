"""QQ 群里的飘：收消息 → 判断要不要开口 → 等对方说完 → 生成 → 像人一样分条发出去。"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections import defaultdict, deque
from datetime import datetime
from typing import Any

from ..chatlog.clean import normalize_text
from ..chatlog.models import Message
from ..chatlog.parsers import CHINA_TZ, SEGMENT_PLACEHOLDER, now_china
from ..config import Config
from ..persona.prompt import ReplyLine
from .onebot import ActionError, OneBotClient
from .policy import AT, ReplyPolicy, Trigger
from .responder import Responder

log = logging.getLogger(__name__)

_CQ_AT = re.compile(r"\[CQ:at,qq=(\d+|all)[^\]]*\]")
_CQ_REPLY = re.compile(r"\[CQ:reply,id=(-?\d+)[^\]]*\]")
_LEADING_AT = re.compile(r"^@(\S+?)\s+(.*)$")


class PiaoBot:
    def __init__(self, cfg: Config, client: OneBotClient, responder: Responder, tts: Any = None):
        self.cfg = cfg
        self.client = client
        self.responder = responder
        self.tts = tts
        self.policy = ReplyPolicy(cfg.bot, cfg.persona.aliases)
        self.self_id = ""
        self.history: dict[int, deque[Message]] = defaultdict(lambda: deque(maxlen=cfg.bot.context_messages))
        self.names: dict[int, dict[str, str]] = defaultdict(dict)
        self.my_message_ids: deque[str] = deque(maxlen=500)
        self.locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.pending: dict[int, tuple[Trigger, str | None]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------ 收消息

    def render(self, group_id: int, message: Any) -> tuple[str, bool, str | None]:
        """消息 → (给模型看的文本, 是否 @ 了我, 回复的消息 id)。"""
        at_me, reply_id = False, None
        if isinstance(message, str):  # CQ 码字符串格式
            for qq in _CQ_AT.findall(message):
                at_me |= qq == self.self_id
            m = _CQ_REPLY.search(message)
            reply_id = m.group(1) if m else None
            text = _CQ_AT.sub(lambda m: "@" + self._name(group_id, m.group(1)) + " ", message)
            return normalize_text(text), at_me, reply_id
        parts: list[str] = []
        for seg in message:
            typ, data = seg.get("type"), seg.get("data") or {}
            if typ == "text":
                parts.append(data.get("text", ""))
            elif typ == "at":
                qq = str(data.get("qq", ""))
                at_me |= qq == self.self_id
                parts.append("@" + self._name(group_id, qq, data.get("name")) + " ")
            elif typ == "reply":
                reply_id = str(data.get("id"))
            elif typ == "mface" and data.get("summary"):
                parts.append(str(data["summary"]))
            else:
                parts.append(SEGMENT_PLACEHOLDER.get(typ, ""))
        return normalize_text("".join(parts)), at_me, reply_id

    def _name(self, group_id: int, qq: str, fallback: str | None = None) -> str:
        if qq == "all":
            return "全体成员"
        if qq == self.self_id:
            return self.cfg.persona.name
        return self.names[group_id].get(qq) or fallback or qq

    async def on_event(self, event: dict[str, Any]) -> None:
        if "self_id" in event:
            self.self_id = str(event["self_id"])
        if event.get("post_type") == "message" and event.get("message_type") == "group":
            await self.on_group_message(event)

    async def on_group_message(self, event: dict[str, Any]) -> None:
        gid, uid = int(event["group_id"]), str(event["user_id"])
        if uid == self.self_id or gid not in self.cfg.bot.groups:
            return
        sender = event.get("sender") or {}
        name = sender.get("card") or sender.get("nickname") or uid
        self.names[gid][uid] = name
        text, at_me, reply_id = self.render(gid, event.get("message") or event.get("raw_message", ""))
        if not text:
            return
        if text.startswith("/飘") and int(uid) in self.cfg.bot.admins:
            await self.admin_command(gid, text)
            return
        reply_to_me = reply_id is not None and reply_id in self.my_message_ids
        label = name + ("（@你）" if at_me else "（回复你）" if reply_to_me else "")
        ts = event.get("time")
        when = datetime.fromtimestamp(ts, CHINA_TZ).replace(tzinfo=None) if ts else now_china()
        self.history[gid].append(Message(when, uid, label, text, str(gid)))

        trigger = self.policy.decide(gid, int(uid), name, text, at_me, reply_to_me)
        if trigger is not None:
            quote = str(event.get("message_id")) if trigger.priority >= AT and self.cfg.bot.quote_trigger else None
            self.schedule(gid, trigger, quote)

    async def admin_command(self, gid: int, text: str) -> None:
        arg = text.removeprefix("/飘").strip()
        if arg == "off":
            self.policy.muted.add(gid)
            reply = "好的，先潜水了（/飘 on 恢复）"
        elif arg == "on":
            self.policy.muted.discard(gid)
            reply = "回来了"
        else:
            state = "静音中" if gid in self.policy.muted else "运行中"
            reply = f"[{self.cfg.persona.name} AI 分身] {state}，本群最近 {len(self.history[gid])} 条上下文"
        await self.client.call("send_group_msg", group_id=gid, message=[{"type": "text", "data": {"text": reply}}])

    # ------------------------------------------------------------ 回消息

    def schedule(self, gid: int, trigger: Trigger, quote: str | None) -> None:
        current = self.pending.get(gid)
        if current is not None:
            if trigger.priority > current[0].priority:
                self.pending[gid] = (trigger, quote)
            return
        self.pending[gid] = (trigger, quote)
        task = asyncio.create_task(self._respond(gid))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _respond(self, gid: int) -> None:
        # 等一会儿：对方经常连发好几条，收齐了再回
        await asyncio.sleep(self.cfg.bot.debounce_seconds)
        async with self.locks[gid]:
            trigger, quote = self.pending.pop(gid)
            try:
                lines = await self.responder.respond(list(self.history[gid]), self.self_id, trigger.reason, now_china())
                if not lines:
                    return
                self.policy.record_reply(gid)
                for i, line in enumerate(lines):
                    await asyncio.sleep(self.typing_delay(line.text, first=i == 0))
                    await self.send_line(gid, line, quote if i == 0 else None, single=len(lines) == 1)
            except Exception:
                log.exception("生成或发送回复时出错")

    def typing_delay(self, text: str, first: bool) -> float:
        base = len(text) / max(self.cfg.bot.typing_cps, 0.1)
        return min(base + random.uniform(0.3, 1.2) + (0.5 if first else 0), 8.0)

    def build_segments(self, gid: int, text: str, quote: str | None) -> list[dict[str, Any]]:
        segs: list[dict[str, Any]] = []
        if quote:
            segs.append({"type": "reply", "data": {"id": quote}})
        m = _LEADING_AT.match(text)
        if m:
            target = next((qq for qq, n in self.names[gid].items() if n == m.group(1)), None)
            if target:
                segs.append({"type": "at", "data": {"qq": target}})
                text = " " + m.group(2)
        segs.append({"type": "text", "data": {"text": text}})
        return segs

    async def send_line(self, gid: int, line: ReplyLine, quote: str | None, single: bool) -> None:
        vc = self.cfg.voice
        want_voice = line.voice or (single and random.random() < vc.probability)
        message: list[dict[str, Any]] | None = None
        if self.tts is not None and want_voice and len(line.text) <= vc.max_chars:
            try:
                message = [await self.tts.record_segment(line.text)]
            except Exception as e:  # 语音失败就发文字
                log.warning("语音合成失败，改发文字: %s", e)
        if message is None:
            message = self.build_segments(gid, line.text, quote)
        try:
            data = await self.client.call("send_group_msg", group_id=gid, message=message)
        except ActionError as e:
            log.error("发送失败: %s", e)
            return
        if data.get("message_id") is not None:
            self.my_message_ids.append(str(data["message_id"]))
        self.history[gid].append(Message(now_china(), self.self_id, self.cfg.persona.name, line.text, str(gid)))
        log.info("[群%s] %s%s", gid, "(语音) " if message[0]["type"] == "record" else "", line.text)
