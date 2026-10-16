"""什么时候开口：被 @ / 被回复必回，被叫名字大概率回，平时偶尔插话；带冷却和限流。"""

from __future__ import annotations

import random
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

from ..config import BotConfig
from ..persona.prompt import PASS_TOKEN

# 触发优先级：数字越大越“必须回”
AT, ALIAS, RANDOM = 3, 2, 1


@dataclass
class Trigger:
    priority: int
    reason: str


class ReplyPolicy:
    def __init__(
        self,
        cfg: BotConfig,
        aliases: list[str],
        rng: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.cfg = cfg
        self.aliases = sorted(aliases, key=len, reverse=True)
        self.rng = rng
        self.clock = clock
        self.last_reply: dict[int, float] = {}
        self.recent: dict[int, deque[float]] = defaultdict(deque)
        self.muted: set[int] = set()

    def mentions_alias(self, text: str) -> bool:
        return any(a in text for a in self.aliases)

    def rate_limited(self, group_id: int) -> bool:
        now = self.clock()
        q = self.recent[group_id]
        while q and now - q[0] > 60:
            q.popleft()
        return len(q) >= self.cfg.max_replies_per_minute

    def decide(
        self, group_id: int, user_id: int, sender: str, text: str, at_me: bool, reply_to_me: bool
    ) -> Trigger | None:
        if group_id in self.muted or user_id in self.cfg.ignore_users:
            return None
        if group_id not in self.cfg.groups or self.rate_limited(group_id):
            return None
        if self.cfg.reply_on_at and (at_me or reply_to_me):
            how = "@了你" if at_me else "回复了你的消息"
            return Trigger(AT, f"{sender} {how}，在等你回应。")
        if self.cfg.reply_on_alias and self.mentions_alias(text) and self.rng() < self.cfg.alias_reply_prob:
            return Trigger(ALIAS, f"{sender} 的消息里提到了你（也可能只是字面上撞了名字，自己判断）。")
        last = self.last_reply.get(group_id, -1e9)
        if self.clock() - last >= self.cfg.cooldown_seconds and self.rng() < self.cfg.random_reply_prob:
            return Trigger(
                RANDOM,
                f"没人叫你，你只是在潜水时刷到了这些消息。本人大概率会插话才说，否则只输出 {PASS_TOKEN}",
            )
        return None

    def record_reply(self, group_id: int) -> None:
        now = self.clock()
        self.last_reply[group_id] = now
        self.recent[group_id].append(now)
