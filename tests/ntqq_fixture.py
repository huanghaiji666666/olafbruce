"""nt_msg.db 测试样本。

tests/data/nt_msg_sample.db 是用真实 SQLCipher 按 NTQQ 参数加密的小库（内容是假的），
用来验证 piao.chatlog.ntqq 的纯 Python 解密。重新生成：

    pip install sqlcipher3-binary
    python -m tests.ntqq_fixture
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

KEY = "#8abcdefghijk@uJ"
FIXTURE = Path(__file__).parent / "data" / "nt_msg_sample.db"
MAIN_GROUP = 829124409
OTHER_GROUP = 123456789
PIAO = 2044715303


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def pb_field(num: int, value: int | bytes | str) -> bytes:
    if isinstance(value, int):
        return _varint(num << 3) + _varint(value)
    if isinstance(value, str):
        value = value.encode("utf-8")
    return _varint(num << 3 | 2) + _varint(len(value)) + value


def segment(typ: int, text: str | None = None) -> bytes:
    body = pb_field(45001, 7_300_000_000_000_000_001) + pb_field(45002, typ)
    if text is not None:
        body += pb_field(45101, text)
    return pb_field(40800, body)


def text_body(text: str) -> bytes:
    return segment(1, text)


# (40001 消息ID, 40003 群内序号, 40013 来源, 40030 群号, 40033 发送者, 40050 时间, 40090 群名片, 40093 昵称, 40800 消息体)
ROWS = [
    (1, 101, 0, MAIN_GROUP, 10001111, 1_700_000_000, "张三", "zs", text_body("飘哥 23466m345p11s5678p 切啥")),
    (2, 102, 0, MAIN_GROUP, PIAO, 1_700_000_015, "", "轻轻的飘", segment(7) + text_body("切5p")),
    (3, 103, 3, 0, 0, 1_700_000_020, "", "", segment(8)),
    (4, 104, 0, MAIN_GROUP, PIAO, 1_700_000_030, "飘", "轻轻的飘", text_body("确实") + segment(2) + segment(6)),
    (5, 201, 0, OTHER_GROUP, 10002222, 1_700_000_040, "李四", "ls", text_body("别的群")),
    (6, 105, 0, MAIN_GROUP, 10001111, 1_700_000_050, "张三", "zs", segment(8)),
]

COLUMNS = ("40001", "40003", "40013", "40030", "40033", "40050", "40090", "40093", "40800")


def fill(conn: sqlite3.Connection) -> None:
    conn.execute(
        'CREATE TABLE group_msg_table ("40001" INTEGER PRIMARY KEY, "40003" INTEGER, "40013" INTEGER, '
        '"40030" INTEGER, "40033" INTEGER, "40050" INTEGER, "40090" TEXT, "40093" TEXT, "40800" BLOB)'
    )
    conn.executemany(f"INSERT INTO group_msg_table VALUES ({','.join('?' * len(COLUMNS))})", ROWS)
    conn.commit()


def main() -> None:
    import sqlcipher3

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    body = FIXTURE.with_suffix(".tmp")
    body.unlink(missing_ok=True)
    conn = sqlcipher3.connect(str(body))
    conn.execute(f"PRAGMA key = '{KEY}'")
    conn.execute("PRAGMA cipher_page_size = 4096")
    conn.execute("PRAGMA kdf_iter = 4000")
    conn.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA1")
    conn.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512")
    fill(conn)
    conn.close()
    header = b"SQLite header 3\x00QQ_NT DB (piao test fixture)".ljust(1024, b"\x00")
    FIXTURE.write_bytes(header + body.read_bytes())
    body.unlink()
    print(f"写出 {FIXTURE}（{FIXTURE.stat().st_size} 字节）")


if __name__ == "__main__":
    main()
