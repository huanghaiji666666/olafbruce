"""从清洗后的消息构造数据集：飘的发言、(上下文 → 回复) 样本、可选的微调数据。"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path

from .models import Message, Pair, Turn


def group_turns(messages: Iterable[Message], merge_gap_seconds: float = 60) -> list[Turn]:
    """把同一人间隔不超过 merge_gap_seconds 的连发消息合并为一轮（输入需为同一个群、按时间排序）。
    多行消息拆成多行，和机器人“一行一条消息”的输出约定保持一致。"""
    turns: list[Turn] = []
    gap = timedelta(seconds=merge_gap_seconds)
    last_time = None
    for m in messages:
        lines = [line for line in m.text.splitlines() if line.strip()] or [m.text]
        if turns and turns[-1].sender_id == m.sender_id and last_time is not None and m.time - last_time <= gap:
            turns[-1].lines.extend(lines)
        else:
            turns.append(Turn(m.sender_id, m.sender_name, m.time, lines))
        last_time = m.time
    return turns


def split_by_group(messages: Iterable[Message]) -> dict[str, list[Message]]:
    groups: dict[str, list[Message]] = defaultdict(list)
    for m in messages:
        groups[m.group].append(m)
    for msgs in groups.values():
        msgs.sort(key=lambda m: m.time)
    return dict(groups)


def build_pairs(
    messages: Iterable[Message],
    target_id: str,
    context_turns: int = 8,
    max_gap_minutes: float = 20,
    merge_gap_seconds: float = 60,
) -> list[Pair]:
    """对飘的每一轮发言，取它之前、时间上连续的若干轮作为上下文。"""
    max_gap = timedelta(minutes=max_gap_minutes)
    pairs: list[Pair] = []
    for group, msgs in split_by_group(messages).items():
        turns = group_turns(msgs, merge_gap_seconds)
        for i, turn in enumerate(turns):
            if turn.sender_id != target_id:
                continue
            context: list[Turn] = []
            prev_time = turn.time
            for j in range(i - 1, max(-1, i - 1 - context_turns), -1):
                if prev_time - turns[j].time > max_gap:
                    break
                context.append(turns[j])
                prev_time = turns[j].time
            context.reverse()
            pairs.append(Pair(context=context, reply=turn, group=group))
    pairs.sort(key=lambda p: p.reply.time)
    return pairs


# ---------------------------------------------------------------- 读写

def write_jsonl(path: str | Path, rows: Iterable[dict]) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def read_jsonl(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_messages_jsonl(path: str | Path) -> list[Message]:
    return [Message.from_dict(d) for d in read_jsonl(path)]


def load_pairs_jsonl(path: str | Path) -> list[Pair]:
    return [Pair.from_dict(d) for d in read_jsonl(path)]


# ---------------------------------------------------------------- 微调数据导出

def export_sft(
    pairs: Iterable[Pair],
    path: str | Path,
    persona_id: str,
    persona_name: str,
    system_prompt: str,
    fmt: str = "sharegpt",
    min_context: int = 1,
) -> int:
    """导出 SFT 数据（LLaMA-Factory 的 sharegpt 格式，或 OpenAI messages 格式），用于 LoRA 微调本地模型。"""
    from ..persona.prompt import format_turns

    rows = []
    for p in pairs:
        if len(p.context) < min_context:
            continue
        user = format_turns(p.context, persona_id, persona_name)
        reply = p.reply.text
        if fmt == "sharegpt":
            rows.append(
                {
                    "system": system_prompt,
                    "conversations": [
                        {"from": "human", "value": user},
                        {"from": "gpt", "value": reply},
                    ],
                }
            )
        elif fmt == "openai":
            rows.append(
                {
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user},
                        {"role": "assistant", "content": reply},
                    ]
                }
            )
        else:
            raise ValueError(f"未知格式: {fmt}")
    return write_jsonl(path, rows)
