import pytest

from piao.mahjong import (
    analyze_hand,
    find_hand_in_text,
    parse_tiles,
    shanten,
    tiles_to_str,
)
from piao.mahjong.analysis import discard_options, ukeire


@pytest.mark.parametrize(
    "hand,expected",
    [
        ("123m456p789s11222z", -1),
        ("123m456p789s1122z", 0),
        ("11223344556677z", -1),  # 七对子和了
        ("1122334455667m", 0),  # 七对子听牌（同时也是一般形一向听以内）
        ("19m19p19s1234567z", 0),  # 国士十三面
        ("123456789m1234z", 2),
        ("1112345678999m", 0),  # 九莲
        ("147m258p369s1234z", 6),
        ("123m456p789s123z", 2),
    ],
)
def test_shanten(hand, expected):
    assert shanten(parse_tiles(hand)) == expected


def test_shanten_with_melds():
    # 副露两组，剩 7 张
    assert shanten(parse_tiles("23m456p9s1z"), melds=2) == 1
    assert shanten(parse_tiles("23m456p11z"), melds=2) == 0


def test_parse_and_format():
    c = parse_tiles("406m 11z+5m")
    assert sum(c) == 6 and c[4] == 2  # 0m = 赤五
    assert tiles_to_str(c, counts=True) == "4556m 11z"
    with pytest.raises(ValueError):
        parse_tiles("11111m")
    with pytest.raises(ValueError):
        parse_tiles("8z")
    with pytest.raises(ValueError):
        parse_tiles("hello")


def test_ukeire_and_discards():
    c = parse_tiles("123m456p789s1122z")
    assert ukeire(c) == [(27, 2), (28, 2)]
    opts = discard_options(parse_tiles("23466m345p11s5678p"))
    assert {opts[0].discard, opts[1].discard} == {13, 16}  # 切 5p / 8p 都听 6m 1s
    assert opts[0].shanten == 0 and opts[0].total == 4


def test_analyze_text():
    text = analyze_hand("23466m345p11s5678p")
    assert "当前听牌" in text and "切5p" in text and "（退向）" in text
    assert "听 12z" in analyze_hand("123m456p789s1122z")
    assert "和了" in analyze_hand("123m456p789s11222z")
    with pytest.raises(ValueError):
        analyze_hand("123m456p")


def test_find_hand_in_text():
    assert find_hand_in_text("飘哥 23466m 345p 11s 5678p 切啥") == "23466m 345p 11s 5678p"
    assert find_hand_in_text("今天3m放铳了") is None
    assert find_hand_in_text("123456789m 打了5z") is None
