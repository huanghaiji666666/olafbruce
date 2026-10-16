from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from piao.chatlog.models import Message

TARGET = "2044715303"

QQ_TXT = """消息记录（此消息记录为文本格式，不支持重新导入）

================================================================
消息分组:我的群聊
================================================================
消息对象:日麻交流群
================================================================

2023-05-01 21:15:32 【群主】张三(123456)
飘哥 23466m345p11s5678p 切啥

2023-05-01 21:15:50 轻轻的飘(2044715303)
切5p
没什么好说的

2023-05-01 9:16:01 李四<lisi@example.com>
这也要问

2023-05-01 21:16:30 系统消息(10000)
张三 撤回了一条消息

2023-05-01 21:17:00 轻轻的飘(2044715303)
确实

"""


@pytest.fixture
def qq_txt(tmp_path: Path) -> Path:
    p = tmp_path / "group.txt"
    p.write_text(QQ_TXT, encoding="utf-8-sig")
    return p


def synthetic_chat(n_rounds: int = 300, seed: int = 1) -> list[Message]:
    """一个能体现口癖的假群聊：目标爱说“确实”“绷不住了”，其他人说普通的话。"""
    rng = random.Random(seed)
    others = [("10001111", "张三"), ("10002222", "李四"), ("10003333", "王五")]
    other_lines = ["今天天凤又四位了", "这牌切啥", "雀魂活动开了", "晚上打不打", "立直被一发了", "好饿", "下班了"]
    target_lines = ["确实", "绷不住了", "切8s", "确实 这个没办法", "推", "绷不住了 又放铳", "6", "早点睡"]
    t = datetime(2024, 1, 1, 20, 0, 0)
    msgs = []
    for _ in range(n_rounds):
        sid, name = rng.choice(others)
        t += timedelta(seconds=rng.randint(5, 90))
        msgs.append(Message(t, sid, name, rng.choice(other_lines), "日麻群"))
        if rng.random() < 0.5:
            t += timedelta(seconds=rng.randint(3, 30))
            msgs.append(Message(t, TARGET, "轻轻的飘", rng.choice(target_lines), "日麻群"))
        if rng.random() < 0.05:
            t += timedelta(hours=2)
    return msgs
