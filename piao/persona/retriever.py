"""在飘的历史发言里找“相似场景”，作为每次回复时的参考样例（BM25，纯 Python，无额外依赖）。"""

from __future__ import annotations

import math
import random
import re
from collections import Counter, defaultdict
from collections.abc import Sequence

from ..chatlog.clean import is_placeholder_only
from ..chatlog.models import Pair

_WORD = re.compile(r"[A-Za-z]+|\d+[mpsz]|[一-鿿぀-ヿ]")
_PLACEHOLDER = re.compile(r"\[[^\[\]]{1,6}\]")


def tokenize(text: str) -> list[str]:
    """中日文按字 + 相邻二字，英文/牌谱记号按词。"""
    text = _PLACEHOLDER.sub(" ", text.lower())
    units = _WORD.findall(text)
    tokens = list(units)
    tokens += [a + b for a, b in zip(units, units[1:], strict=False) if len(a) == 1 and len(b) == 1]
    return tokens


class PairRetriever:
    def __init__(self, pairs: Sequence[Pair], context_window: int = 2, k1: float = 1.4, b: float = 0.75):
        # 只要有上下文、且飘的回复不是纯图片/表情的样本
        self.pairs = [p for p in pairs if p.context and not is_placeholder_only(p.reply.text)]
        self.k1, self.b = k1, b
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.doc_len: list[int] = []
        for i, p in enumerate(self.pairs):
            text = " ".join(t.text for t in p.context[-context_window:])
            tf = Counter(tokenize(text))
            self.doc_len.append(sum(tf.values()))
            for tok, c in tf.items():
                self.postings[tok].append((i, c))
        n = len(self.pairs)
        self.avgdl = sum(self.doc_len) / n if n else 0.0
        self.idf = {
            tok: math.log(1 + (n - len(post) + 0.5) / (len(post) + 0.5)) for tok, post in self.postings.items()
        }

    def __len__(self) -> int:
        return len(self.pairs)

    def search(self, query: str, k: int = 6, exclude_reply: str | None = None) -> list[Pair]:
        if not self.pairs:
            return []
        scores: dict[int, float] = defaultdict(float)
        for tok, qtf in Counter(tokenize(query)).items():
            idf = self.idf.get(tok)
            if idf is None:
                continue
            for i, tf in self.postings[tok]:
                denom = tf + self.k1 * (1 - self.b + self.b * self.doc_len[i] / (self.avgdl or 1))
                scores[i] += idf * tf * (self.k1 + 1) / denom * min(qtf, 3)
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        out: list[Pair] = []
        seen_replies: set[str] = set()
        for i, _ in ranked:
            p = self.pairs[i]
            reply = p.reply.text
            if reply in seen_replies or reply == exclude_reply:
                continue
            seen_replies.add(reply)
            out.append(p)
            if len(out) >= k:
                break
        return out

    def sample(self, k: int, seed: int = 0) -> list[Pair]:
        """固定随机种子的抽样，保证系统提示词每次一样（便于 prompt cache 命中）。"""
        rng = random.Random(seed)
        return rng.sample(self.pairs, min(k, len(self.pairs)))
