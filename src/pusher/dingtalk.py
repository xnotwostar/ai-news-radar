"""DingTalk Webhook pusher with message chunking."""

from __future__ import annotations

import logging
import os
import re
import time

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

MAX_CHUNK_SIZE = 6000  # DingTalk markdown limit ~6000-8000 chars, use conservative
RETRY_ATTEMPTS = 3
RETRY_WAIT = 5  # seconds


class DingTalkPusher:
    """Push Markdown messages to DingTalk via Webhook, auto-chunking long messages.

    Supports multiple webhooks via comma-separated URLs in a single env var, e.g.:
        DINGTALK_GLOBAL_WEBHOOK=https://...token1,https://...token2
    """

    def __init__(self, webhook_url: str | None = None, webhook_env: str | None = None):
        if webhook_url:
            raw = webhook_url
        elif webhook_env:
            raw = os.environ[webhook_env]
        else:
            raise ValueError("Must provide webhook_url or webhook_env")
        # Split on comma or newline, strip whitespace/newlines from each URL
        self.webhook_urls = [
            u.strip() for u in re.split(r'[,\n\r]+', raw) if u.strip()
        ]

    def push(self, title: str, markdown_text: str, report_url: str | None = None) -> bool:
        """Push report to all configured webhooks."""
        success = True
        for idx, url in enumerate(self.webhook_urls):
            if idx > 0:
                time.sleep(2)  # Rate limit between groups
            ok = self._push_single_webhook(url, title, markdown_text, report_url)
            if not ok:
                success = False
        return success

    def _push_single_webhook(
        self, webhook_url: str, title: str, markdown_text: str, report_url: str | None,
    ) -> bool:
        """Push report to a single webhook."""
        if report_url:
            return self._push_action_card(webhook_url, title, markdown_text, report_url)

        chunks = self._split_chunks(markdown_text)
        logger.info("Pushing '%s' in %d chunk(s) → %s...%s", title, len(chunks), webhook_url[:50], webhook_url[-8:])

        success = True
        for i, chunk in enumerate(chunks):
            chunk_title = title if len(chunks) == 1 else f"{title} ({i+1}/{len(chunks)})"
            try:
                self._send_single(webhook_url, chunk_title, chunk)
                if i < len(chunks) - 1:
                    time.sleep(2)
            except Exception as e:
                logger.error("Failed to push chunk %d/%d: %s", i+1, len(chunks), e)
                success = False

        return success

    def push_action_card(self, title: str, markdown_text: str, report_url: str) -> bool:
        """Push actionCard to all configured webhooks."""
        success = True
        for idx, url in enumerate(self.webhook_urls):
            if idx > 0:
                time.sleep(2)
            ok = self._push_action_card(url, title, markdown_text, report_url)
            if not ok:
                success = False
        return success

    def _push_action_card(self, webhook_url: str, title: str, markdown_text: str, report_url: str) -> bool:
        """Push DingTalk actionCard with a button linking to the full HTML report."""
        summary = self._extract_core_judgment(markdown_text)
        event_count = len(re.findall(r'^[🔴🚀🔬💰🔧🤝🌐📜📊📌💡]\s*\*\*', markdown_text, re.MULTILINE))

        card_text = f"## {title}\n\n{summary}\n\n📊 共 {event_count} 条事件"
        payload = {
            "msgtype": "actionCard",
            "actionCard": {
                "title": title,
                "text": card_text,
                "btnOrientation": "0",
                "singleTitle": "阅读完整报告 →",
                "singleURL": report_url,
            },
        }
        try:
            resp = httpx.post(webhook_url, json=payload, timeout=10)
            resp.raise_for_status()
            result = resp.json()
            if result.get("errcode", 0) != 0:
                raise RuntimeError(f"DingTalk API error: {result}")
            logger.info("Sent actionCard '%s' → %s", title, report_url)
            return True
        except Exception as e:
            logger.error("Failed to push actionCard: %s", e)
            return False

    @staticmethod
    def _extract_core_judgment(markdown_text: str) -> str:
        """Extract the core judgment section (first ~200 chars after 核心判断)."""
        lines = markdown_text.split("\n")
        capture = False
        parts: list[str] = []
        for line in lines:
            if "核心判断" in line:
                capture = True
                continue
            if capture:
                if line.strip().startswith("## ") or line.strip().startswith("# "):
                    break
                if line.strip():
                    parts.append(line.strip())
        text = " ".join(parts)
        return text[:200] + "..." if len(text) > 200 else text

    @retry(stop=stop_after_attempt(RETRY_ATTEMPTS), wait=wait_fixed(RETRY_WAIT))
    def _send_single(self, webhook_url: str, title: str, text: str) -> None:
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": text,
            },
        }
        resp = httpx.post(webhook_url, json=payload, timeout=10)
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
