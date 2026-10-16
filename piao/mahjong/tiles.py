"""牌的表示：34 种牌用 0~33 表示（0-8 万，9-17 筒，18-26 索，27-33 东南西北白发中）。"""

from __future__ import annotations

import re
from collections.abc import Iterable

SUITS = "mpsz"
HONOR_NAMES = "东南西北白发中"
_GROUP = re.compile(r"(\d+)([mpsz])")


def tile_index(digit: int, suit: str) -> int:
    if suit == "z":
        if not 1 <= digit <= 7:
            raise ValueError(f"字牌只有 1~7z: {digit}z")
        return 27 + digit - 1
    if digit == 0:  # 赤五
        digit = 5
    return SUITS.index(suit) * 9 + digit - 1


def parse_tiles(text: str) -> list[int]:
    """'123m406p789s11z' → 34 格计数。不合法时抛 ValueError。"""
    counts = [0] * 34
    compact = re.sub(r"[\s+]", "", text)
    if not compact or _GROUP.sub("", compact):
        raise ValueError(f"无法识别的手牌: {text}")
    for digits, suit in _GROUP.findall(compact):
        for ch in digits:
            counts[tile_index(int(ch), suit)] += 1
    if any(c > 4 for c in counts):
        raise ValueError(f"同一种牌超过 4 张: {text}")
    return counts


def tile_str(i: int) -> str:
    return f"{i % 9 + 1}{SUITS[i // 9]}" if i < 27 else f"{i - 26}z"


def tiles_to_str(tiles: Iterable[int], counts: bool = False) -> str:
    """tiles 是牌的编号列表（counts=False）或 34 格计数（counts=True）→ '147m 25p 1z'。"""
    seq: list[int] = []
    if counts:
        for i, c in enumerate(tiles):
            seq += [i] * c
    else:
        seq = sorted(tiles)
    parts = []
    for s, suit in enumerate(SUITS):
        lo, hi = s * 9, (34 if suit == "z" else s * 9 + 9)
        nums = "".join(str(i - lo + 1) for i in seq if lo <= i < hi)
        if nums:
            parts.append(nums + suit)
    return " ".join(parts)
