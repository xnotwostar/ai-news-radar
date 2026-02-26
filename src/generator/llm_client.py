"""Unified LLM client with Fallback Chain: Claude → Qwen-Plus → DeepSeek."""

from __future__ import annotations

import logging
import os
from typing import Sequence

import anthropic
import httpx

from ..schemas import LLMModelEntry

logger = logging.getLogger(__name__)

GOOGLE_CHAT_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
DASHSCOPE_CHAT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
DEEPSEEK_CHAT_URL = "https://api.deepseek.com/v1/chat/completions"


class LLMClient:
    """Unified LLM caller with automatic fallback chain."""

    def __init__(self, chain: Sequence[LLMModelEntry]):
        self.chain = sorted(chain, key=lambda m: m.priority)

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.5,
        max_tokens: int = 8192,
    ) -> str:
        """Try each model in the fallback chain until one succeeds."""
        last_error: Exception | None = None

        for entry in self.chain:
            try:
                logger.info("Trying %s/%s (priority %d)", entry.provider, entry.model, entry.priority)
                result = self._call(entry, system_prompt, user_prompt, temperature, max_tokens)
                logger.info("Success with %s/%s (%d chars)", entry.provider, entry.model, len(result))
                return result
            except Exception as e:
                logger.warning("Failed %s/%s: %s", entry.provider, entry.model, e)
                last_error = e
                continue

        raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")

    def _call(
        self,
        entry: LLMModelEntry,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        if entry.provider == "anthropic":
            return self._call_anthropic(entry, system_prompt, user_prompt, temperature, max_tokens)
        elif entry.provider == "google":
            return self._call_openai_compat(
                entry, GOOGLE_CHAT_URL, "GOOGLE_API_KEY",
                system_prompt, user_prompt, temperature, max_tokens,
            )
        elif entry.provider == "dashscope":
            return self._call_openai_compat(
                entry, DASHSCOPE_CHAT_URL, "DASHSCOPE_API_KEY",
                system_prompt, user_prompt, temperature, max_tokens,
            )
        elif entry.provider == "deepseek":
            return self._call_openai_compat(
                entry, DEEPSEEK_CHAT_URL, "DEEPSEEK_API_KEY",
                system_prompt, user_prompt, temperature, max_tokens,
            )
        else:
            raise ValueError(f"Unknown provider: {entry.provider}")

    @staticmethod
    def _call_anthropic(
        entry: LLMModelEntry,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        client = anthropic.Anthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            timeout=httpx.Timeout(entry.timeout, connect=30.0),
        )
        message = client.messages.create(
            model=entry.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=temperature,
        )
        return message.content[0].text

    @staticmethod
    def _call_openai_compat(
        entry: LLMModelEntry,
        base_url: str,
        api_key_env: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        api_key = os.environ[api_key_env]
        resp = httpx.post(
            base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": entry.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=httpx.Timeout(entry.timeout, connect=30.0),
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
