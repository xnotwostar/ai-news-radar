"""Server酱 (ServerChan) pusher — push reports to WeChat via sctapi.ftqq.com."""

from __future__ import annotations

import logging
import os

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

MAX_TITLE_LEN = 32
RETRY_ATTEMPTS = 3
RETRY_WAIT = 5  # seconds


class ServerChanPusher:
    """Push Markdown messages to WeChat via Server酱 (ServerChan) API."""

    API_URL = "https://sctapi.ftqq.com/{key}.send"

    def __init__(self, send_key: str | None = None, send_key_env: str | None = None):
        if send_key:
            self._key = send_key
        elif send_key_env:
            self._key = os.environ[send_key_env]
        else:
            raise ValueError("Must provide send_key or send_key_env")

    def push(self, title: str, markdown_text: str, report_url: str | None = None) -> bool:
        """Push report to WeChat. If report_url is provided, append link at the top."""
        # Server酱 title 限制 32 字符
        short_title = title[:MAX_TITLE_LEN] if len(title) > MAX_TITLE_LEN else title

        body = markdown_text
        if report_url:
            body = f"[阅读完整报告 →]({report_url})\n\n{body}"

        try:
            self._send(short_title, body)
            logger.info("ServerChan push succeeded: '%s'", short_title)
            return True
        except Exception as e:
            logger.error("ServerChan push failed: %s", e)
            return False

    @retry(stop=stop_after_attempt(RETRY_ATTEMPTS), wait=wait_fixed(RETRY_WAIT))
    def _send(self, title: str, desp: str) -> None:
        url = self.API_URL.format(key=self._key)
        payload = {"title": title, "desp": desp}
        resp = httpx.post(url, data=payload, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 0:
            raise RuntimeError(f"ServerChan API error: {result}")
        logger.debug("ServerChan sent '%s' successfully", title)
