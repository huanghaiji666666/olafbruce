"""何切 / 听牌分析，以及从群消息里找出手牌记号。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .shanten import shanten
from .tiles import parse_tiles, tile_str, tiles_to_str

_HAND_RUN = re.compile(r"(?:\d+[mpsz][\s+]*){1,8}")


def shanten_name(s: int) -> str:
    if s < 0:
        return "和了"
    if s == 0:
        return "听牌"
    return ("一", "两", "三", "四", "五", "六")[s - 1] + "向听" if s <= 6 else f"{s}向听"


@dataclass
class Option:
    discard: int | None
    shanten: int
    tiles: list[tuple[int, int]]  # (牌, 剩余枚数)

    @property
    def total(self) -> int:
        return sum(n for _, n in self.tiles)


def ukeire(counts: list[int], melds: int = 0, current: int | None = None) -> list[tuple[int, int]]:
    """3n+1 张时，能让向听数前进的牌及其剩余枚数（只扣除自己手里的）。"""
    if current is None:
        current = shanten(counts, melds)
    out = []
    for t in range(34):
        if counts[t] >= 4:
            continue
        counts[t] += 1
        if shanten(counts, melds) < current:
            out.append((t, 4 - (counts[t] - 1)))
        counts[t] -= 1
    return out


def discard_options(counts: list[int], melds: int = 0) -> list[Option]:
    """3n+2 张时，每种切法的向听数和进张，按（向听数，进张数）排序。"""
    opts = []
    for i in range(34):
        if counts[i] == 0:
            continue
        counts[i] -= 1
        s = shanten(counts, melds)
        opts.append(Option(i, s, ukeire(counts, melds, s)))
        counts[i] += 1
    opts.sort(key=lambda o: (o.shanten, -o.total, o.discard))
    return opts


def _fmt_tiles(tiles: list[tuple[int, int]]) -> str:
    return tiles_to_str([t for t, _ in tiles])


def analyze_hand(text: str, melds: int = 0, top: int = 5) -> str:
    """输入手牌记号，输出给模型看的分析摘要。"""
    counts = parse_tiles(text)
    n = sum(counts)
    hand = tiles_to_str(counts, counts=True)
    if n % 3 == 2:
        s = shanten(counts, melds)
        if s < 0:
            return f"手牌 {hand}（{n}张）：已经和了。"
        opts = discard_options(counts, melds)
        best = opts[0].shanten
        lines = [f"手牌 {hand}（{n}张），当前{shanten_name(s)}。各切法（按向听数、进张枚数排序）："]
        for o in opts[:top]:
            mark = "" if o.shanten == best else "（退向）"
            lines.append(
                f"- 切{tile_str(o.discard)}：{shanten_name(o.shanten)}{mark}，"
                f"进张 {_fmt_tiles(o.tiles)} 共{len(o.tiles)}种{o.total}枚"
            )
        lines.append("注：枚数只扣了自己手里的牌，没算场上已见的；没考虑打点、役和安全度。")
        return "\n".join(lines)
    if n % 3 == 1:
        s = shanten(counts, melds)
        tiles = ukeire(counts, melds, s)
        what = "听" if s == 0 else "进张"
        return (
            f"手牌 {hand}（{n}张）：{shanten_name(s)}，{what} {_fmt_tiles(tiles)} 共{len(tiles)}种"
            f"{sum(k for _, k in tiles)}枚（只扣了自己手里的牌）。"
        )
    raise ValueError(f"张数不对（{n}张），手牌应为 3n+1 或 3n+2 张")


def find_hand_in_text(text: str) -> str | None:
    """在消息里找 13 或 14 张的手牌记号（如“123m456p789s1122z 切啥”），找不到返回 None。"""
    best = None
    for m in _HAND_RUN.finditer(text):
        chunk = m.group(0).strip()
        try:
            n = sum(parse_tiles(chunk))
        except ValueError:
            continue
        if n in (13, 14) and (best is None or len(chunk) > len(best)):
            best = chunk
    return best
