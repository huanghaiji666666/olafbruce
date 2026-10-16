"""生成一次回复：群聊记录 → 检索相似场景 + 牌效计算 → Claude → 解析成若干条消息。
QQ 机器人和本地 `piao chat` 共用这一层。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime

from ..chatlog.models import Message
from ..llm import ChatModel
from ..mahjong import analyze_hand, find_hand_in_text
from ..persona.prompt import ReplyLine, build_user_turn, format_live, parse_reply
from ..persona.retriever import PairRetriever

log = logging.getLogger(__name__)


class Responder:
    def __init__(
        self,
        llm: ChatModel,
        system_prompt: str,
        persona_name: str,
        persona_id: str,
        retriever: PairRetriever | None = None,
        n_examples: int = 6,
    ):
        self.llm = llm
        self.system_prompt = system_prompt
        self.persona_name = persona_name
        self.persona_id = persona_id
        self.retriever = retriever
        self.n_examples = n_examples

    def mahjong_hint(self, history: Sequence[Message], self_id: str) -> str | None:
        """最近几条别人的消息里如果有 13/14 张的手牌记号，就算一下牌效。"""
        for m in reversed(history[-6:]):
            if m.sender_id == self_id:
                continue
            hand = find_hand_in_text(m.text)
            if hand:
                try:
                    return analyze_hand(hand)
                except ValueError:
                    return None
        return None

    def build_prompt(self, history: Sequence[Message], self_id: str, trigger: str, now: datetime) -> str:
        examples = []
        if self.retriever is not None and len(self.retriever):
            others = [m.text for m in history if m.sender_id != self_id][-3:]
            examples = self.retriever.search(" ".join(others), k=self.n_examples)
        return build_user_turn(
            transcript=format_live(history, self_id, self.persona_name),
            now=now,
            trigger=trigger,
            examples=examples,
            persona_id=self.persona_id,
            persona_name=self.persona_name,
            mahjong=self.mahjong_hint(history, self_id),
        )

    async def respond(
        self, history: Sequence[Message], self_id: str, trigger: str, now: datetime
    ) -> list[ReplyLine]:
        user = self.build_prompt(history, self_id, trigger, now)
        log.debug("prompt:\n%s", user)
        raw = await self.llm.complete(self.system_prompt, user)
        log.info("模型输出: %r", raw)
        return parse_reply(raw, self.persona_name)
