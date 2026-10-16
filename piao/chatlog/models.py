from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Message:
    time: datetime
    sender_id: str
    sender_name: str
    text: str
    group: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["time"] = self.time.isoformat(sep=" ", timespec="seconds")
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Message:
        return cls(
            time=datetime.fromisoformat(d["time"]),
            sender_id=str(d["sender_id"]),
            sender_name=d.get("sender_name", ""),
            text=d.get("text", ""),
            group=d.get("group", ""),
        )


@dataclass
class Turn:
    """同一个人在短时间内连发的几条消息，算作一“轮”。"""

    sender_id: str
    sender_name: str
    time: datetime
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "time": self.time.isoformat(sep=" ", timespec="seconds"),
            "lines": self.lines,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Turn:
        return cls(
            sender_id=str(d["sender_id"]),
            sender_name=d.get("sender_name", ""),
            time=datetime.fromisoformat(d["time"]),
            lines=list(d["lines"]),
        )


@dataclass
class Pair:
    """一段群聊上下文，以及飘紧接着的发言。context 可能为空（飘主动开话题）。"""

    context: list[Turn]
    reply: Turn
    group: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "context": [t.to_dict() for t in self.context],
            "reply": self.reply.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Pair:
        return cls(
            context=[Turn.from_dict(t) for t in d["context"]],
            reply=Turn.from_dict(d["reply"]),
            group=d.get("group", ""),
        )
