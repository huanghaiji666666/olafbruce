import json
from datetime import datetime

from piao.chatlog.clean import clean_messages, normalize_text, redact
from piao.chatlog.dataset import build_pairs, export_sft, group_turns, read_jsonl
from piao.chatlog.models import Message
from piao.chatlog.parsers import (
    load_messages,
    parse_json,
    parse_qq_txt,
    segments_to_text,
)

from .conftest import TARGET


def test_parse_qq_txt(qq_txt):
    msgs = parse_qq_txt(qq_txt)
    assert [m.sender_id for m in msgs] == ["123456", TARGET, "lisi@example.com", TARGET]
    assert msgs[0].sender_name == "张三"  # 头衔被去掉
    assert msgs[0].group == "日麻交流群"
    assert msgs[1].text == "切5p\n没什么好说的"  # 多行消息
    assert msgs[2].time.hour == 9  # 一位数小时
    assert all(m.sender_id != "10000" for m in msgs)


def test_parse_qce_style_json(tmp_path):
    data = {
        "chatInfo": {"name": "日麻群", "type": "group"},
        "messages": [
            {
                "timestamp": 1700000000000,
                "sender": {"uid": "u_x", "uin": TARGET, "name": "轻轻的飘"},
                "content": {"text": "确实"},
            },
            {"timestamp": 1700000001000, "sender": {"uin": "1"}, "content": {"text": "撤回"}, "recalled": True},
            {
                "time": "2023-11-14 22:13:30",
                "sender_id": 222,
                "sender_name": "李四",
                "message": [{"type": "text", "data": {"text": "看"}}, {"type": "image", "data": {}}],
            },
        ],
    }
    p = tmp_path / "x.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    msgs = parse_json(p)
    assert len(msgs) == 2
    assert msgs[0].sender_id == TARGET and msgs[0].text == "确实" and msgs[0].group == "日麻群"
    assert msgs[0].time == datetime(2023, 11, 15, 6, 13, 20)  # 毫秒时间戳 → 北京时间
    assert msgs[1].text == "看[图片]"


def test_parse_jsonl_and_dedupe(tmp_path, qq_txt):
    p = tmp_path / "x.jsonl"
    rows = [{"time": "2024-01-01 10:00:00", "sender_id": "1", "sender_name": "a", "text": "hi"}] * 2
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    msgs = load_messages([tmp_path])
    assert sum(1 for m in msgs if m.text == "hi") == 1
    assert len(msgs) == 5


def test_segments_to_text():
    segs = [
        {"type": "reply", "data": {"id": "1"}},
        {"type": "at", "data": {"qq": "123", "name": "张三"}},
        {"type": "text", "data": {"text": "你看"}},
        {"type": "face", "data": {"id": "1"}},
    ]
    assert segments_to_text(segs) == "@张三 你看[表情]"


def test_normalize_and_redact():
    assert normalize_text("[CQ:at,qq=123] 草[CQ:image,file=a.jpg]") == "@某人  草[图片]"
    assert redact("电话13812345678 邮箱a@b.com 看 https://x.com/a") == "电话[手机号] 邮箱[邮箱] 看 [链接]"
    assert redact("25000点") == "25000点"


def test_clean_drops_system_messages():
    t = datetime(2024, 1, 1)
    msgs = [
        Message(t, "1", "a", "张三 撤回了一条消息"),
        Message(t, "1", "a", "[动画表情]"),
        Message(t, "1", "a", "  "),
    ]
    out = clean_messages(msgs)
    assert [m.text for m in out] == ["[表情]"]


def test_group_turns_and_pairs(qq_txt):
    msgs = clean_messages(parse_qq_txt(qq_txt))
    msgs.sort(key=lambda m: m.time)
    turns = group_turns(msgs)
    assert turns[0].sender_id == "lisi@example.com"  # 09:16 最早
    pairs = build_pairs(msgs, TARGET)
    assert len(pairs) == 2
    first = pairs[0]
    assert first.reply.lines == ["切5p", "没什么好说的"]
    assert first.context[-1].sender_name == "张三"
    # 第二次发言前一轮仍是自己（隔了 70 秒，不合并）
    assert pairs[1].context[-1].sender_id == TARGET


def test_pairs_respect_time_gap():
    t = datetime(2024, 1, 1, 12)
    from datetime import timedelta

    msgs = [
        Message(t, "1", "a", "早"),
        Message(t + timedelta(hours=3), "1", "a", "在吗"),
        Message(t + timedelta(hours=3, seconds=30), TARGET, "飘", "在"),
    ]
    pairs = build_pairs(msgs, TARGET)
    assert [t.text for t in pairs[0].context] == ["在吗"]


def test_export_sft(tmp_path, qq_txt):
    pairs = build_pairs(clean_messages(parse_qq_txt(qq_txt)), TARGET)
    out = tmp_path / "sft.jsonl"
    n = export_sft(pairs, out, TARGET, "轻轻的飘", "sys", fmt="openai")
    rows = read_jsonl(out)
    assert n == len(rows) == 2
    assert rows[0]["messages"][2]["content"] == "切5p\n没什么好说的"
    assert "张三：飘哥" in rows[0]["messages"][1]["content"]
