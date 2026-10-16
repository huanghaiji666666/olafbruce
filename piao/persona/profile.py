"""统计飘的说话习惯：消息长度、连发、标点、口癖、常用短句、活跃时段、日麻用语等。"""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from collections.abc import Iterable
from typing import Any

from ..chatlog.clean import is_placeholder_only
from ..chatlog.dataset import group_turns, split_by_group
from ..chatlog.models import Message, Pair

LAUGH_PATTERNS = {
    "哈哈": re.compile(r"哈{2,}"),
    "hhh": re.compile(r"[hH]{3,}"),
    "草": re.compile(r"草"),
    "笑死": re.compile(r"笑死"),
    "绷": re.compile(r"绷|蚌埠"),
    "乐": re.compile(r"(?<![快欢音娱])乐(?![器观意])"),
    "233": re.compile(r"233+"),
    "xswl": re.compile(r"xswl", re.I),
    "典": re.compile(r"典(?![型礼籍故范])"),
}

MAHJONG_TERMS = [
    "立直", "副露", "鸣牌", "默听", "听牌", "一向听", "两向听", "改良", "押引", "推", "放铳", "铳",
    "自摸", "荣和", "一发", "里宝", "宝牌", "赤", "牌效", "何切", "染手", "混一色", "清一色", "对对和",
    "七对", "国士", "役满", "振听", "天凤", "雀魂", "鳳凰", "凤凰", "特上", "段位", "九段", "十段", "天鳳位",
    "四麻", "三麻", "打点", "防守", "安牌", "现物", "筋", "壁", "早巡", "终盘", "横移", "流局", "亲家",
    "子家", "南场", "东场", "末巡", "一般形", "好型", "愚形", "坎张", "边张", "单骑", "两面", "追立",
    "先制", "役", "断幺", "平和", "dora", "雀力", "牌谱", "复盘", "大局观", "顺位", "四位", "避四", "R值",
]

_ENDINGS = [
    ("。", "。"),
    ("！/!", "！!"),
    ("？/?", "？?"),
    ("～/~", "～~"),
    ("…/...", "…."),
    ("，/,", "，,"),
]
_NGRAM_BAD = re.compile(r"[\s\[\]\d，。！？、,.!?~～…:：;；\"'“”‘’()（）@#/\\\-—_=+*]")


def _is_text(m: Message) -> bool:
    return not is_placeholder_only(m.text)


def _ngrams(text: str, ns: Iterable[int] = (2, 3, 4, 5)) -> Iterable[str]:
    text = re.sub(r"\[[^\[\]]{1,6}\]", " ", text)
    for n in ns:
        for i in range(len(text) - n + 1):
            g = text[i : i + n]
            if not _NGRAM_BAD.search(g):
                yield g


def distinctive_ngrams(
    target_texts: list[str], other_texts: list[str], top_k: int = 40, min_count: int | None = None
) -> list[tuple[str, int, float]]:
    """找出飘比其他群友明显更爱用的字串（平滑对数几率比 × √频次）。返回 (字串, 次数, 得分)。"""
    t_counts: Counter[str] = Counter()
    for text in target_texts:
        t_counts.update(set(_ngrams(text)))  # 每条消息只计一次，避免“哈哈哈哈哈”刷屏
    o_counts: Counter[str] = Counter()
    for text in other_texts:
        o_counts.update(set(_ngrams(text)))
    if min_count is None:
        min_count = max(3, len(target_texts) // 2000)
    nt, no = max(1, len(target_texts)), max(1, len(other_texts))
    alpha = 0.5
    scored = []
    for g, ct in t_counts.items():
        if ct < min_count:
            continue
        co = o_counts.get(g, 0)
        if other_texts:
            lor = math.log((ct + alpha) / (nt + alpha)) - math.log((co + alpha) / (no + alpha))
            if lor <= 0.3:
                continue
            score = lor * math.sqrt(ct)
        else:
            score = math.sqrt(ct) * len(g)
        scored.append((g, ct, score))
    scored.sort(key=lambda x: (-x[2], -len(x[0])))

    picked: list[tuple[str, int, float]] = []
    for g, ct, score in scored:
        # 频次接近的子串和长串只留长的（“绷不住”“不住了”→“绷不住了”）
        if any(g in p and ct <= p_ct * 1.3 for p, p_ct, _ in picked):
            continue
        picked = [x for x in picked if not (x[0] in g and x[1] <= ct * 1.3)]
        picked.append((g, ct, score))
        if len(picked) >= top_k:
            break
    return picked


def compute_profile(messages: list[Message], target_id: str, pairs: list[Pair] | None = None) -> dict[str, Any]:
    target = [m for m in messages if m.sender_id == target_id]
    if not target:
        raise ValueError(f"聊天记录里找不到 QQ {target_id} 的发言，检查 persona.qq 或导出文件")
    others = [m for m in messages if m.sender_id != target_id]
    text_msgs = [m for m in target if _is_text(m)]
    texts = [m.text for m in text_msgs]
    lengths = [len(t) for t in texts] or [0]

    names = Counter(m.sender_name for m in target)
    turns = []
    for msgs in split_by_group(messages).values():
        turns.extend(t for t in group_turns(msgs) if t.sender_id == target_id)

    endings: Counter[str] = Counter()
    for t in texts:
        last = t.rstrip()[-1:]
        label = next((lab for lab, chars in _ENDINGS if last and last in chars), "无标点")
        endings[label] += 1

    def ratio(pred) -> float:
        return round(sum(1 for t in texts if pred(t)) / max(1, len(texts)), 3)

    laughs = {k: sum(1 for t in texts if p.search(t)) for k, p in LAUGH_PATTERNS.items()}
    placeholders = Counter()
    for m in target:
        for ph in re.findall(r"\[[^\[\]]{1,6}\]", m.text):
            placeholders[ph] += 1

    short = Counter(t.strip() for t in texts if len(t.strip()) <= 12)
    top_messages = [(t, c) for t, c in short.most_common(60) if c >= 3][:40]

    terms = Counter()
    for t in texts:
        for term in MAHJONG_TERMS:
            if term in t:
                terms[term] += 1

    hours = Counter(m.time.hour for m in target)

    interlocutors: Counter[str] = Counter()
    for p in pairs or []:
        if p.context and p.context[-1].sender_id != target_id:
            interlocutors[p.context[-1].sender_name] += 1

    return {
        "target_id": target_id,
        "names": [n for n, _ in names.most_common(5)],
        "n_messages": len(target),
        "n_group_messages": len(messages),
        "share_of_group": round(len(target) / max(1, len(messages)), 4),
        "first": min(m.time for m in target).isoformat(sep=" "),
        "last": max(m.time for m in target).isoformat(sep=" "),
        "length": {
            "mean": round(statistics.fmean(lengths), 1),
            "median": statistics.median(lengths),
            "p90": sorted(lengths)[min(len(lengths) - 1, int(len(lengths) * 0.9))],
            "short_ratio": ratio(lambda t: len(t) <= 4),
        },
        "lines_per_turn": round(statistics.fmean(len(t.lines) for t in turns), 2) if turns else 1.0,
        "multi_line_turn_ratio": round(sum(1 for t in turns if len(t.lines) > 1) / max(1, len(turns)), 3),
        "endings": {k: round(v / max(1, len(texts)), 3) for k, v in endings.most_common()},
        "punctuation": {
            "uses_comma": ratio(lambda t: "，" in t or "," in t),
            "uses_period": ratio(lambda t: "。" in t),
            "halfwidth_question": ratio(lambda t: "?" in t),
            "fullwidth_question": ratio(lambda t: "？" in t),
            "ellipsis": ratio(lambda t: "..." in t or "…" in t),
            "space_as_pause": ratio(lambda t: " " in t.strip() and not re.search(r"[A-Za-z] [A-Za-z]", t)),
        },
        "laughs": {k: v for k, v in sorted(laughs.items(), key=lambda kv: -kv[1]) if v},
        "placeholders": dict(placeholders.most_common(8)),
        "catchphrases": [
            {"text": g, "count": c} for g, c, _ in distinctive_ngrams(texts, [m.text for m in others if _is_text(m)])
        ],
        "top_messages": [{"text": t, "count": c} for t, c in top_messages],
        "mahjong_terms": dict(terms.most_common(25)),
        "active_hours": {h: hours.get(h, 0) for h in range(24)},
        "interlocutors": dict(interlocutors.most_common(12)),
    }


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def render_profile_md(p: dict[str, Any]) -> str:
    """把统计结果写成给人和模型都能看懂的说明。"""
    L = p["length"]
    lines = [
        f"- 统计样本：{p['n_messages']} 条发言（占群消息 {_pct(p['share_of_group'])}），"
        f"时间 {p['first'][:10]} ~ {p['last'][:10]}；群名片曾用：{'、'.join(p['names'])}",
        f"- 消息长度：平均 {L['mean']} 字，中位数 {L['median']} 字，90% 的消息不超过 {L['p90']} 字；"
        f"{_pct(L['short_ratio'])} 的消息只有 1~4 个字",
        f"- 连发：平均每次开口发 {p['lines_per_turn']} 条，{_pct(p['multi_line_turn_ratio'])} 的时候会拆成多条连发",
    ]
    endings = "，".join(f"{k} {_pct(v)}" for k, v in p["endings"].items())
    lines.append(f"- 句尾：{endings}")
    pu = p["punctuation"]
    lines.append(
        f"- 标点：用逗号 {_pct(pu['uses_comma'])}，用句号 {_pct(pu['uses_period'])}，"
        f"半角问号 {_pct(pu['halfwidth_question'])} / 全角问号 {_pct(pu['fullwidth_question'])}，"
        f"省略号 {_pct(pu['ellipsis'])}，用空格断句 {_pct(pu['space_as_pause'])}"
    )
    if p["laughs"]:
        lines.append("- 笑/情绪词（出现条数）：" + "，".join(f"{k}×{v}" for k, v in p["laughs"].items()))
    if p["placeholders"]:
        lines.append("- 非文字消息：" + "，".join(f"{k}×{v}" for k, v in p["placeholders"].items()))
    peak = sorted(p["active_hours"].items(), key=lambda kv: -kv[1])[:4]
    lines.append("- 最活跃时段：" + "、".join(f"{int(h)}点" for h, c in peak if c))
    if p["catchphrases"]:
        lines.append("- 口癖（比其他群友明显更常用）：" + "、".join(f"「{c['text']}」" for c in p["catchphrases"][:30]))
    if p["top_messages"]:
        lines.append("- 常用短句（整条消息，次数）：" + "、".join(f"「{c['text']}」×{c['count']}" for c in p["top_messages"][:30]))
    if p["mahjong_terms"]:
        lines.append("- 日麻用语频次：" + "，".join(f"{k}×{v}" for k, v in list(p["mahjong_terms"].items())[:20]))
    if p["interlocutors"]:
        lines.append("- 最常接谁的话：" + "、".join(f"{k}({v})" for k, v in p["interlocutors"].items()))
    return "\n".join(lines)
