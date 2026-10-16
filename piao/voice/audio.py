"""格式转换：QQ 语音（SILK / AMR）→ wav，wav → QQ 语音 SILK。"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

SILK_MAGIC = b"#!SILK_V3"
AMR_MAGIC = b"#!AMR"
VOICE_SUFFIXES = {".amr", ".silk", ".slk", ".aud", ".ptt", ".mp3", ".m4a", ".ogg", ".wav"}


def require_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("找不到 ffmpeg，请先安装并加入 PATH")
    return exe


def detect_format(path: str | Path) -> str:
    """QQ 的语音文件扩展名经常是 .amr，但内容其实是腾讯版 SILK（开头多一个 0x02）。"""
    head = Path(path).read_bytes()[:16]
    if head.startswith(SILK_MAGIC) or head[1:].startswith(SILK_MAGIC):
        return "silk"
    if head.startswith(AMR_MAGIC):
        return "amr"
    return "other"


def _write_wav(pcm: bytes, dst: Path, rate: int) -> None:
    with wave.open(str(dst), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm)


def voice_to_wav(src: str | Path, dst: str | Path, rate: int = 24000) -> float:
    """把一条 QQ 语音转成单声道 wav，返回时长（秒）。"""
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if detect_format(src) == "silk":
        import pilk

        with tempfile.TemporaryDirectory() as tmp:
            pcm_path = Path(tmp) / "out.pcm"
            pilk.decode(str(src), str(pcm_path), pcm_rate=rate)
            pcm = pcm_path.read_bytes()
        _write_wav(pcm, dst, rate)
        return len(pcm) / 2 / rate
    subprocess.run(
        [require_ffmpeg(), "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", str(rate), str(dst)],
        check=True,
    )
    with wave.open(str(dst), "rb") as f:
        return f.getnframes() / f.getframerate()


def batch_voice_to_wav(src_dir: str | Path, out_dir: str | Path, min_seconds: float = 0.8) -> list[Path]:
    """批量解码，太短的（“嗯”“啊”）跳过。"""
    out = []
    for src in sorted(Path(src_dir).rglob("*")):
        if not src.is_file() or src.suffix.lower() not in VOICE_SUFFIXES:
            continue
        dst = Path(out_dir) / (src.stem + ".wav")
        try:
            dur = voice_to_wav(src, dst)
        except Exception as e:  # 个别损坏文件不影响整体
            print(f"跳过 {src.name}: {e}")
            continue
        if dur < min_seconds:
            dst.unlink(missing_ok=True)
            continue
        out.append(dst)
    return out


def wav_to_silk(wav_bytes: bytes, rate: int = 24000) -> bytes:
    """任意音频 → 腾讯版 SILK（QQ 语音消息格式）。"""
    import pilk

    pcm = subprocess.run(
        [require_ffmpeg(), "-loglevel", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", str(rate), "pipe:1"],
        input=wav_bytes,
        capture_output=True,
        check=True,
    ).stdout
    with tempfile.TemporaryDirectory() as tmp:
        pcm_path, silk_path = Path(tmp) / "in.pcm", Path(tmp) / "out.silk"
        pcm_path.write_bytes(pcm)
        pilk.encode(str(pcm_path), str(silk_path), pcm_rate=rate, tencent=True)
        return silk_path.read_bytes()
