"""读取 config.toml。所有字段都有默认值，缺什么补什么。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any


@dataclass
class PersonaConfig:
    name: str = "轻轻的飘"
    qq: str = "2044715303"
    aliases: list[str] = field(default_factory=lambda: ["飘", "飘飘", "轻轻的飘"])
    skill_dir: str = "skill/qingqingdepiao"
    persona_file: str = "persona/persona.md"


@dataclass
class DataConfig:
    work_dir: str = "data/work"


@dataclass
class LLMConfig:
    model: str = "claude-opus-5-5"
    effort: str = "low"
    max_tokens: int = 8000
    fallbacks: bool = True


@dataclass
class BotConfig:
    ws_url: str = "ws://127.0.0.1:3001"
    access_token: str = ""
    groups: list[int] = field(default_factory=list)
    admins: list[int] = field(default_factory=list)
    ignore_users: list[int] = field(default_factory=list)
    self_disclose: bool = True
    reply_on_at: bool = True
    reply_on_alias: bool = True
    alias_reply_prob: float = 0.85
    random_reply_prob: float = 0.03
    cooldown_seconds: float = 25.0
    max_replies_per_minute: int = 6
    debounce_seconds: float = 2.5
    context_messages: int = 40
    typing_cps: float = 7.0
    quote_trigger: bool = True


@dataclass
class VoiceConfig:
    enabled: bool = False
    api_url: str = "http://127.0.0.1:9880"
    ref_audio: str = ""
    ref_text: str = ""
    ref_lang: str = "zh"
    text_lang: str = "zh"
    probability: float = 0.08
    max_chars: int = 40
    encode_silk: bool = True
    # 直播素材降噪：Music-Source-Separation-Training 仓库路径、它的 Python 解释器、分离步骤
    msst_dir: str = ""
    msst_python: str = "python"
    msst_stages: list[dict[str, str]] = field(default_factory=list)


@dataclass
class Config:
    persona: PersonaConfig = field(default_factory=PersonaConfig)
    data: DataConfig = field(default_factory=DataConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    bot: BotConfig = field(default_factory=BotConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)

    @property
    def work_dir(self) -> Path:
        return Path(self.data.work_dir)


def _fill(cls: type, raw: dict[str, Any]) -> Any:
    known = {f.name for f in fields(cls)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"[{cls.__name__}] 未知配置项: {', '.join(sorted(unknown))}")
    return cls(**raw)


def load_config(path: str | Path | None = "config.toml") -> Config:
    """读取配置；文件不存在时返回默认配置。"""
    if path is None or not Path(path).exists():
        return Config()
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return Config(
        persona=_fill(PersonaConfig, raw.get("persona", {})),
        data=_fill(DataConfig, raw.get("data", {})),
        llm=_fill(LLMConfig, raw.get("llm", {})),
        bot=_fill(BotConfig, raw.get("bot", {})),
        voice=_fill(VoiceConfig, raw.get("voice", {})),
    )
