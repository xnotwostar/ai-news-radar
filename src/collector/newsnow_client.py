"""Newsnow trending topics collector."""

from __future__ import annotations

import logging
import re

import httpx

from ..schemas import TrendingItem

logger = logging.getLogger(__name__)

NEWSNOW_API_URL = "https://newsnow.busiyi.world/api"
REQUEST_TIMEOUT = 30


class NewsnowCollector:
    """Fetch trending topics from newsnow aggregator and filter by keywords."""

    def __init__(self, keywords: list[str] | None = None):
        self.keywords = keywords or ["AI", "人工智能", "大模型", "芯片", "GPU", "算力", "机器人"]
        self._pattern = re.compile("|".join(re.escape(k) for k in self.keywords), re.IGNORECASE)

    def collect(self) -> list[TrendingItem]:
        """Fetch and filter trending items."""
        logger.info("Fetching newsnow trending data...")

        all_items: list[TrendingItem] = []
        try:
            resp = httpx.get(NEWSNOW_API_URL, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            all_items = self._parse_response(data)
        except Exception as e:
            logger.error("Failed to fetch newsnow API: %s", e)
            # Try fallback endpoint
            try:
                resp = httpx.get(f"{NEWSNOW_API_URL}/hot", timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                all_items = self._parse_response(data)
            except Exception as e2:
                logger.error("Fallback also failed: %s", e2)
                return []

        filtered = [item for item in all_items if self._matches(item.title)]
        logger.info("Filtered %d AI-related items from %d total", len(filtered), len(all_items))
        return filtered

    def _parse_response(self, data: dict | list) -> list[TrendingItem]:
        items: list[TrendingItem] = []

        # newsnow returns different structures depending on endpoint
        if isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            entries = data.get("data", data.get("items", []))
            # Might be nested by platform
            if isinstance(entries, dict):
                flat: list = []
                for platform, platform_items in entries.items():
                    if isinstance(platform_items, list):
                        for it in platform_items:
                            if isinstance(it, dict):
                                it.setdefault("platform", platform)
                                flat.append(it)
                entries = flat
        else:
            return items

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            title = entry.get("title", entry.get("name", ""))
            if not title:
                continue
            items.append(TrendingItem(
                title=title,
                url=entry.get("url", entry.get("link", "")),
                platform=entry.get("platform", entry.get("source", "")),
                rank=int(entry.get("rank", entry.get("index", 0))),
                hot_value=entry.get("hotValue", entry.get("hot", None)),
            ))

        return items

    def _matches(self, text: str) -> bool:
        return bool(self._pattern.search(text))
