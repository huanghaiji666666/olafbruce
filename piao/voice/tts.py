"""GPT-SoVITS 合成（调用它自带的 api_v2.py）。"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import urllib.error
import urllib.request

from ..config import VoiceConfig
from .audio import wav_to_silk

log = logging.getLogger(__name__)


class GPTSoVITS:
    def __init__(self, cfg: VoiceConfig, timeout: float = 60.0):
        self.cfg = cfg
        self.timeout = timeout

    def _request(self, text: str) -> bytes:
        body = {
            "text": text,
            "text_lang": self.cfg.text_lang,
            "ref_audio_path": self.cfg.ref_audio,
            "prompt_text": self.cfg.ref_text,
            "prompt_lang": self.cfg.ref_lang,
            "text_split_method": "cut5",
            "media_type": "wav",
            "streaming_mode": False,
        }
        req = urllib.request.Request(
            self.cfg.api_url.rstrip("/") + "/tts",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"GPT-SoVITS 合成失败: {e.read()[:300]!r}") from e

    async def synthesize(self, text: str) -> bytes:
        """返回 wav 字节。"""
        return await asyncio.to_thread(self._request, text)

    async def record_segment(self, text: str) -> dict:
        """合成并包装成 OneBot 的语音消息段。"""
        audio = await self.synthesize(text)
        if self.cfg.encode_silk:
            audio = await asyncio.to_thread(wav_to_silk, audio)
        return {"type": "record", "data": {"file": "base64://" + base64.b64encode(audio).decode()}}
