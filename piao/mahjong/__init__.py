"""日麻牌效：解析手牌记号、计算向听数和进张，给“何切”提供可信的数据。"""

from .analysis import analyze_hand, find_hand_in_text
from .shanten import shanten
from .tiles import parse_tiles, tiles_to_str

__all__ = ["analyze_hand", "find_hand_in_text", "parse_tiles", "shanten", "tiles_to_str"]
