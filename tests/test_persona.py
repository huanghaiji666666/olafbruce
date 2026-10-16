from datetime import datetime

from piao.chatlog.dataset import build_pairs
from piao.chatlog.models import Message
from piao.persona.profile import compute_profile, distinctive_ngrams, render_profile_md
from piao.persona.prompt import (
    PASS_TOKEN,
    build_system_prompt,
    build_user_turn,
    format_live,
    parse_reply,
)
from piao.persona.retriever import PairRetriever, tokenize
from piao.persona.skill import build_skill

from .conftest import TARGET, synthetic_chat


def test_profile_finds_catchphrases():
    msgs = synthetic_chat()
    profile = compute_profile(msgs, TARGET, build_pairs(msgs, TARGET))
    phrases = [c["text"] for c in profile["catchphrases"]]
    assert "确实" in phrases
    assert any("绷不住" in p for p in phrases)
    assert "天凤" not in phrases  # 别人说的不算
    top = {c["text"] for c in profile["top_messages"]}
    assert {"确实", "6", "推"} <= top
    assert profile["names"] == ["轻轻的飘"]
    md = render_profile_md(profile)
    assert "口癖" in md and "确实" in md


def test_distinctive_ngrams_without_baseline():
    out = distinctive_ngrams(["确实确实", "确实"] * 5, [], min_count=2)
    assert out[0][0].startswith("确实")


def test_tokenize_keeps_tiles():
    toks = tokenize("飘哥 5p 切不切[图片]")
    assert "5p" in toks and "飘哥" in toks and "图片" not in "".join(toks)


def test_retriever_finds_similar_context():
    msgs = synthetic_chat()
    r = PairRetriever(build_pairs(msgs, TARGET))
    assert len(r) > 50
    hits = r.search("立直被一发了", k=3)
    assert hits and any("一发" in t.text for t in hits[0].context)
    assert len({h.reply.text for h in hits}) == len(hits)  # 回复去重
    assert r.sample(5) == r.sample(5)  # 固定种子


def test_parse_reply():
    assert parse_reply(PASS_TOKEN, "轻轻的飘") == []
    assert parse_reply("  ", "轻轻的飘") == []
    lines = parse_reply("轻轻的飘：切5p\n\n- 没什么好说的\n[语音]晚安", "轻轻的飘")
    assert [(r.text, r.voice) for r in lines] == [("切5p", False), ("没什么好说的", False), ("晚安", True)]
    assert parse_reply("8.5w分", "轻轻的飘")[0].text == "8.5w分"
    assert len(parse_reply("\n".join("abcdef"), "x")) == 4


def test_system_prompt_strips_frontmatter():
    skill = "---\nname: x\ndescription: y\n---\n\n# 飘\n正文"
    s = build_system_prompt(skill, "轻轻的飘", self_disclose=True, extra_references=["# 样例\n飘：确实"])
    assert not s.startswith("---")
    assert "# 飘" in s and "飘：确实" in s and "AI分身" in s
    assert "AI分身" not in build_system_prompt(skill, "轻轻的飘", self_disclose=False)


def test_user_turn_layout():
    t = datetime(2026, 10, 16, 21, 15)
    history = [
        Message(t, "1", "张三（@你）", "飘哥 这牌切啥"),
        Message(t, "self", "轻轻的飘", "切5p"),
    ]
    transcript = format_live(history, "self", "轻轻的飘")
    assert "[21:15] 张三（@你）：飘哥 这牌切啥" in transcript
    assert "轻轻的飘（你）：切5p" in transcript
    msgs = synthetic_chat(50)
    pairs = build_pairs(msgs, TARGET)
    turn = build_user_turn(transcript, t, "张三 @了你。", pairs[:2], TARGET, "轻轻的飘", mahjong="当前听牌")
    assert turn.index("<相似场景>") < turn.index("<牌效计算>") < turn.index("<群聊>")
    assert "周五" in turn and "飘：" in turn


def test_build_skill(tmp_path):
    msgs = synthetic_chat()
    pairs = build_pairs(msgs, TARGET)
    profile = compute_profile(msgs, TARGET, pairs)
    persona = "<!-- 注释 -->\n- 天凤九段，门清立直派"
    out = build_skill(tmp_path / "qingqingdepiao", "轻轻的飘", TARGET, profile, pairs, persona, n_examples=10)
    skill_md = (out / "SKILL.md").read_text(encoding="utf-8")
    assert skill_md.startswith("---\nname: qingqingdepiao\n")
    assert "门清立直派" in skill_md and "注释" not in skill_md and "{{" not in skill_md
    examples = (out / "references" / "examples.md").read_text(encoding="utf-8")
    assert examples.count("\n---\n") == 9
    assert "飘：" in examples
    assert (out / "references" / "style.md").exists()
