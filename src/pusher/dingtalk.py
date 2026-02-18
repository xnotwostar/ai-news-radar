"""DingTalk Webhook pusher with message chunking."""

from __future__ import annotations

import logging
import os
import time

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

MAX_CHUNK_SIZE = 6000  # DingTalk markdown limit ~6000-8000 chars, use conservative
RETRY_ATTEMPTS = 3
RETRY_WAIT = 5  # seconds


class DingTalkPusher:
    """Push Markdown messages to DingTalk via Webhook, auto-chunking long messages."""

    def __init__(self, webhook_url: str | None = None, webhook_env: str | None = None):
        if webhook_url:
            self.webhook_url = webhook_url
        elif webhook_env:
            self.webhook_url = os.environ[webhook_env]
        else:
            raise ValueError("Must provide webhook_url or webhook_env")

    def push(self, title: str, markdown_text: str) -> bool:
        """Push markdown report. Auto-chunks if too long. Returns True on success."""
        chunks = self._split_chunks(markdown_text)
        logger.info("Pushing '%s' in %d chunk(s)", title, len(chunks))

        success = True
        for i, chunk in enumerate(chunks):
            chunk_title = title if len(chunks) == 1 else f"{title} ({i+1}/{len(chunks)})"
            try:
                self._send_single(chunk_title, chunk)
                if i < len(chunks) - 1:
                    time.sleep(2)  # Rate limit between chunks
            except Exception as e:
                logger.error("Failed to push chunk %d/%d: %s", i+1, len(chunks), e)
                success = False

        return success

    @retry(stop=stop_after_attempt(RETRY_ATTEMPTS), wait=wait_fixed(RETRY_WAIT))
    def _send_single(self, title: str, text: str) -> None:
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": text,
            },
        }
        resp = httpx.post(self.webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("errcode", 0) != 0:
            raise RuntimeError(f"DingTalk API error: {result}")
        logger.debug("Sent chunk '%s' successfully", title)

    @staticmethod
    def _split_chunks(text: str, max_size: int = MAX_CHUNK_SIZE) -> list[str]:
        """Split markdown by sections (##) to stay under max_size."""
        if len(text) <= max_size:
            return [text]

        sections = text.split("\n## ")
        chunks: list[str] = []
        current = ""

        for i, section in enumerate(sections):
            piece = section if i == 0 else f"## {section}"

            if len(current) + len(piece) + 1 > max_size:
                if current:
                    chunks.append(current.strip())
                current = piece
            else:
                current = f"{current}\n{piece}" if current else piece

        if current.strip():
            chunks.append(current.strip())

        # Safety: if any chunk still too long, hard-split
        final: list[str] = []
        for chunk in chunks:
            while len(chunk) > max_size:
                split_at = chunk.rfind("\n", 0, max_size)
                if split_at <= 0:
                    split_at = max_size
                final.append(chunk[:split_at])
                chunk = chunk[split_at:].lstrip("\n")
            if chunk:
                final.append(chunk)

        return final
