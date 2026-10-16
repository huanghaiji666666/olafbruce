"""命令行入口：piao <命令>。运行 `piao -h` 查看全部命令。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from .config import Config, load_config


def _work(cfg: Config, name: str) -> Path:
    return cfg.work_dir / name


def _need(path: Path, hint: str) -> Path:
    if not path.exists():
        sys.exit(f"找不到 {path}，请先运行 {hint}")
    return path


# ---------------------------------------------------------------- 数据 → Skill

def cmd_ntqq(cfg: Config, args: argparse.Namespace) -> None:
    from .chatlog.ntqq import WrongKeyError, extract_group_chats

    groups = None if args.all_groups else (args.group or [str(g) for g in cfg.bot.groups] or None)
    try:
        counts, bad_pages = extract_group_chats(args.db, args.out, args.key, groups)
    except (WrongKeyError, ValueError, RuntimeError) as e:
        sys.exit(str(e))
    if bad_pages:
        print(f"注意：有 {bad_pages} 页校验失败，库可能有损坏，这部分消息可能缺失或乱码")
    if not counts:
        which = f"群 {', '.join(groups)} 的" if groups else ""
        sys.exit(f"没有提取到{which}消息。确认这个号在群里，或者加 --all-groups 看看库里有哪些群")
    for group, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"群 {group}：{n} 条 → {Path(args.out) / f'ntqq_{group}.jsonl'}")


def cmd_ingest(cfg: Config, args: argparse.Namespace) -> None:
    from collections import Counter

    from .chatlog.clean import clean_messages
    from .chatlog.dataset import build_pairs, write_jsonl
    from .chatlog.parsers import load_messages

    target = args.target or cfg.persona.qq
    raw = load_messages(args.paths)
    msgs = clean_messages(raw)
    pairs = build_pairs(msgs, target)
    n_msg = write_jsonl(_work(cfg, "messages.jsonl"), (m.to_dict() for m in msgs))
    n_pair = write_jsonl(_work(cfg, "pairs.jsonl"), (p.to_dict() for p in pairs))
    n_target = sum(1 for m in msgs if m.sender_id == target)
    print(f"读取 {len(raw)} 条，清洗后 {n_msg} 条；其中 QQ {target} 的发言 {n_target} 条，样本 {n_pair} 组")
    if n_target == 0:
        top = Counter((m.sender_id, m.sender_name) for m in msgs).most_common(10)
        print("没找到目标 QQ 的发言。发言最多的是：")
        for (sid, name), c in top:
            print(f"  {sid}  {name}  {c} 条")
        sys.exit(1)


def cmd_profile(cfg: Config, args: argparse.Namespace) -> None:
    from .chatlog.dataset import load_messages_jsonl, load_pairs_jsonl
    from .persona.profile import compute_profile, render_profile_md

    msgs = load_messages_jsonl(_need(_work(cfg, "messages.jsonl"), "piao ingest"))
    pairs = load_pairs_jsonl(_work(cfg, "pairs.jsonl"))
    profile = compute_profile(msgs, cfg.persona.qq, pairs)
    _work(cfg, "profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    md = render_profile_md(profile)
    _work(cfg, "profile.md").write_text(md + "\n", encoding="utf-8")
    print(md)


def cmd_skill(cfg: Config, args: argparse.Namespace) -> None:
    from .chatlog.dataset import load_pairs_jsonl
    from .persona.skill import build_skill

    profile = json.loads(_need(_work(cfg, "profile.json"), "piao profile").read_text(encoding="utf-8"))
    pairs = load_pairs_jsonl(_work(cfg, "pairs.jsonl"))
    persona_path = Path(cfg.persona.persona_file)
    persona_text = persona_path.read_text(encoding="utf-8") if persona_path.exists() else None
    if persona_text is None:
        print(f"提示：没有 {persona_path}，人设部分会留空。复制 persona/persona.example.md 填写后重新运行效果更好。")
    out = build_skill(
        cfg.persona.skill_dir, cfg.persona.name, cfg.persona.qq, profile, pairs, persona_text, n_examples=args.examples
    )
    print(f"Skill 已生成：{out}/SKILL.md（参考资料在 {out}/references/）")


def cmd_build(cfg: Config, args: argparse.Namespace) -> None:
    cmd_ingest(cfg, args)
    cmd_profile(cfg, args)
    cmd_skill(cfg, args)


def cmd_export_sft(cfg: Config, args: argparse.Namespace) -> None:
    from .chatlog.dataset import export_sft, load_pairs_jsonl
    from .persona.prompt import load_skill_prompt

    pairs = load_pairs_jsonl(_need(_work(cfg, "pairs.jsonl"), "piao ingest"))
    system = load_skill_prompt(cfg.persona.skill_dir, cfg.persona.name) if args.with_skill else (
        f"你是QQ群友「{cfg.persona.name}」，用本人的语气接群聊。"
    )
    out = Path(args.out or _work(cfg, f"sft_{args.format}.jsonl"))
    n = export_sft(pairs, out, cfg.persona.qq, cfg.persona.name, system, fmt=args.format)
    print(f"导出 {n} 条到 {out}")


# ---------------------------------------------------------------- 对话

def _make_responder(cfg: Config):
    from .bot.responder import Responder
    from .chatlog.dataset import load_pairs_jsonl
    from .llm import ClaudeChat
    from .persona.prompt import load_skill_prompt
    from .persona.retriever import PairRetriever

    skill_dir = _need(Path(cfg.persona.skill_dir) / "SKILL.md", "piao build").parent
    system = load_skill_prompt(skill_dir, cfg.persona.name, cfg.bot.self_disclose)
    pairs_path = _work(cfg, "pairs.jsonl")
    retriever = PairRetriever(load_pairs_jsonl(pairs_path)) if pairs_path.exists() else None
    return Responder(ClaudeChat(cfg.llm), system, cfg.persona.name, cfg.persona.qq, retriever)


def cmd_chat(cfg: Config, args: argparse.Namespace) -> None:
    from .chatlog.models import Message
    from .chatlog.parsers import now_china

    responder = _make_responder(cfg)
    history: list[Message] = []
    self_id = "self"
    print(
        f"本地测试：输入消息和{cfg.persona.name}聊天。\n"
        f"  「名字：内容」以别的群友身份发言；以 ~ 开头只加入记录不触发回复；/clear 清空；Ctrl-D 退出"
    )

    async def loop() -> None:
        while True:
            try:
                line = await asyncio.to_thread(input, "> ")
            except EOFError:
                return
            line = line.strip()
            if not line:
                continue
            if line == "/clear":
                history.clear()
                continue
            silent = line.startswith("~")
            line = line.lstrip("~").strip()
            name, text = args.name, line
            for sep in ("：", ":"):
                head, found, rest = line.partition(sep)
                if found and 0 < len(head) <= 12 and " " not in head:
                    name, text = head, rest.strip()
                    break
            history.append(Message(now_china(), name, name, text))
            if silent:
                continue
            recent = history[-cfg.bot.context_messages :]
            lines = await responder.respond(recent, self_id, f"{name} 在跟你说话。", now_china())
            if not lines:
                print("  （已读不回）")
            for r in lines:
                print(f"  {cfg.persona.name}：{'[语音] ' if r.voice else ''}{r.text}")
                history.append(Message(now_china(), self_id, cfg.persona.name, r.text))

    asyncio.run(loop())


def cmd_bot(cfg: Config, args: argparse.Namespace) -> None:
    from .bot.onebot import OneBotClient
    from .bot.runtime import PiaoBot

    if not cfg.bot.groups:
        sys.exit("config.toml 的 [bot] groups 为空：请写上允许机器人说话的群号")
    tts = None
    if cfg.voice.enabled:
        from .voice.tts import GPTSoVITS

        tts = GPTSoVITS(cfg.voice)
    client = OneBotClient(cfg.bot.ws_url, cfg.bot.access_token)
    bot = PiaoBot(cfg, client, _make_responder(cfg), tts)
    print(f"连接 {cfg.bot.ws_url}，生效的群：{cfg.bot.groups}")
    try:
        asyncio.run(client.run_forever(bot.on_event))
    except KeyboardInterrupt:
        pass


def cmd_hand(cfg: Config, args: argparse.Namespace) -> None:
    from .mahjong import analyze_hand

    print(analyze_hand(" ".join(args.hand), melds=args.melds))


# ---------------------------------------------------------------- 语音

def cmd_voice(cfg: Config, args: argparse.Namespace) -> None:
    from .voice import prepare

    if args.voice_cmd == "decode":
        from .voice.audio import batch_voice_to_wav

        out = batch_voice_to_wav(args.src, args.out, args.min_seconds)
        print(f"解码 {len(out)} 条语音到 {args.out}")
    elif args.voice_cmd == "extract":
        out = prepare.extract_audio(args.src, args.out, args.chunk_minutes)
        print(f"抽取 {len(out)} 段音频到 {args.out}")
    elif args.voice_cmd == "msst":
        if not cfg.voice.msst_dir or not cfg.voice.msst_stages:
            sys.exit("请先在 config.toml 的 [voice] 里配置 msst_dir 和 [[voice.msst_stages]]")
        stages = [prepare.MsstStage(**s) for s in cfg.voice.msst_stages]
        final = prepare.run_msst(args.src, args.out, cfg.voice.msst_dir, stages, cfg.voice.msst_python, args.extra)
        print(f"完成，干净人声在 {final}")
    elif args.voice_cmd == "slice":
        out = prepare.slice_audio(args.src, args.out, args.threshold_db, args.min_len, args.max_len)
        print(f"切出 {len(out)} 段到 {args.out}")
    elif args.voice_cmd == "filter":
        scores = prepare.filter_by_speaker(args.clips, args.ref, args.out, args.threshold)
        kept = sum(1 for _, s in scores if s >= args.threshold)
        print(f"保留 {kept}/{len(scores)} 段（阈值 {args.threshold}）")
        if scores:
            sims = sorted(s for _, s in scores)
            q = lambda p: sims[min(len(sims) - 1, int(len(sims) * p))]  # noqa: E731
            print(f"相似度分布：10%={q(0.1):.2f} 50%={q(0.5):.2f} 90%={q(0.9):.2f}，不满意可以调 --threshold")
    elif args.voice_cmd == "asr":
        n = prepare.transcribe(args.clips, args.list, args.speaker or cfg.persona.name, args.engine, args.lang)
        print(f"转写 {n} 条，标注文件：{args.list}")
    elif args.voice_cmd == "say":
        from .voice.tts import GPTSoVITS

        audio = asyncio.run(GPTSoVITS(cfg.voice).synthesize(args.text))
        Path(args.out).write_bytes(audio)
        print(f"已保存 {args.out}")


# ---------------------------------------------------------------- 参数

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="piao", description="把 QQ 群友做成 Skill 和会说话的机器人")
    p.add_argument("-c", "--config", default="config.toml", help="配置文件（默认 config.toml）")
    p.add_argument("-v", "--verbose", action="store_true", help="输出调试日志（含发给模型的提示词）")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_ingest_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("paths", nargs="+", help="聊天记录文件或目录（.txt/.json/.jsonl/.csv）")
        sp.add_argument("--target", help="目标 QQ 号（默认取配置里的 persona.qq）")

    sp = sub.add_parser("ntqq", help="从 QQNT 本地数据库 nt_msg.db 直接提取群聊记录（不需要 NapCat）")
    sp.add_argument("db", help="nt_msg.db（加密原文件，或已经解密的 SQLite）")
    sp.add_argument("--key", default=os.environ.get("NTQQ_DB_KEY"), help="数据库密钥（也可以用环境变量 NTQQ_DB_KEY）")
    sp.add_argument("--group", action="append", help="只提取这个群，可重复；默认取配置里的 bot.groups")
    sp.add_argument("--all-groups", action="store_true", help="提取库里所有群")
    sp.add_argument("--out", default="data/raw/chat", help="输出目录（默认 data/raw/chat）")
    sp.set_defaults(func=cmd_ntqq)

    sp = sub.add_parser("ingest", help="解析并清洗聊天记录")
    add_ingest_args(sp)
    sp.set_defaults(func=cmd_ingest)

    sp = sub.add_parser("profile", help="统计说话风格")
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("skill", help="生成 Skill 目录")
    sp.add_argument("--examples", type=int, default=40, help="写进 Skill 的真实对话样例数量")
    sp.set_defaults(func=cmd_skill)

    sp = sub.add_parser("build", help="ingest + profile + skill 一步完成")
    add_ingest_args(sp)
    sp.add_argument("--examples", type=int, default=40)
    sp.set_defaults(func=cmd_build)

    sp = sub.add_parser("export-sft", help="导出微调数据（LLaMA-Factory sharegpt / OpenAI 格式）")
    sp.add_argument("--format", choices=["sharegpt", "openai"], default="sharegpt")
    sp.add_argument("--out")
    sp.add_argument("--with-skill", action="store_true", help="用完整 Skill 作为 system prompt")
    sp.set_defaults(func=cmd_export_sft)

    sp = sub.add_parser("chat", help="在终端里和分身聊天（不需要 QQ）")
    sp.add_argument("--name", default="群友", help="你在对话里的名字")
    sp.set_defaults(func=cmd_chat)

    sp = sub.add_parser("bot", help="连接 NapCat，在 QQ 群里运行")
    sp.set_defaults(func=cmd_bot)

    sp = sub.add_parser("hand", help="牌效计算，如 piao hand 23466m345p11s5678p")
    sp.add_argument("hand", nargs="+")
    sp.add_argument("--melds", type=int, default=0, help="副露数")
    sp.set_defaults(func=cmd_hand)

    vp = sub.add_parser("voice", help="语音数据处理与合成")
    vp.set_defaults(func=cmd_voice)
    vs = vp.add_subparsers(dest="voice_cmd", required=True)
    s = vs.add_parser("decode", help="QQ 语音（.amr/.silk）→ wav")
    s.add_argument("src")
    s.add_argument("out")
    s.add_argument("--min-seconds", type=float, default=0.8)
    s = vs.add_parser("extract", help="直播录像 → 44.1k wav（按时长切块）")
    s.add_argument("src")
    s.add_argument("out")
    s.add_argument("--chunk-minutes", type=int, default=10)
    s = vs.add_parser("msst", help="用 MSST 分离人声/降噪（步骤在 config.toml 里配置）")
    s.add_argument("src")
    s.add_argument("out")
    s.add_argument("--extra", nargs=argparse.REMAINDER, default=[], help="原样传给 inference.py 的参数")
    s = vs.add_parser("slice", help="按静音切成 3~10 秒的片段")
    s.add_argument("src")
    s.add_argument("out")
    s.add_argument("--threshold-db", type=float, default=-40.0)
    s.add_argument("--min-len", type=float, default=3.0)
    s.add_argument("--max-len", type=float, default=10.0)
    s = vs.add_parser("filter", help="用 QQ 语音做参考，过滤掉不是本人的片段")
    s.add_argument("clips")
    s.add_argument("ref")
    s.add_argument("out")
    s.add_argument("--threshold", type=float, default=0.75)
    s = vs.add_parser("asr", help="转写，生成 GPT-SoVITS 的 .list 标注")
    s.add_argument("clips")
    s.add_argument("list")
    s.add_argument("--engine", choices=["funasr", "whisper"], default="funasr")
    s.add_argument("--lang", default="zh")
    s.add_argument("--speaker")
    s = vs.add_parser("say", help="用 GPT-SoVITS 合成一句话试听")
    s.add_argument("text")
    s.add_argument("-o", "--out", default="say.wav")
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if not args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpx2").setLevel(logging.WARNING)
    cfg = load_config(args.config)
    args.func(cfg, args)


if __name__ == "__main__":
    main()
