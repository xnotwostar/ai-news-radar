"""Historical event deduplication — remove events already covered in recent days."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import jieba

from ..schemas import EventCard

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# High-frequency / generic words to ignore during keyword matching
_DEDUP_STOPWORDS = {
    "的", "了", "在", "是", "和", "与", "对", "于", "将", "为", "被",
    "AI", "人工智能", "大模型", "LLM", "发布", "宣布", "推出",
    "表示", "称", "说", "指出", "认为", "公司", "技术", "平台",
    "全球", "中国", "新", "正式", "重大", "最新",
}

# Emoji pattern to strip from titles before keyword extraction
_EMOJI_RE = re.compile(
    r'[\U0001F300-\U0001FAFF\u2600-\u27BF\u2702-\u27B0\u231A-\u2B55]+\s*'
)


class HistoryDeduplicator:
    """Compare today's events against recent historical events and remove duplicates."""

    def __init__(self, lookback_days: int = 3, threshold: int = 2):
        self.lookback_days = lookback_days
        self.threshold = threshold

    def deduplicate(
        self,
        events: list[EventCard],
        pipeline_name: str,
        date_str: str,
    ) -> list[EventCard]:
        """Remove events whose titles overlap with recent history by ≥threshold keywords."""
        historical_titles = self._load_history(pipeline_name, date_str)
        if not historical_titles:
            logger.info("History dedup: no historical events found, skipping")
            return events

        hist_kw_list = [self._extract_keywords(t) for t in historical_titles]

        unique: list[EventCard] = []
        removed: list[str] = []
        for event in events:
            event_kw = self._extract_keywords(event.title)
            is_dup = any(
                len(event_kw & hkw) >= self.threshold for hkw in hist_kw_list
            )
            if is_dup:
                removed.append(event.title)
            else:
                unique.append(event)

        if removed:
            logger.info(
                "History dedup: %d → %d events (removed %d duplicates)",
                len(events), len(unique), len(removed),
            )
            for t in removed:
                logger.debug("  removed: %s", t)
        else:
            logger.info("History dedup: %d events, no duplicates found", len(events))

        return unique

    def _load_history(self, pipeline_name: str, date_str: str) -> list[str]:
        """Load event titles from recent days' event files."""
        try:
            current_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return []

        titles: list[str] = []
        events_dir = DATA_DIR / "events"
        if not events_dir.exists():
            return titles

        for days_ago in range(1, self.lookback_days + 1):
            past_date = current_date - timedelta(days=days_ago)
            past_str = past_date.strftime("%Y-%m-%d")
            path = events_dir / f"{past_str}_{pipeline_name}_events.json"
            if not path.exists():
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                for item in data:
                    if isinstance(item, dict) and "title" in item:
                        titles.append(item["title"])
                logger.info("Loaded %d historical events from %s", len(data), path.name)
            except Exception as e:
                logger.warning("Failed to load %s: %s", path.name, e)

        return titles

    @staticmethod
    def _extract_keywords(text: str) -> set[str]:
        """Extract substantive keywords via jieba, stripping emoji and stopwords."""
        clean = _EMOJI_RE.sub("", text)
        words = jieba.cut(clean)
        return {w for w in words if len(w) >= 2 and w not in _DEDUP_STOPWORDS}
