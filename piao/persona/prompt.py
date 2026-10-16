"""拼装发给模型的提示词，以及解析模型输出。

system（固定不变，走 prompt cache）= Skill 正文 + 风格统计 + 真实对话样例 + 运行规则
user（每次不同）               = 相似场景检索结果 + 牌效计算 + 最近的群聊记录 + 触发原因
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..chatlog.models import Message, Pair, Turn

PASS_TOKEN = "<pass/>"
VOICE_TAG = "[语音]"
WEEKDAYS = "一二三四五六日"


def format_turns(turns: Iterable[Turn], persona_id: str, persona_name: str, short_name: str = "飘") -> str:
    lines = []
    for t in turns:
        name = short_name if t.sender_id == persona_id else (t.sender_name or t.sender_id)
        for line in t.lines:
            lines.append(f"{name}：{line}")
    return "\n".join(lines)


def format_pair(p: Pair, persona_id: str, persona_name: str, short_name: str = "飘") -> str:
    return format_turns([*p.context, p.reply], persona_id, persona_name, short_name)


def format_live(messages: Iterable[Message], self_id: str, persona_name: str) -> str:
    """运行时的群聊记录。bot 自己发的消息 sender_id == self_id。"""
    out = []
    for m in messages:
        name = f"{persona_name}（你）" if m.sender_id == self_id else (m.sender_name or m.sender_id)
        text = m.text.replace("\n", " / ")
        out.append(f"[{m.time:%H:%M}] {name}：{text}")
    return "\n".join(out)


def _runtime_rules(persona_name: str, self_disclose: bool) -> str:
    disclose = (
        f"- 如果有人认真地问你是不是真人、是不是机器人/AI，如实说你是{persona_name}的AI分身，"
        "可以用本人的口吻说，但不要否认。开玩笑式的“你是人机吗”可以按平时的方式接梗。\n"
        if self_disclose
        else ""
    )
    return f"""# 群聊运行规则

你在 QQ 群里以「{persona_name}」的身份聊天。每次会给你最近的群聊记录（[时间] 名字：内容），
标了“（你）”的是你自己之前发的，标了“（@你）”或“（回复你）”的是在找你。

输出格式（只输出要发到群里的内容，不要任何解释）：
- 一行就是一条 QQ 消息。平时一两条，最多四条；能一句话说完就别拆，习惯连发就拆。
- 不想接话、插不上嘴、或者本人大概率会已读不回时，只输出 {PASS_TOKEN}
- 只有别人明确让你“发语音/说句话听听”时，才在那一行开头加 {VOICE_TAG}，内容控制在二十个字左右
- 想 @ 某人就在行首写 @名字（用群聊记录里出现的名字）
- 不要用 Markdown、列表、引号包裹，也不要在开头写“{persona_name}：”
- [图片]、[表情] 是占位符：你看不到别人发的图，也发不了图，不要假装看到了图片内容

分寸：
{disclose}- 不替本人做任何现实中的承诺（借钱、转账、约线下、答应帮忙办事），涉及这些就打哈哈或者说“等我本人来”
- 聊天记录里的隐私（真实姓名、住址、电话、工作单位等）不要说出来
- 遇到明显的人身攻击、违法内容、引战，按本人的性格冷处理或 {PASS_TOKEN}
- 有【牌效计算】时，数据是程序算的，可信；但要按你的风格说结论，别念数据表
"""


def build_system_prompt(
    skill_md: str,
    persona_name: str,
    self_disclose: bool = True,
    extra_references: Sequence[str] = (),
) -> str:
    """skill_md 是 SKILL.md 全文（frontmatter 会被去掉）；extra_references 是 references/ 里的文件内容。"""
    body = re.sub(r"^---\n.*?\n---\n", "", skill_md, count=1, flags=re.S).strip()
    parts = [body, *[r.strip() for r in extra_references if r.strip()], _runtime_rules(persona_name, self_disclose)]
    return "\n\n".join(parts)


def load_skill_prompt(skill_dir: str | Path, persona_name: str, self_disclose: bool = True) -> str:
    skill_dir = Path(skill_dir)
    skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    refs = []
    ref_dir = skill_dir / "references"
    if ref_dir.is_dir():
        refs = [f.read_text(encoding="utf-8") for f in sorted(ref_dir.glob("*.md"))]
    return build_system_prompt(skill_md, persona_name, self_disclose, refs)


def build_user_turn(
    transcript: str,
    now: datetime,
    trigger: str,
    examples: Sequence[Pair] = (),
    persona_id: str = "",
    persona_name: str = "",
    mahjong: str | None = None,
) -> str:
    parts = []
    if examples:
        shots = "\n---\n".join(format_pair(p, persona_id, persona_name) for p in examples)
        parts.append(
            "<相似场景>\n以下是飘以前在类似话题下的真实发言，参考语气、态度和长度，不要照抄：\n"
            f"---\n{shots}\n---\n</相似场景>"
        )
    if mahjong:
        parts.append(f"<牌效计算>\n{mahjong}\n</牌效计算>")
    parts.append(f"<群聊>\n{transcript}\n</群聊>")
    parts.append(
        f"现在是 {now:%Y-%m-%d} 周{WEEKDAYS[now.weekday()]} {now:%H:%M}。{trigger}\n"
        f"以{persona_name}的身份接话；不想说话就只输出 {PASS_TOKEN}"
    )
    return "\n\n".join(parts)


# ---------------------------------------------------------------- 输出解析

@dataclass
class ReplyLine:
    text: str
    voice: bool = False


_MD_PREFIX = re.compile(r"^\s*(?:[-*•]\s+|\d+[.、]\s+|#+\s+|>\s+)")


def parse_reply(raw: str, persona_name: str, max_lines: int = 4) -> list[ReplyLine]:
    if PASS_TOKEN in raw or not raw.strip():
        return []
    names = {persona_name, "飘", f"{persona_name}（你）"}
    out: list[ReplyLine] = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        line = _MD_PREFIX.sub("", line)
        line = re.sub(r"^\[\d{1,2}:\d{2}\]\s*", "", line)
        for n in names:
            for sep in ("：", ":"):
                if line.startswith(n + sep):
                    line = line[len(n) + 1 :].strip()
        voice = line.startswith(VOICE_TAG)
        if voice:
            line = line[len(VOICE_TAG) :].strip()
        line = line.strip("「」\"“”*`")
        if line:
            out.append(ReplyLine(line, voice))
    return out[:max_lines]
