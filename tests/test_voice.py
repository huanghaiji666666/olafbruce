import shutil
import wave

import pytest

np = pytest.importorskip("numpy")
sf = pytest.importorskip("soundfile")

from piao.voice.prepare import _collect_stem, slice_audio  # noqa: E402


def _tone(seconds, sr=16000, freq=220.0):
    t = np.arange(int(seconds * sr)) / sr
    return 0.3 * np.sin(2 * np.pi * freq * t)


def test_slice_audio(tmp_path):
    sr = 16000
    silence = np.zeros(int(0.8 * sr))
    # 4 秒、1.5 秒、25 秒（要被切开）、0.3 秒（太短被丢）
    audio = np.concatenate([_tone(4), silence, _tone(1.5), silence, _tone(25), silence, _tone(0.3), silence])
    src = tmp_path / "live.wav"
    sf.write(str(src), audio, sr)
    clips = slice_audio(src, tmp_path / "out", min_len=3, max_len=10)
    durations = [sf.info(str(c)).duration for c in clips]
    assert all(d <= 10.5 for d in durations)
    assert all(d >= 1.0 for d in durations)
    assert 4 <= len(clips) <= 6
    assert abs(sum(durations) - 30.5) < 3


def test_collect_stem_handles_both_layouts(tmp_path):
    raw = tmp_path / "raw"
    (raw / "b").mkdir(parents=True)
    for p in (raw / "a_vocals.wav", raw / "a_other.wav", raw / "b" / "vocals.wav", raw / "b" / "other.wav"):
        sf.write(str(p), np.zeros(100), 16000)
    out = _collect_stem(raw, "vocals", tmp_path / "kept")
    assert sorted(p.name for p in out) == ["a.wav", "b.wav"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_silk_roundtrip(tmp_path):
    pytest.importorskip("pilk")
    from piao.voice.audio import detect_format, voice_to_wav, wav_to_silk

    src = tmp_path / "a.wav"
    sf.write(str(src), _tone(1.0, sr=24000), 24000)
    silk = wav_to_silk(src.read_bytes())
    assert silk.startswith(b"\x02#!SILK_V3")  # 腾讯版 SILK
    qq_voice = tmp_path / "voice.amr"  # QQ 的语音经常是 .amr 扩展名
    qq_voice.write_bytes(silk)
    assert detect_format(qq_voice) == "silk"
    dur = voice_to_wav(qq_voice, tmp_path / "back.wav")
    assert abs(dur - 1.0) < 0.1
    with wave.open(str(tmp_path / "back.wav")) as f:
        assert f.getframerate() == 24000 and f.getnchannels() == 1
