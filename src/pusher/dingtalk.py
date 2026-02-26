"""DingTalk Webhook pusher — condensed news digest."""

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
        """Extract key content from report and push condensed digest."""
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
        """Build digest: title + core judgment + top headlines + expert quote + link."""
        lines: list[str] = [f"## {title}", ""]

        # Core judgment
        judgment = self._extract_core_judgment(markdown_text)
        if judgment:
            lines.append(f"> {judgment}")
            lines.append("")

        # Top headlines (max 8), double newline between each for DingTalk rendering
        headlines = self._extract_headlines(markdown_text)
        if headlines:
            for hl in headlines[:8]:
                lines.append(hl)
                lines.append("")

        # Expert insight (1-2 quotes)
        insights = self._extract_insights(markdown_text)
        if insights:
            lines.append("**💬 专家说**")
            lines.append("")
            for ins in insights[:2]:
                lines.append(ins)
            lines.append("")

        if report_url:
            lines.append(f"📖 [查看完整报告]({report_url})")

        return "\n".join(lines)

    @staticmethod
    def _extract_core_judgment(markdown_text: str) -> str:
        """Extract core judgment paragraph, truncate to ~150 chars."""
        lines = markdown_text.split("\n")
        capture = False
        parts: list[str] = []
        for line in lines:
            stripped = line.strip()
            if "核心判断" in stripped:
                # Handle inline format: **核心判断**：text on same line
                m = re.search(r'核心判断\**[：:]\s*(.+)', stripped)
                if m:
                    parts.append(m.group(1))
                    break
                capture = True
                continue
            if capture:
                if stripped.startswith("#") or (not stripped and parts):
                    break
                if stripped:
                    parts.append(stripped)
        text = " ".join(parts)
        if len(text) > 150:
            text = text[:147] + "..."
        return text

    @staticmethod
    def _extract_headlines(markdown_text: str) -> list[str]:
        """Extract news headline lines (emoji-prefixed bold/link titles)."""
        headlines: list[str] = []
        event_pattern = re.compile(
            r'^[•*\-\s]*[🔴🚀🔬💰🔧🤝🌐📜📊📌💡🔥⚡]\s*'
            r'(?:\[?\*\*.*?\*\*\]?)'
        )

        for line in markdown_text.split("\n"):
            stripped = line.strip()
            # Stop before expert section
            if "专家" in stripped and ("视角" in stripped or "##" in stripped):
                break
            if event_pattern.match(stripped):
                headlines.append(stripped)

        return headlines

    @staticmethod
    def _extract_insights(markdown_text: str) -> list[str]:
        """Extract 1-2 expert quotes from insight or debate sections.

        Handles multiple LLM output formats:
        - — [@expert](url)："quote"       (em-dash prefix)
        - → @expert："quote"              (arrow prefix)
        - @expert："quote"                (bare @mention)
        - 代表观点：@expert："quote"       (inline label)
        """
        insights: list[str] = []
        in_expert = False
        # Pattern: line contains @someone followed by quoted text
        quote_pattern = re.compile(
            r'[@＠]\w+.*?[：:]["「"\'](.*?)["\」"\'」]'
        )
        for line in markdown_text.split("\n"):
            stripped = line.strip()
            # Enter expert section
            if "专家" in stripped and ("视角" in stripped or "##" in stripped):
                in_expert = True
                continue
            if not in_expert:
                continue
            # Stop if we hit a new top-level section (but not sub-sections)
            if stripped.startswith("## ") and "专家" not in stripped:
                break
            # Capture quote lines in various formats
            is_quote = (
                stripped.startswith("—") or stripped.startswith("\u2014")  # em-dash
                or stripped.startswith("–")  # en-dash
                or stripped.startswith("→")  # arrow
                or (stripped.startswith("@") or stripped.startswith("[@"))  # bare @mention
            )
            if is_quote and quote_pattern.search(stripped):
                # Normalize to — prefix for consistent DingTalk display
                normalized = stripped
                if not (normalized.startswith("—") or normalized.startswith("\u2014")):
                    # Strip leading markers (→, –, -, •, etc.)
                    normalized = re.sub(r'^[→–\-•*\s]+', '', normalized).strip()
                    normalized = f"— {normalized}"
                insights.append(normalized)
                if len(insights) >= 2:
                    break
        return insights

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
