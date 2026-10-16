"""解析常见的 QQ 聊天记录导出格式。

支持：
- 旧版 PC QQ「消息管理器 → 导出为 .txt」
- JSON / JSONL：QQ Chat Exporter（QQNT）、NapCat/OneBot 消息段、或自己整理的
  {"time", "sender_id", "sender_name", "text"} 格式。字段名做了宽松匹配。
- CSV：表头包含 time / sender_id / sender_name / text（同样宽松匹配）。
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import Message

CHINA_TZ = timezone(timedelta(hours=8))
SUPPORTED_SUFFIXES = {".txt", ".json", ".jsonl", ".csv"}

# 2021-03-04 21:15:32 【管理员】轻轻的飘(2044715303)
# 2021-03-04 9:05:01 某人<someone@example.com>
_TXT_HEADER = re.compile(
    r"^(\d{4}-\d{1,2}-\d{1,2}) (\d{1,2}:\d{2}:\d{2}) (.*?)(?:\((\d{5,12})\)|<([^<>]+)>)\s*$"
)
_TXT_GROUP = re.compile(r"^消息对象[:：](.+)$")
_TITLE_PREFIX = re.compile(r"^【[^】]{1,12}】")
SYSTEM_SENDERS = {"10000", "1000000"}


# ---------------------------------------------------------------- 工具函数

def now_china() -> datetime:
    return datetime.now(CHINA_TZ).replace(tzinfo=None)


def to_datetime(value: Any) -> datetime | None:
    """时间戳（秒/毫秒）或字符串 → 北京时间的 naive datetime。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000
        return datetime.fromtimestamp(ts, CHINA_TZ).replace(tzinfo=None)
    if isinstance(value, str):
        s = value.strip().replace("/", "-")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is not None:
            dt = dt.astimezone(CHINA_TZ).replace(tzinfo=None)
        return dt
    return None


def _pick(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


SEGMENT_PLACEHOLDER = {
    "image": "[图片]",
    "face": "[表情]",
    "mface": "[表情]",
    "record": "[语音]",
    "video": "[视频]",
    "file": "[文件]",
    "forward": "[聊天记录]",
    "json": "[卡片]",
    "xml": "[卡片]",
    "dice": "[骰子]",
    "rps": "[猜拳]",
    "poke": "[戳一戳]",
}


def segments_to_text(segments: Iterable[Any]) -> str:
    """OneBot v11 消息段数组 → 文本（非文字内容用占位符表示）。"""
    out: list[str] = []
    for seg in segments:
        if isinstance(seg, str):
            out.append(seg)
            continue
        if not isinstance(seg, dict):
            continue
        typ = seg.get("type", "")
        data = seg.get("data") or {}
        if typ == "text":
            out.append(str(data.get("text", "")))
        elif typ == "at":
            out.append("@" + str(data.get("name") or data.get("qq") or "某人") + " ")
        elif typ == "reply":
            continue
        elif typ == "mface" and data.get("summary"):
            out.append(str(data["summary"]))
        else:
            out.append(SEGMENT_PLACEHOLDER.get(typ, ""))
    return "".join(out).strip()


def _qqnt_elements_to_text(elements: Iterable[Any]) -> str:
    """QQNT 原始 elements（textElement / picElement / faceElement ...）→ 文本。"""
    out: list[str] = []
    for el in elements:
        if not isinstance(el, dict):
            continue
        if el.get("textElement"):
            out.append(str(el["textElement"].get("content", "")))
        elif el.get("picElement"):
            out.append("[图片]")
        elif el.get("faceElement") or el.get("marketFaceElement"):
            out.append("[表情]")
        elif el.get("pttElement"):
            out.append("[语音]")
        elif el.get("videoElement"):
            out.append("[视频]")
        elif el.get("fileElement"):
            out.append("[文件]")
        elif el.get("arkElement"):
            out.append("[卡片]")
        elif el.get("multiForwardMsgElement"):
            out.append("[聊天记录]")
        elif el.get("type") == "text" and "content" in el:  # 一些导出器的简化 elements
            out.append(str(el["content"]))
    return "".join(out).strip()


def _record_text(rec: dict[str, Any]) -> str:
    content = rec.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        text = _pick(content, "text", "plain", "raw")
        if isinstance(text, str):
            return text
        if isinstance(content.get("elements"), list):
            return _qqnt_elements_to_text(content["elements"])
    if isinstance(content, list):
        return segments_to_text(content)
    for key in ("text", "raw_message", "rawMessage", "plain"):
        if isinstance(rec.get(key), str):
            return rec[key]
    message = rec.get("message")
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        return segments_to_text(message)
    if isinstance(rec.get("elements"), list):
        return _qqnt_elements_to_text(rec["elements"])
    return ""


def record_to_message(rec: dict[str, Any], group: str = "") -> Message | None:
    """把一条字段名不确定的 JSON/CSV 记录转换为 Message；无法识别时返回 None。"""
    if rec.get("recalled") is True or rec.get("system") is True or rec.get("isSystem") is True:
        return None
    time = to_datetime(_pick(rec, "timestamp", "time", "msgTime", "date", "datetime", "sendTime"))

    sender = rec.get("sender") if isinstance(rec.get("sender"), dict) else {}
    sender_id = _pick(sender, "uin", "user_id", "qq", "id", "senderUin") or _pick(
        rec, "sender_id", "senderId", "user_id", "senderUin", "uin", "qq", "from_id"
    )
    if sender_id is None:
        sender_id = _pick(sender, "uid") or _pick(rec, "senderUid")
    sender_name = _pick(
        sender, "card", "groupCard", "remark", "name", "nickname", "nick", "memberName"
    ) or _pick(
        rec, "sender_name", "senderName", "sendMemberName", "sendNickName", "nickname", "name"
    )
    if isinstance(rec.get("sender"), str) and not sender_name:
        sender_name = rec["sender"]

    text = _record_text(rec)
    if time is None or sender_id is None:
        return None
    return Message(
        time=time,
        sender_id=str(sender_id),
        sender_name=str(sender_name or sender_id),
        text=text,
        group=str(rec.get("group") or group or ""),
    )


# ---------------------------------------------------------------- 各格式解析

def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if enc == "utf-16" and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            continue
        return text
    return raw.decode("utf-8", errors="replace")


def parse_qq_txt(path: str | Path) -> list[Message]:
    """旧版 PC QQ 导出的 txt。"""
    path = Path(path)
    messages: list[Message] = []
    group = path.stem
    current: Message | None = None
    body: list[str] = []

    def flush() -> None:
        nonlocal current
        if current is not None:
            while body and not body[-1].strip():
                body.pop()
            current.text = "\n".join(body).strip()
            messages.append(current)
        current = None
        body.clear()

    for line in _read_text(path).splitlines():
        line = line.rstrip("\r")
        m = _TXT_HEADER.match(line)
        if m:
            flush()
            date, clock, name, qq, email = m.groups()
            try:
                time = datetime.strptime(f"{date} {clock}", "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            name = _TITLE_PREFIX.sub("", name).strip()
            current = Message(time=time, sender_id=qq or email, sender_name=name, text="", group=group)
            continue
        g = _TXT_GROUP.match(line.strip())
        if g:
            flush()
            group = g.group(1).strip()
            continue
        if line.startswith("====="):
            flush()
            continue
        if current is not None:
            body.append(line)
    flush()
    return [m for m in messages if m.sender_id not in SYSTEM_SENDERS]


def _iter_json_records(data: Any) -> Iterator[tuple[dict[str, Any], str]]:
    if isinstance(data, list):
        for rec in data:
            if isinstance(rec, dict):
                yield rec, ""
        return
    if isinstance(data, dict):
        group = ""
        info = _pick(data, "chatInfo", "chat", "group", "meta", "metadata")
        if isinstance(info, dict):
            group = str(_pick(info, "name", "groupName", "title") or "")
        elif isinstance(info, str):
            group = info
        for key in ("messages", "msgs", "data", "records", "list"):
            if isinstance(data.get(key), list):
                for rec in data[key]:
                    if isinstance(rec, dict):
                        yield rec, group
                return


def parse_json(path: str | Path) -> list[Message]:
    path = Path(path)
    text = _read_text(path)
    records: Iterable[tuple[dict[str, Any], str]]
    if path.suffix == ".jsonl":
        records = ((json.loads(line), "") for line in text.splitlines() if line.strip())
    else:
        records = _iter_json_records(json.loads(text))
    group = path.stem
    out = []
    for rec, g in records:
        msg = record_to_message(rec, g or group)
        if msg is not None and msg.sender_id not in SYSTEM_SENDERS:
            out.append(msg)
    return out


def parse_csv(path: str | Path) -> list[Message]:
    path = Path(path)
    reader = csv.DictReader(_read_text(path).splitlines())
    out = []
    for row in reader:
        msg = record_to_message(dict(row), path.stem)
        if msg is not None:
            out.append(msg)
    return out


def parse_file(path: str | Path) -> list[Message]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return parse_qq_txt(path)
    if suffix in (".json", ".jsonl"):
        return parse_json(path)
    if suffix == ".csv":
        return parse_csv(path)
    raise ValueError(f"不支持的文件格式: {path}")


def load_messages(paths: Iterable[str | Path]) -> list[Message]:
    """读取文件或目录（递归），按时间排序并去重。"""
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            files.extend(sorted(f for f in p.rglob("*") if f.suffix.lower() in SUPPORTED_SUFFIXES))
        elif p.exists():
            files.append(p)
        else:
            raise FileNotFoundError(p)
    seen: set[tuple[str, str, str]] = set()
    out: list[Message] = []
    for f in files:
        for m in parse_file(f):
            key = (m.time.isoformat(), m.sender_id, m.text)
            if key in seen:
                continue
            seen.add(key)
            out.append(m)
    out.sort(key=lambda m: m.time)
    return out
