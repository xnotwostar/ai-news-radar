"""DingTalk Webhook pusher — condensed news titles only."""

from __future__ import annotations

import logging
import os
import re
import time

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

RETRY_ATTEMPTS = 3
RETRY_WAIT = 5  # seconds


class DingTalkPusher:
    """Push condensed news digest to DingTalk via Webhook.

    Supports multiple webhooks via comma-separated URLs in a single env var.
    """

    def __init__(self, webhook_url: str | None = None, webhook_env: str | None = None):
        if webhook_url:
            raw = webhook_url
        elif webhook_env:
            raw = os.environ[webhook_env]
        else:
            raise ValueError("Must provide webhook_url or webhook_env")
        self.webhook_urls = [
            u.strip() for u in re.split(r'[,\n\r]+', raw) if u.strip()
        ]

    def push(
        self,
        title: str,
        markdown_text: str,
        report_url: str | None = None,
    ) -> bool:
        """Extract headlines from report and push condensed digest."""
        digest = self._build_digest(title, markdown_text, report_url)
        success = True
        for idx, url in enumerate(self.webhook_urls):
            if idx > 0:
                time.sleep(2)
            try:
                self._send_markdown(url, title, digest)
            except Exception as e:
                logger.error("DingTalk push failed for webhook %d: %s", idx + 1, e)
                success = False
        return success

    def _build_digest(
        self,
        title: str,
        markdown_text: str,
        report_url: str | None,
    ) -> str:
        """Build a condensed digest: title + headline list + report link."""
        headlines = self._extract_headlines(markdown_text)
        lines: list[str] = [f"## {title}", ""]

        if headlines:
            for hl in headlines:
                lines.append(hl)
            lines.append("")
        else:
            lines.append("暂无新闻条目")
            lines.append("")

        if report_url:
            lines.append("---")
            lines.append("")
            lines.append(f"> [📖 查看完整报告]({report_url})")

        return "\n".join(lines)

    @staticmethod
    def _extract_headlines(markdown_text: str) -> list[str]:
        """Extract news headline lines from the report markdown.

        Matches lines starting with emoji markers like:
        🔴 **title** / 🚀 [**title**](url) / • 🔬 title
        """
        headlines: list[str] = []
        # Match main event lines: emoji (optionally after •) followed by bold or link
        event_pattern = re.compile(
            r'^[•\s]*[🔴🚀🔬💰🔧🤝🌐📜📊📌💡🔥⚡]\s*'
            r'(?:\[?\*\*.*?\*\*\]?)'
        )
        # Match speed-read bullet lines: • emoji one-liner
        speed_pattern = re.compile(
            r'^[•]\s*[🔴🚀🔬💰🔧🤝🌐📜📊📌💡🔥⚡]\s*.+'
        )

        in_speed_section = False
        for line in markdown_text.split("\n"):
            stripped = line.strip()

            # Detect speed-read section
            if "速览" in stripped:
                in_speed_section = True
                continue
            # Stop at next major section
            if in_speed_section and stripped.startswith("## "):
                in_speed_section = False

            if in_speed_section and speed_pattern.match(stripped):
                headlines.append(stripped)
                continue

            if event_pattern.match(stripped):
                # Keep only the first line (title), strip analysis
                headlines.append(stripped)

        return headlines

    @retry(stop=stop_after_attempt(RETRY_ATTEMPTS), wait=wait_fixed(RETRY_WAIT))
    def _send_markdown(self, webhook_url: str, title: str, text: str) -> None:
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
        logger.info("Sent digest '%s' → %s...%s", title, webhook_url[:50], webhook_url[-8:])
