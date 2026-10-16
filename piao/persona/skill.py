"""生成 Skill 目录：SKILL.md + references/style.md + references/examples.md。"""

from __future__ import annotations

import random
import re
from importlib import resources
from pathlib import Path
from typing import Any

from ..chatlog.clean import is_placeholder_only
from ..chatlog.models import Pair, Turn
from .profile import MAHJONG_TERMS, render_profile_md
from .prompt import format_pair

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_PERSONA_MISSING = "（还没有填写 persona/persona.md。只能依据下面的统计和样例来模仿，不要编造本人的背景。）"


def _render(template: str, **values: Any) -> str:
    for k, v in values.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


def _clip(turn: Turn, max_chars: int = 80) -> Turn:
    lines = [line if len(line) <= max_chars else line[:max_chars] + "…" for line in turn.lines[:4]]
    return Turn(turn.sender_id, turn.sender_name, turn.time, lines)


def select_examples(pairs: list[Pair], k: int = 40, seed: int = 0, context_turns: int = 4) -> list[Pair]:
    """挑选有代表性的对话样例：有上下文、回复不是纯图片；一部分聊牌，长短回复都要有。"""
    usable = [p for p in pairs if p.context and not is_placeholder_only(p.reply.text)]
    rng = random.Random(seed)
    rng.shuffle(usable)
    mahjong = [p for p in usable if any(t in p.reply.text for t in MAHJONG_TERMS)]
    short = [p for p in usable if len(p.reply.text) <= 6]
    longer = [p for p in usable if len(p.reply.text) > 6]
    picked: list[Pair] = []
    seen: set[int] = set()
    for bucket, quota in ((mahjong, k * 3 // 10), (short, k * 3 // 10), (longer, k)):
        n = 0
        for p in bucket:
            if len(picked) >= k or n >= quota:
                break
            if id(p) in seen:
                continue
            seen.add(id(p))
            picked.append(p)
            n += 1
    picked.sort(key=lambda p: p.reply.time)
    return [Pair([_clip(t) for t in p.context[-context_turns:]], _clip(p.reply), p.group) for p in picked]


def render_examples_md(examples: list[Pair], persona_id: str, persona_name: str) -> str:
    blocks = [format_pair(p, persona_id, persona_name) for p in examples]
    return "# 真实群聊片段\n\n“飘：”开头的是本人的发言。\n\n" + "\n\n---\n\n".join(blocks) + "\n"


def render_style_md(profile: dict[str, Any]) -> str:
    out = ["# 风格统计（补充）", "", "SKILL.md 里是摘要，这里是完整列表。", ""]
    if profile["catchphrases"]:
        out += ["## 口癖（次数）", ""]
        out += [f"- {c['text']}（{c['count']}）" for c in profile["catchphrases"]]
        out.append("")
    if profile["top_messages"]:
        out += ["## 常用整句（次数）", ""]
        out += [f"- {c['text']}（{c['count']}）" for c in profile["top_messages"]]
        out.append("")
    hours = profile["active_hours"]
    total = sum(hours.values()) or 1
    out += ["## 各时段发言占比", ""]
    out += [f"- {int(h):02d} 点：{c / total * 100:.1f}%" for h, c in hours.items() if c]
    return "\n".join(out) + "\n"


def build_skill(
    skill_dir: str | Path,
    persona_name: str,
    persona_id: str,
    profile: dict[str, Any],
    pairs: list[Pair],
    persona_text: str | None,
    n_examples: int = 40,
) -> Path:
    skill_dir = Path(skill_dir)
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    slug = skill_dir.name
    persona = _COMMENT.sub("", persona_text or "").strip() or _PERSONA_MISSING
    template = resources.files("piao.templates").joinpath("SKILL.md").read_text(encoding="utf-8")
    skill_md = _render(
        template,
        slug=slug,
        name=persona_name,
        persona=persona,
        n_messages=profile["n_messages"],
        profile=render_profile_md(profile),
    )
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (skill_dir / "references" / "style.md").write_text(render_style_md(profile), encoding="utf-8")
    examples = select_examples(pairs, k=n_examples)
    (skill_dir / "references" / "examples.md").write_text(
        render_examples_md(examples, persona_id, persona_name), encoding="utf-8"
    )
    return skill_dir
