"""ServerChan (Server酱) pusher for WeChat notifications."""

from __future__ import annotations

import logging
import os

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

SCTAPI_URL = "https://sctapi.ftqq.com/{key}.send"
RETRY_ATTEMPTS = 3
RETRY_WAIT = 5


class ServerChanPusher:
    """Push Markdown messages to WeChat via ServerChan (Server酱)."""

    def __init__(self, send_key: str | None = None, key_env: str | None = None):
        if send_key:
            self.send_key = send_key
        elif key_env:
            self.send_key = os.environ.get(key_env, "")
        else:
            raise ValueError("Must provide send_key or key_env")

        if not self.send_key:
            raise ValueError(f"ServerChan key is empty (env: {key_env})")

    def push(self, title: str, markdown_text: str, report_url: str | None = None) -> bool:
        """Push report to WeChat via ServerChan."""
        desp = markdown_text
        if report_url:
            desp += f"\n\n---\n[阅读完整报告]({report_url})"

        try:
            self._send(title, desp)
            logger.info("ServerChan push succeeded: '%s'", title)
            return True
        except Exception as e:
            logger.error("ServerChan push failed: %s", e)
            return False

    @retry(stop=stop_after_attempt(RETRY_ATTEMPTS), wait=wait_fixed(RETRY_WAIT))
    def _send(self, title: str, desp: str) -> None:
        url = SCTAPI_URL.format(key=self.send_key)
        resp = httpx.post(
            url,
            data={"title": title, "desp": desp},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("code", 0) != 0:
            raise RuntimeError(f"ServerChan API error: {result}")
