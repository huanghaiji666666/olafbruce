"""向听数：一般形 / 七对子 / 国士无双，取最小值。

一般形按花色分别枚举拆法（面子、搭子、雀头），每种花色的结果带缓存，
算何切时只有一两个花色在变，所以几百次向听计算也很快。
"""

from __future__ import annotations

from functools import lru_cache

YAOCHU = (0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33)

Block = tuple[int, int, int]  # (面子数, 搭子数, 有无雀头)


def _pareto(states: set[Block]) -> tuple[Block, ...]:
    """去掉被支配的拆法（面子、搭子、雀头都不多于另一种的）。"""
    items = sorted(states, reverse=True)
    kept: list[Block] = []
    for s in items:
        if not any(k[0] >= s[0] and k[1] >= s[1] and k[2] >= s[2] for k in kept):
            kept.append(s)
    return tuple(kept)


@lru_cache(maxsize=65536)
def _suit_blocks(counts: tuple[int, ...], honor: bool) -> tuple[Block, ...]:
    c = list(counts)
    n = len(c)
    results: set[Block] = set()

    def dfs(i: int, m: int, t: int, p: int) -> None:
        while i < n and c[i] == 0:
            i += 1
        if i >= n:
            results.add((m, t, p))
            return
        if c[i] >= 3:  # 刻子
            c[i] -= 3
            dfs(i, m + 1, t, p)
            c[i] += 3
        if not honor and i + 2 < n and c[i + 1] and c[i + 2]:  # 顺子
            c[i] -= 1; c[i + 1] -= 1; c[i + 2] -= 1
            dfs(i, m + 1, t, p)
            c[i] += 1; c[i + 1] += 1; c[i + 2] += 1
        if c[i] >= 2:  # 对子：当雀头或当搭子
            c[i] -= 2
            if not p:
                dfs(i, m, t, 1)
            dfs(i, m, t + 1, p)
            c[i] += 2
        if not honor:
            if i + 1 < n and c[i + 1]:  # 两面/边张
                c[i] -= 1; c[i + 1] -= 1
                dfs(i, m, t + 1, p)
                c[i] += 1; c[i + 1] += 1
            if i + 2 < n and c[i + 2]:  # 坎张
                c[i] -= 1; c[i + 2] -= 1
                dfs(i, m, t + 1, p)
                c[i] += 1; c[i + 2] += 1
        c[i] -= 1  # 孤张
        dfs(i, m, t, p)
        c[i] += 1

    dfs(0, 0, 0, 0)
    return _pareto(results)


def standard_shanten(counts: list[int] | tuple[int, ...], melds: int = 0) -> int:
    groups = [
        _suit_blocks(tuple(counts[0:9]), False),
        _suit_blocks(tuple(counts[9:18]), False),
        _suit_blocks(tuple(counts[18:27]), False),
        _suit_blocks(tuple(counts[27:34]), True),
    ]
    states: set[Block] = {(0, 0, 0)}
    for opts in groups:
        new: set[Block] = set()
        for m, t, p in states:
            for m2, t2, p2 in opts:
                if p + p2 <= 1:
                    new.add((m + m2, t + t2, p + p2))
        states = set(_pareto(new))
    best = 8
    for m, t, p in states:
        mm = min(m + melds, 4)
        tt = min(t, 4 - mm)
        best = min(best, 8 - 2 * mm - tt - p)
    return best


def chiitoi_shanten(counts: list[int] | tuple[int, ...]) -> int:
    pairs = sum(1 for c in counts if c >= 2)
    kinds = sum(1 for c in counts if c >= 1)
    return 6 - pairs + max(0, 7 - kinds)


def kokushi_shanten(counts: list[int] | tuple[int, ...]) -> int:
    kinds = sum(1 for i in YAOCHU if counts[i])
    has_pair = any(counts[i] >= 2 for i in YAOCHU)
    return 13 - kinds - (1 if has_pair else 0)


def shanten(counts: list[int] | tuple[int, ...], melds: int = 0) -> int:
    """-1 = 和了，0 = 听牌，1 = 一向听 ……（melds = 副露数，副露时不算七对/国士）"""
    s = standard_shanten(counts, melds)
    if melds == 0 and sum(counts) >= 13:
        s = min(s, chiitoi_shanten(counts), kokushi_shanten(counts))
    return s
