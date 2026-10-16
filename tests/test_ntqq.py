import sqlite3

import pytest

from piao.chatlog.ntqq import WrongKeyError, body_to_text, decrypt_db, extract_group_chats
from piao.chatlog.parsers import load_messages
from piao.cli import main

from .conftest import TARGET
from .ntqq_fixture import FIXTURE, KEY, MAIN_GROUP, OTHER_GROUP, fill, pb_field, segment, text_body


def test_body_to_text():
    assert body_to_text(text_body("确实")) == "确实"
    assert body_to_text(segment(7) + text_body("切5p")) == "切5p"  # 引用块不输出
    assert body_to_text(text_body("绷不住了") + segment(2) + segment(6)) == "绷不住了[图片][表情]"
    assert body_to_text(segment(8)) == ""  # 系统灰条
    assert body_to_text(pb_field(40800, 1)) == ""  # 旧 varint 形态
    assert body_to_text(text_body("确实")[:-2]) == ""  # 截断
    assert body_to_text(None) == ""


def _check_main_group(out_dir):
    msgs = load_messages([out_dir])
    assert [m.text for m in msgs] == ["飘哥 23466m345p11s5678p 切啥", "切5p", "确实[图片][表情]"]
    assert [m.sender_name for m in msgs] == ["张三", "轻轻的飘", "飘"]  # 没有群名片时用昵称
    assert msgs[1].sender_id == TARGET
    assert {m.group for m in msgs} == {str(MAIN_GROUP)}
    assert msgs[0].time.isoformat() == "2023-11-15T06:13:20"  # 北京时间


def test_extract_plain_db(tmp_path):
    db = tmp_path / "nt_msg_plain.db"
    conn = sqlite3.connect(db)
    fill(conn)
    conn.close()
    counts, bad = extract_group_chats(db, tmp_path / "out", groups=[str(MAIN_GROUP)])
    assert counts == {str(MAIN_GROUP): 3} and bad == 0
    _check_main_group(tmp_path / "out")

    counts, _ = extract_group_chats(db, tmp_path / "all")
    assert counts == {str(MAIN_GROUP): 3, str(OTHER_GROUP): 1}


def test_decrypt_real_sqlcipher_fixture(tmp_path):
    pytest.importorskip("cryptography")
    assert decrypt_db(FIXTURE, tmp_path / "plain.db", KEY) == 0
    assert sqlite3.connect(tmp_path / "plain.db").execute("PRAGMA integrity_check").fetchone() == ("ok",)

    with pytest.raises(WrongKeyError):
        decrypt_db(FIXTURE, tmp_path / "x.db", "wrong-key")
    with pytest.raises(ValueError, match="密钥"):
        extract_group_chats(FIXTURE, tmp_path / "out")


def test_cli_ntqq(tmp_path, capsys):
    pytest.importorskip("cryptography")
    out = tmp_path / "chat"
    main(["-c", str(tmp_path / "none.toml"), "ntqq", str(FIXTURE), "--key", KEY, "--group", str(MAIN_GROUP), "--out", str(out)])
    assert "3 条" in capsys.readouterr().out
    _check_main_group(out)

    with pytest.raises(SystemExit, match="密钥不对"):
        main(["-c", str(tmp_path / "none.toml"), "ntqq", str(FIXTURE), "--key", "nope", "--out", str(out)])
