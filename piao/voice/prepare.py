"""直播录像 → GPT-SoVITS 训练集。

1. extract   ffmpeg 抽音轨，按 10 分钟切块（MSST 处理几个小时的整段很吃显存/内存）
2. msst      调用 Music-Source-Separation-Training 的 inference.py，逐级分离：人声 → 去噪 → 去混响
3. slice     按静音切成 3~10 秒的小段
4. filter    用 QQ 语音（一定是本人）做参考，过滤掉队友/观众连麦/游戏角色语音
5. asr       转写，生成 GPT-SoVITS 的 .list 标注文件
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .audio import require_ffmpeg

AUDIO_SUFFIXES = {".wav", ".flac", ".mp3", ".m4a", ".ogg"}
VIDEO_SUFFIXES = {".flv", ".mp4", ".mkv", ".ts", ".mov", ".webm", ".m4a", ".aac", ".mp3", ".wav"}


def _files(path: str | Path, suffixes: set[str]) -> list[Path]:
    path = Path(path)
    if path.is_file():
        return [path]
    return sorted(f for f in path.rglob("*") if f.is_file() and f.suffix.lower() in suffixes)


# ---------------------------------------------------------------- 1. 抽音轨

def extract_audio(src: str | Path, out_dir: str | Path, chunk_minutes: int = 10) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    for video in _files(src, VIDEO_SUFFIXES):
        pattern = out_dir / f"{video.stem}_%03d.wav"
        subprocess.run(
            [
                require_ffmpeg(), "-y", "-loglevel", "error", "-i", str(video), "-vn",
                "-ac", "2", "-ar", "44100",  # MSST 的模型基本都是 44.1k 立体声训练的
                "-f", "segment", "-segment_time", str(chunk_minutes * 60), str(pattern),
            ],
            check=True,
        )
        made += sorted(out_dir.glob(f"{video.stem}_*.wav"))
    return made


# ---------------------------------------------------------------- 2. MSST

@dataclass
class MsstStage:
    model_type: str
    config: str
    checkpoint: str
    keep: str  # 保留哪个输出（模型 config 里 instruments 的名字，如 vocals / dry / noreverb）


def _collect_stem(store_dir: Path, keep: str, dest: Path) -> list[Path]:
    """不同版本 MSST 的输出命名不一样（{name}_{stem}.wav 或 {name}/{stem}.wav），这里都兼容。"""
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(store_dir.rglob("*")):
        if f.suffix.lower() not in AUDIO_SUFFIXES:
            continue
        if f.stem.endswith("_" + keep):
            name = f.stem[: -len(keep) - 1]
        elif f.stem == keep and f.parent != store_dir:
            name = f.parent.name
        else:
            continue
        target = dest / f"{name}{f.suffix}"
        shutil.copy2(f, target)
        out.append(target)
    return out


def run_msst(
    input_dir: str | Path,
    out_dir: str | Path,
    msst_dir: str | Path,
    stages: list[MsstStage],
    python: str = sys.executable,
    extra_args: list[str] | None = None,
) -> Path:
    """逐级跑 MSST，返回最后一级保留下来的音频所在目录。"""
    msst_dir, out_dir = Path(msst_dir), Path(out_dir)
    if not (msst_dir / "inference.py").exists():
        raise FileNotFoundError(f"{msst_dir} 里没有 inference.py，请指向 Music-Source-Separation-Training 仓库")
    current = Path(input_dir)
    for i, st in enumerate(stages, 1):
        raw = out_dir / f"stage{i}_raw"
        kept = out_dir / f"stage{i}_{st.keep}"
        cmd = [
            python, "inference.py",
            "--model_type", st.model_type,
            "--config_path", st.config,
            "--start_check_point", st.checkpoint,
            "--input_folder", str(current.resolve()),
            "--store_dir", str(raw.resolve()),
            *(extra_args or []),
        ]
        print(f"[MSST {i}/{len(stages)}] {st.model_type} → 保留 {st.keep}")
        subprocess.run(cmd, cwd=msst_dir, check=True)
        if not _collect_stem(raw, st.keep, kept):
            raise RuntimeError(f"第 {i} 级没有找到名为 {st.keep} 的输出，检查 keep 是否和模型 config 的 instruments 一致")
        current = kept
    return current


# ---------------------------------------------------------------- 3. 切片

def slice_audio(
    src: str | Path,
    out_dir: str | Path,
    threshold_db: float = -40.0,
    min_len: float = 3.0,
    max_len: float = 10.0,
    min_silence: float = 0.3,
    keep_silence: float = 0.15,
    hop: float = 0.01,
) -> list[Path]:
    """按静音切片，片段控制在 [min_len, max_len] 秒（GPT-SoVITS 推荐 3~10 秒），输出单声道。"""
    import numpy as np
    import soundfile as sf

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    for f in _files(src, AUDIO_SUFFIXES):
        audio, sr = sf.read(str(f), dtype="float32", always_2d=True)
        audio = audio.mean(axis=1)
        for idx, (a, b) in enumerate(_split_points(audio, sr, threshold_db, min_len, max_len, min_silence, keep_silence, hop)):
            clip = audio[a:b]
            peak = float(np.abs(clip).max()) or 1.0
            clip = clip * min(0.95 / peak, 4.0)  # 峰值归一化，最多放大 4 倍
            path = out_dir / f"{f.stem}_{idx:04d}.wav"
            sf.write(str(path), clip, sr, subtype="PCM_16")
            made.append(path)
    return made


def _split_points(audio, sr, threshold_db, min_len, max_len, min_silence, keep_silence, hop):
    import numpy as np

    h = max(1, int(sr * hop))
    n_frames = len(audio) // h
    if n_frames == 0:
        return []
    frames = audio[: n_frames * h].reshape(n_frames, h)
    rms_db = 20 * np.log10(np.sqrt((frames**2).mean(axis=1)) + 1e-9)
    voiced = rms_db > threshold_db

    # 找出有声音的区段，静音短于 min_silence 的视为同一段
    segments: list[list[int]] = []
    gap_frames = int(min_silence / hop)
    start = last = None
    for i, v in enumerate(voiced):
        if not v:
            continue
        if start is None:
            start = last = i
        elif i - last > gap_frames:
            segments.append([start, last + 1])
            start = i
        last = i
    if start is not None:
        segments.append([start, last + 1])

    # 太短的段与后面的合并（不超过 max_len）
    max_f, min_f = int(max_len / hop), int(min_len / hop)
    merged: list[list[int]] = []
    for seg in segments:
        if merged and merged[-1][1] - merged[-1][0] < min_f and seg[1] - merged[-1][0] <= max_f:
            merged[-1][1] = seg[1]
        else:
            merged.append(seg)

    # 太长的段在 [min_len, max_len] 范围里能量最低处切开（几处差不多低时取最靠后的，让片段尽量长）
    final: list[tuple[int, int]] = []
    for a, b in merged:
        while b - a > max_f:
            window = rms_db[a + min_f : a + max_f]
            quiet = np.flatnonzero(window <= window.min() + 3.0)
            cut = a + min_f + int(quiet[-1])
            final.append((a, cut))
            a = cut
        final.append((a, b))

    pad = int(keep_silence / hop)
    out = []
    for a, b in final:
        if b - a < int(1.0 / hop):  # 不到 1 秒的丢掉
            continue
        out.append((max(0, a - pad) * h, min(n_frames, b + pad) * h))
    return out


# ---------------------------------------------------------------- 4. 说话人过滤

def filter_by_speaker(
    clips_dir: str | Path, ref_dir: str | Path, out_dir: str | Path, threshold: float = 0.75
) -> list[tuple[Path, float]]:
    """用参考音频（QQ 语音解码出来的 wav）算声纹，把相似度低于阈值的切片丢掉。
    需要 `pip install resemblyzer`。返回所有切片及其相似度，方便调阈值。"""
    import numpy as np
    from resemblyzer import VoiceEncoder, preprocess_wav

    encoder = VoiceEncoder()
    refs = [preprocess_wav(p) for p in _files(ref_dir, AUDIO_SUFFIXES)]
    refs = [r for r in refs if len(r) > 16000]
    if not refs:
        raise ValueError(f"{ref_dir} 里没有可用的参考音频")
    ref_embed = encoder.embed_speaker(refs)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    scores = []
    for clip in _files(clips_dir, AUDIO_SUFFIXES):
        wav = preprocess_wav(clip)
        if len(wav) < 8000:
            continue
        sim = float(np.dot(encoder.embed_utterance(wav), ref_embed))
        scores.append((clip, sim))
        if sim >= threshold:
            shutil.copy2(clip, out_dir / clip.name)
    return scores


# ---------------------------------------------------------------- 5. 转写

def transcribe(
    clips_dir: str | Path, list_path: str | Path, speaker: str, engine: str = "funasr", lang: str = "zh"
) -> int:
    """生成 GPT-SoVITS 的标注文件：每行 `音频绝对路径|说话人|语言|文本`。"""
    clips = _files(clips_dir, AUDIO_SUFFIXES)
    if engine == "funasr":
        from funasr import AutoModel

        model = AutoModel(model="paraformer-zh", vad_model="fsmn-vad", punc_model="ct-punc", disable_update=True)

        def run(p: Path) -> str:
            res = model.generate(input=str(p))
            return res[0]["text"] if res else ""
    elif engine == "whisper":
        from faster_whisper import WhisperModel

        model = WhisperModel("large-v3", device="auto")

        def run(p: Path) -> str:
            segs, _ = model.transcribe(str(p), language=lang, initial_prompt="以下是普通话的句子。")
            return "".join(s.text for s in segs)
    else:
        raise ValueError(f"未知的 ASR 引擎: {engine}")

    list_path = Path(list_path)
    list_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with list_path.open("w", encoding="utf-8") as f:
        for clip in clips:
            text = run(clip).strip()
            if len(text) < 2:
                continue
            f.write(f"{clip.resolve()}|{speaker}|{lang.upper()}|{text}\n")
            n += 1
    return n
