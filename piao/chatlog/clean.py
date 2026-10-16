"""清洗：统一占位符、脱敏、过滤系统消息。"""

from __future__ import annotations

import re

from .models import Message

# CQ 码（go-cqhttp / NapCat 的字符串消息格式）
_CQ = re.compile(r"\[CQ:(\w+)(?:,[^\]]*)?\]")
_CQ_PLACEHOLDER = {
    "image": "[图片]",
    "face": "[表情]",
    "mface": "[表情]",
    "record": "[语音]",
    "video": "[视频]",
    "file": "[文件]",
    "forward": "[聊天记录]",
    "json": "[卡片]",
    "xml": "[卡片]",
    "at": "@某人 ",
    "reply": "",
}
# 旧版 QQ 导出的各种占位
_PLACEHOLDER_ALIASES = {
    "[动画表情]": "[表情]",
    "[闪照]": "[图片]",
    "[QQ红包]": "[红包]",
    "[语音消息]": "[语音]",
    "[视频消息]": "[视频]",
}

_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID_CARD = re.compile(r"(?<![\dA-Za-z])\d{17}[\dXx](?![\dA-Za-z])")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_URL = re.compile(r"(?:https?://|www\.)[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")

_SYSTEM_PATTERNS = [
    re.compile(p)
    for p in (
        r"撤回了一条(成员)?消息",
        r"加入(了)?本群",
        r"退出(了)?(本)?群",
        r"被管理员禁言",
        r"拍了拍",
        r"^\[戳一戳\]$",
        r"邀请.+加入了群聊",
        r"修改了群名称",
        r"成为了?管理员",
        r"^请使用最新版(本)?(手机)?QQ",
    )
]
_ONLY_PLACEHOLDERS = re.compile(r"^(\s|\[[^\[\]]{1,6}\])*$")


def normalize_text(text: str) -> str:
    text = _CQ.sub(lambda m: _CQ_PLACEHOLDER.get(m.group(1), ""), text)
    for old, new in _PLACEHOLDER_ALIASES.items():
        text = text.replace(old, new)
    text = text.replace("​", "").replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def redact(text: str) -> str:
    """去掉手机号、身份证、邮箱、链接，避免模型学到并复述隐私。"""
    text = _URL.sub("[链接]", text)
    text = _EMAIL.sub("[邮箱]", text)
    text = _ID_CARD.sub("[证件号]", text)
    text = _PHONE.sub("[手机号]", text)
    return text


def is_system(text: str) -> bool:
    return any(p.search(text) for p in _SYSTEM_PATTERNS)


def is_placeholder_only(text: str) -> bool:
    return bool(_ONLY_PLACEHOLDERS.match(text))


def clean_messages(messages: list[Message], keep_placeholder_only: bool = True) -> list[Message]:
    """清洗消息列表。只有占位符（比如只发了张图）的消息默认保留，它们也是说话习惯的一部分。"""
    out = []
    for m in messages:
        text = redact(normalize_text(m.text))
        if not text or is_system(text):
            continue
        if not keep_placeholder_only and is_placeholder_only(text):
            continue
        out.append(Message(m.time, m.sender_id, m.sender_name, text, m.group))
    return out
