"""调用 Claude 生成回复。

每次回复是一次无状态请求：system = 人设（固定，开 prompt cache），user = 群聊记录 + 检索样例。
凭据从 ANTHROPIC_API_KEY 读取；自定义 API 地址用 ANTHROPIC_BASE_URL。
"""

from __future__ import annotations

import logging
from typing import Protocol

import anthropic

from .config import LLMConfig

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ChatModel(Protocol):
    async def complete(self, system: str, user: str) -> str: ...


class ClaudeChat:
    def __init__(self, cfg: LLMConfig, client: anthropic.AsyncAnthropic | None = None):
        self.cfg = cfg
        self.client = client or anthropic.AsyncAnthropic()
        # Haiku 没有服务端 fallback
        self.use_fallbacks = cfg.fallbacks and not cfg.model.startswith("claude-haiku")

    async def complete(self, system: str, user: str) -> str:
        params = dict(
            model=self.cfg.model,
            max_tokens=self.cfg.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": self.cfg.effort},
        )
        try:
            if self.use_fallbacks:
                resp = await self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)
            else:
                resp = await self.client.messages.create(**params)
        except anthropic.RateLimitError:
            log.warning("Claude API 限流，这次不回复")
            return ""
        except anthropic.APIStatusError as e:
            log.error("Claude API 错误 %s: %s", e.status_code, e.message)
            return ""
        except anthropic.APIConnectionError as e:
            log.error("连不上 Claude API: %s", e)
            return ""

        if resp.stop_reason == "refusal":
            log.info("模型拒绝回复（%s），这次保持沉默", getattr(resp.stop_details, "category", None))
            return ""
        if resp.stop_reason == "max_tokens":
            log.warning("回复被 max_tokens 截断，丢弃")
            return ""
        u = resp.usage
        log.debug(
            "tokens: in=%s cache_read=%s cache_write=%s out=%s",
            u.input_tokens, u.cache_read_input_tokens, u.cache_creation_input_tokens, u.output_tokens,
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()
