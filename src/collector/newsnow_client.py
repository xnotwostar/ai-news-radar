"""Newsnow trending topics collector."""

from __future__ import annotations

import logging
import re

import httpx

from ..schemas import TrendingItem

logger = logging.getLogger(__name__)

NEWSNOW_API_URL = "https://newsnow.busiyi.world/api/s"
REQUEST_TIMEOUT = 30
SOURCES = ["weibo", "zhihu", "baidu", "douyin", "toutiao", "36kr", "ithome", "wallstreetcn"]
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://newsnow.busiyi.world/",
    "Accept": "application/json",
}


class NewsnowCollector:
    """Fetch trending topics from newsnow aggregator and filter by keywords."""

    def __init__(self, keywords: list[str] | None = None):
        self.keywords = keywords or ["AI", "人工智能", "大模型", "芯片", "GPU", "算力", "机器人"]
        self._pattern = re.compile("|".join(re.escape(k) for k in self.keywords), re.IGNORECASE)

    def collect(self) -> list[TrendingItem]:
        """Fetch and filter trending items from all sources."""
        logger.info("Fetching newsnow trending data from %d sources...", len(SOURCES))

        all_items: list[TrendingItem] = []
        for source_id in SOURCES:
            try:
                resp = httpx.get(
                    NEWSNOW_API_URL,
                    params={"id": source_id},
                    headers=REQUEST_HEADERS,
                    timeout=REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()
                items = self._parse_response(data, source_id)
                all_items.extend(items)
                logger.debug("Fetched %d items from %s", len(items), source_id)
            except Exception as e:
                logger.warning("Failed to fetch source %s: %s", source_id, e)
                continue

        filtered = [item for item in all_items if self._matches(item.title)]
        logger.info("Filtered %d AI-related items from %d total", len(filtered), len(all_items))
        return filtered

    def _parse_response(self, data: dict | list, source_id: str) -> list[TrendingItem]:
        items: list[TrendingItem] = []

        if isinstance(data, dict):
            entries = data.get("items", data.get("data", []))
        elif isinstance(data, list):
            entries = data
        else:
            return items

        if not isinstance(entries, list):
            return items

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            title = entry.get("title", "")
            if not title:
                continue
            items.append(TrendingItem(
                title=title,
                url=entry.get("url", entry.get("mobileUrl", "")),
                platform=source_id,
            ))

        return items

    def _matches(self, text: str) -> bool:
        return bool(self._pattern.search(text))
