"""直接从 QQNT 本地数据库 nt_msg.db 提取群聊记录（不需要 NapCat）。

nt_msg.db = 1024 字节 NTQQ 文件头 + SQLCipher 4 加密的 SQLite，参数：
cipher_page_size 4096、kdf_iter 4000、HMAC_SHA1、PBKDF2_HMAC_SHA512。
密钥是 16 位字符串，要在登录 QQ 时从进程里取（见 README）。
表名和字段名都是数字，含义参考 QQBackup/nt_msg_db_util 的 db_docs/。
"""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import IO

from .models import Message
from .parsers import to_datetime

NT_HEADER_SIZE = 1024
PAGE_SIZE = 4096
KDF_ITER = 4000
SALT_SIZE = 16
KEY_SIZE = 32
IV_SIZE = 16
HMAC_SIZE = 20  # HMAC_SHA1
RESERVE_SIZE = 48  # IV + HMAC，向上取整到 AES 块大小
SQLITE_MAGIC = b"SQLite format 3\x00"


class WrongKeyError(ValueError):
    pass


# ---------------------------------------------------------------- 解密

def is_plain_sqlite(path: str | Path) -> bool:
    with open(path, "rb") as f:
        return f.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC


def _header_size(path: Path) -> int:
    """原始 nt_msg.db 带 1024 字节文件头；已经剥过头的文件大小是页大小的整数倍。"""
    return NT_HEADER_SIZE if path.stat().st_size % PAGE_SIZE == NT_HEADER_SIZE else 0


def _derive_keys(passphrase: bytes, salt: bytes) -> tuple[bytes, bytes]:
    key = hashlib.pbkdf2_hmac("sha512", passphrase, salt, KDF_ITER, KEY_SIZE)
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", key, mac_salt, 2, KEY_SIZE)
    return key, mac_key


def decrypt_db(src: str | Path, dst: str | Path, key: str) -> int:
    """解密 nt_msg.db 为普通 SQLite 文件，返回校验失败（可能损坏）的页数。"""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as e:
        raise RuntimeError('解密需要 cryptography：pip install -e ".[ntqq]"') from e

    src = Path(src)
    bad_pages = 0
    with open(src, "rb") as f, open(dst, "wb") as out:
        f.seek(_header_size(src))
        page = f.read(PAGE_SIZE)
        if len(page) < PAGE_SIZE:
            raise ValueError(f"{src} 太小，不像 nt_msg.db")
        salt = page[:SALT_SIZE]
        enc_key, mac_key = _derive_keys(key.encode("utf-8"), salt)
        aes = algorithms.AES(enc_key)
        pgno = 1
        while len(page) == PAGE_SIZE:
            start = SALT_SIZE if pgno == 1 else 0
            end = PAGE_SIZE - RESERVE_SIZE
            iv = page[end : end + IV_SIZE]
            mac = hmac.new(mac_key, page[start : end + IV_SIZE] + pgno.to_bytes(4, "little"), "sha1").digest()
            if not hmac.compare_digest(mac, page[end + IV_SIZE : end + IV_SIZE + HMAC_SIZE]):
                if pgno == 1:
                    raise WrongKeyError("密钥不对（第 1 页校验失败），或者这不是 nt_msg.db")
                bad_pages += 1
            dec = Cipher(aes, modes.CBC(iv)).decryptor()
            plain = dec.update(page[start:end]) + dec.finalize()
            out.write((SQLITE_MAGIC if pgno == 1 else b"") + plain + page[end:])
            page = f.read(PAGE_SIZE)
            pgno += 1
    return bad_pages


# ---------------------------------------------------------------- 消息体（40800，Protobuf）

def _varint(buf: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if i >= len(buf):
            raise ValueError("varint 截断")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _fields(buf: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """Protobuf wire 格式 → (字段号, wire type, 值)。不需要 .proto。"""
    i = 0
    while i < len(buf):
        tag, i = _varint(buf, i)
        num, wt = tag >> 3, tag & 7
        val: int | bytes
        if wt == 0:
            val, i = _varint(buf, i)
        elif wt == 1:
            val, i = buf[i : i + 8], i + 8
        elif wt == 2:
            n, i = _varint(buf, i)
            val, i = buf[i : i + n], i + n
        elif wt == 5:
            val, i = buf[i : i + 4], i + 4
        else:
            raise ValueError(f"不支持的 wire type {wt}")
        if i > len(buf):
            raise ValueError("字段截断")
        yield num, wt, val


# 消息段类型（MsgContent.45002）→ 占位符，和 parsers.SEGMENT_PLACEHOLDER 保持一致
SEGMENT_PLACEHOLDER = {
    2: "[图片]",
    3: "[文件]",
    4: "[语音]",
    5: "[视频]",
    6: "[表情]",
    9: "[红包]",
    10: "[卡片]",
    11: "[表情]",
    14: "[卡片]",
    16: "[聊天记录]",
    21: "[通话]",
}
SEG_TEXT, SEG_REPLY, SEG_GRAY_TIP = 1, 7, 8


def body_to_text(blob: bytes | None) -> str:
    """40800 消息体 → 文本。引用块和系统灰条不输出，解析失败的部分丢弃。"""
    if not blob:
        return ""
    out: list[str] = []
    try:
        for num, wt, seg in _fields(blob):
            if num != 40800 or wt != 2 or not isinstance(seg, bytes):
                continue
            fields: dict[int, int | bytes] = {}
            for n, _, v in _fields(seg):
                fields.setdefault(n, v)
            typ = fields.get(45002)
            if not isinstance(typ, int) or typ in (SEG_REPLY, SEG_GRAY_TIP):
                continue
            text = fields.get(45101)
            if typ == SEG_TEXT and isinstance(text, bytes):
                out.append(text.decode("utf-8", errors="replace"))
            else:
                out.append(SEGMENT_PLACEHOLDER.get(typ, ""))
    except ValueError:
        pass
    return "".join(out).strip()


# ---------------------------------------------------------------- 读取群消息

# group_msg_table 用到的字段
COL_TIME = "40050"  # 发送时间（Unix 秒）
COL_GROUP = "40030"  # 群号
COL_SENDER = "40033"  # 发送者 QQ
COL_CARD = "40090"  # 群名片
COL_NICK = "40093"  # 昵称（没有群名片时）
COL_BODY = "40800"  # 消息体
COL_SOURCE = "40013"  # 0 他人，1/2 自己，3 系统
COL_SEQ = "40003"  # 群内消息序号
SOURCE_SYSTEM = 3


def iter_group_messages(db: str | Path, groups: Iterable[str] | None = None) -> Iterator[Message]:
    """按时间顺序读取明文 nt_msg 库里的群消息；groups 为空时读取全部群。"""
    conn = sqlite3.connect(str(db))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(group_msg_table)")}
        if not cols:
            raise ValueError(f"{db} 里没有 group_msg_table，确认是 nt_msg.db")

        def col(name: str) -> str:
            return f'"{name}"' if name in cols else "NULL"

        sql = (
            f"SELECT {col(COL_TIME)}, {col(COL_GROUP)}, {col(COL_SENDER)}, {col(COL_CARD)}, "
            f"{col(COL_NICK)}, {col(COL_BODY)}, {col(COL_SOURCE)} FROM group_msg_table"
        )
        group_list = [str(g) for g in groups or []]
        if group_list:
            sql += f" WHERE CAST({col(COL_GROUP)} AS TEXT) IN ({','.join('?' * len(group_list))})"
        sql += f" ORDER BY {col(COL_TIME)}, {col(COL_SEQ)}"
        for ts, group, sender, card, nick, body, source in conn.execute(sql, group_list):
            time = to_datetime(ts)
            if time is None or not sender or source == SOURCE_SYSTEM:
                continue
            text = body_to_text(body)
            if not text:
                continue
            yield Message(time, str(sender), card or nick or str(sender), text, str(group))
    finally:
        conn.close()


def _write_group_files(messages: Iterable[Message], out_dir: Path) -> dict[str, int]:
    import json

    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, IO[str]] = {}
    counts: dict[str, int] = {}
    try:
        for m in messages:
            if m.group not in files:
                files[m.group] = open(out_dir / f"ntqq_{m.group}.jsonl", "w", encoding="utf-8")
                counts[m.group] = 0
            files[m.group].write(json.dumps(m.to_dict(), ensure_ascii=False) + "\n")
            counts[m.group] += 1
    finally:
        for fh in files.values():
            fh.close()
    return counts


def extract_group_chats(
    src: str | Path, out_dir: str | Path, key: str | None = None, groups: Iterable[str] | None = None
) -> tuple[dict[str, int], int]:
    """nt_msg.db（加密或已解密）→ out_dir/ntqq_<群号>.jsonl。返回 ({群号: 条数}, 损坏页数)。"""
    src = Path(src)
    if is_plain_sqlite(src):
        return _write_group_files(iter_group_messages(src, groups), Path(out_dir)), 0
    if not key:
        raise ValueError(f"{src} 是加密的，需要提供密钥")
    with tempfile.TemporaryDirectory(prefix="piao_ntqq_", ignore_cleanup_errors=True) as tmp:
        plain = Path(tmp) / "nt_msg_plain.db"
        bad_pages = decrypt_db(src, plain, key)
        return _write_group_files(iter_group_messages(plain, groups), Path(out_dir)), bad_pages
