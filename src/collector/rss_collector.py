"""RSS feed collector for global & China AI news.

Sources of feeds:
- ``config/changelog_feeds.yaml`` — T0 official changelogs + T1 high-density blogs
- ``config/newsletters.yaml``    — T1 human-curated newsletters via Kill the Newsletter
- Built-in ``RSS_FEEDS`` / ``CN_RSS_FEEDS`` dicts — legacy mainstream tech media (T2/T3)

Each item is tagged with ``tier`` / ``weight`` / ``category`` so downstream
ranker can weight authoritative sources higher than commentary aggregators.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import feedparser
import yaml

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


class RssItem:
    """Single item from an RSS feed, with provenance metadata."""

    __slots__ = ("title", "summary", "url", "source", "published",
                 "tier", "weight", "category", "_keyword_filter")

    def __init__(
        self,
        title: str,
        summary: str = "",
        url: str = "",
        source: str = "",
        published: Optional[datetime] = None,
        tier: str = "T3",
        weight: float = 1.0,
        category: str = "general",
    ):
        self.title = title
        self.summary = summary
        self.url = url
        self.source = source
        self.published = published
        self.tier = tier
        self.weight = weight
        self.category = category
        self._keyword_filter = True


AI_KEYWORDS = [
    "AI", "artificial intelligence", "machine learning", "deep learning",
    "LLM", "GPT", "Claude", "Gemini", "OpenAI", "Anthropic", "DeepMind",
    "neural network", "transformer", "diffusion", "generative",
    "chatbot", "copilot", "agent", "AGI", "foundation model",
    "NVIDIA", "GPU", "chip", "semiconductor", "compute",
    "robotics", "autonomous", "self-driving",
    "hugging face", "fine-tuning", "RAG", "vector",
    "Meta AI", "Mistral", "Llama", "Stable Diffusion", "Midjourney",
    "Sora", "Gemma", "DeepSeek", "Qwen", "Grok",
    "embedding", "inference", "training", "benchmark",
    "regulation", "safety", "alignment", "hallucination",
    "open source", "model", "parameter", "token",
]

RSS_FEEDS = {
    # Mainstream tech media
    "TechCrunch": "https://techcrunch.com/feed/",
    "The Verge": "https://www.theverge.com/rss/index.xml",
    "VentureBeat": "https://venturebeat.com/category/ai/feed/",
    "MIT Tech Review": "https://cdn.technologyreview.com/rss/",
    "Ars Technica": "http://feeds.arstechnica.com/arstechnica/index/",
    "Wired": "https://www.wired.com/feed/rss",
    "CNET": "https://www.cnet.com/rss/news/",
    # AI company blogs
    "OpenAI Blog": "https://openai.com/blog/rss/",
    "Google DeepMind": "https://deepmind.google/blog/feed/",
    "Hugging Face": "https://huggingface.co/blog/feed.xml",
    "NVIDIA Blog": "https://developer.nvidia.com/blog/feed/",
    # Community and research
    "Hacker News AI": "https://hnrss.org/newest?q=AI+OR+LLM+OR+GPT&points=100",
    "KDnuggets": "https://www.kdnuggets.com/feed",
    "MarkTechPost": "https://www.marktechpost.com/feed",
    "Lobsters AI": "https://lobste.rs/t/ai.rss",
    "Reddit ML": "https://www.reddit.com/r/MachineLearning/.rss",
    "Latent Space": "https://www.latent.space/feed",
}

# These sources are AI-specific and don't need keyword filtering
AI_SPECIFIC_SOURCES = {
    "VentureBeat", "Hacker News AI", "KDnuggets",
    "MarkTechPost", "OpenAI Blog", "Google DeepMind",
    "Hugging Face", "NVIDIA Blog", "Lobsters AI", "Reddit ML",
    "Latent Space",
}

# ---------------------------------------------------------------------------
# China / Chinese-language AI feeds
# ---------------------------------------------------------------------------
CN_RSS_FEEDS = {
    # Direct RSS feeds (verified accessible, no RSSHub dependency)
    "36氪": "https://36kr.com/feed",
    "IT之家": "https://www.ithome.com/rss/",
    "虎嗅": "https://www.huxiu.com/rss/0.xml",
    "少数派": "https://sspai.com/feed",
    "雷锋网": "https://www.leiphone.com/feed",
    "TechNode": "https://technode.com/feed/",
}

CN_AI_KEYWORDS = [
    "AI", "人工智能", "大模型", "LLM", "GPT", "机器学习", "深度学习",
    "神经网络", "Transformer", "生成式", "智能体", "Agent",
    "OpenAI", "Anthropic", "Claude", "Gemini", "DeepSeek", "通义", "文心",
    "百度", "阿里云", "腾讯", "字节", "华为", "商汤", "科大讯飞",
    "芯片", "算力", "GPU", "英伟达", "NVIDIA",
    "自动驾驶", "机器人", "具身智能",
    "扩散模型", "Diffusion", "Stable Diffusion", "Midjourney", "Sora",
    "RAG", "向量", "微调", "推理", "训练",
    "开源", "模型", "参数", "Token",
    "监管", "安全", "对齐", "幻觉",
]

CN_AI_SPECIFIC_SOURCES = {
    "雷锋网",  # AI-focused, no keyword filter needed
}


# ---------------------------------------------------------------------------
# YAML-driven feed loaders
# ---------------------------------------------------------------------------

def _load_yaml_feeds(filename: str, key: str) -> list[dict]:
    """Load ``[{name, rss_url, tier, weight, category, keyword_filter, lookback_hours, notes}]``
    entries from a config YAML. Returns empty list if file missing.
    """
    path = CONFIG_DIR / filename
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    entries = cfg.get(key, []) or []
    out = []
    for e in entries:
        out.append({
            "name": e.get("name", "unknown"),
            "rss_url": e.get("rss_url", ""),
            "tier": e.get("tier", "T2"),
            "weight": float(e.get("weight", 1.0)),
            "category": e.get("category", "general"),
            "keyword_filter": e.get("keyword_filter", True),
            "lookback_hours": int(e.get("lookback_hours", 0)) or None,
        })
    return out


def load_changelog_feeds() -> list[dict]:
    return _load_yaml_feeds("changelog_feeds.yaml", "changelogs")


def load_newsletter_feeds() -> list[dict]:
    return _load_yaml_feeds("newsletters.yaml", "newsletters")


class RssCollector:
    """Fetch and filter AI-related articles from RSS feeds.

    Three feed sources are merged each run:
    1. Built-in mainstream media dict (legacy, ``feeds`` arg)
    2. ``config/changelog_feeds.yaml`` (T0 official + T1 deep blogs)
    3. ``config/newsletters.yaml``    (T1 curated newsletters)

    Each item carries ``tier`` / ``weight`` / ``category`` for ranker.
    """

    def __init__(
        self,
        feeds: dict[str, str] | None = None,
        hours: int = 24,
        keywords: list[str] | None = None,
        ai_specific_sources: set[str] | None = None,
        include_yaml_feeds: bool = True,
    ):
        self.feeds = feeds or RSS_FEEDS
        self.keywords = keywords or AI_KEYWORDS
        self.ai_specific_sources = ai_specific_sources or AI_SPECIFIC_SOURCES
        self.hours = hours
        self.cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        self.include_yaml_feeds = include_yaml_feeds

    def collect(self) -> list[RssItem]:
        """Fetch all feeds, filter for AI relevance, deduplicate."""
        all_items: list[RssItem] = []

        # 1. Legacy built-in feeds — default tier T3, weight 1.0
        for source_name, feed_url in self.feeds.items():
            try:
                items = self._fetch_feed(
                    source_name, feed_url,
                    tier="T3", weight=1.0, category="general",
                    keyword_filter=source_name not in self.ai_specific_sources,
                )
                all_items.extend(items)
                if items:
                    logger.info("RSS [%s]: %d items", source_name, len(items))
            except Exception as e:
                logger.warning("RSS [%s] failed: %s", source_name, e)

        # 2. YAML-driven feeds — explicit tier/weight from config; per-feed lookback
        if self.include_yaml_feeds:
            for entry in load_changelog_feeds() + load_newsletter_feeds():
                if not entry["rss_url"]:
                    continue
                # Per-feed cutoff overrides default — T0/T1 sources usually post weekly
                feed_cutoff = self.cutoff
                if entry.get("lookback_hours"):
                    feed_cutoff = datetime.now(timezone.utc) - timedelta(hours=entry["lookback_hours"])
                try:
                    items = self._fetch_feed(
                        entry["name"], entry["rss_url"],
                        tier=entry["tier"],
                        weight=entry["weight"],
                        category=entry["category"],
                        keyword_filter=entry["keyword_filter"],
                        cutoff_override=feed_cutoff,
                    )
                    all_items.extend(items)
                    if items:
                        logger.info(
                            "RSS [%s tier=%s w=%.1f lookback=%dh]: %d items",
                            entry["name"], entry["tier"], entry["weight"],
                            entry.get("lookback_hours") or self.hours, len(items),
                        )
                    else:
                        # Surface 0-result feeds so user can debug
                        logger.info(
                            "RSS [%s] 0 items (lookback=%dh)",
                            entry["name"], entry.get("lookback_hours") or self.hours,
                        )
                except Exception as e:
                    logger.warning("RSS [%s] failed: %s", entry["name"], e)

        # Filter for AI relevance (entries with keyword_filter=False bypass this)
        filtered = [
            item for item in all_items
            if self._is_ai_related(item) or not self._needs_keyword_filter(item)
        ]
        logger.info("RSS total: %d raw -> %d AI-related", len(all_items), len(filtered))

        # Deduplicate by normalized title
        seen: set[str] = set()
        unique: list[RssItem] = []
        for item in filtered:
            key = re.sub(r"\s+", " ", item.title.lower().strip())
            if key not in seen:
                seen.add(key)
                unique.append(item)

        logger.info("RSS after dedup: %d unique items", len(unique))
        return unique

    def _fetch_feed(
        self, source_name: str, url: str,
        tier: str = "T3", weight: float = 1.0,
        category: str = "general", keyword_filter: bool = True,
        cutoff_override: Optional[datetime] = None,
    ) -> list[RssItem]:
        cutoff = cutoff_override or self.cutoff
        feed = feedparser.parse(url)
        items: list[RssItem] = []
        for entry in feed.entries:
            published = None
            for attr in ("published_parsed", "updated_parsed"):
                parsed = getattr(entry, attr, None)
                if parsed:
                    published = datetime(*parsed[:6], tzinfo=timezone.utc)
                    break
            if published and published < cutoff:
                continue

            summary = ""
            if hasattr(entry, "summary"):
                summary = re.sub(r"<[^>]+>", "", entry.summary)[:500]

            item = RssItem(
                title=entry.get("title", ""),
                summary=summary,
                url=entry.get("link", ""),
                source=source_name,
                published=published,
                tier=tier,
                weight=weight,
                category=category,
            )
            # Stash keyword_filter setting so downstream filter step honors it
            item._keyword_filter = keyword_filter  # type: ignore[attr-defined]
            items.append(item)
        return items

    def _needs_keyword_filter(self, item: RssItem) -> bool:
        """Items explicitly marked keyword_filter=False (e.g. official AI blogs,
        curated AI newsletters) skip the AI-keyword check."""
        return getattr(item, "_keyword_filter", True)

    def _is_ai_related(self, item: RssItem) -> bool:
        if item.source in self.ai_specific_sources:
            return True
        text = f"{item.title} {item.summary}".lower()
        return any(kw.lower() in text for kw in self.keywords)
