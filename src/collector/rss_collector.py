"""RSS feed collector for global & China AI news."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import feedparser

logger = logging.getLogger(__name__)


class RssItem:
    """Single item from an RSS feed."""

    __slots__ = ("title", "summary", "url", "source", "published")

    def __init__(
        self,
        title: str,
        summary: str = "",
        url: str = "",
        source: str = "",
        published: Optional[datetime] = None,
    ):
        self.title = title
        self.summary = summary
        self.url = url
        self.source = source
        self.published = published


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
}

# These sources are AI-specific and don't need keyword filtering
AI_SPECIFIC_SOURCES = {
    "VentureBeat", "Hacker News AI", "KDnuggets",
    "MarkTechPost", "OpenAI Blog", "Google DeepMind",
    "Hugging Face", "NVIDIA Blog", "Lobsters AI", "Reddit ML",
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


class RssCollector:
    """Fetch and filter AI-related articles from RSS feeds."""

    def __init__(
        self,
        feeds: dict[str, str] | None = None,
        hours: int = 24,
        keywords: list[str] | None = None,
        ai_specific_sources: set[str] | None = None,
    ):
        self.feeds = feeds or RSS_FEEDS
        self.keywords = keywords or AI_KEYWORDS
        self.ai_specific_sources = ai_specific_sources or AI_SPECIFIC_SOURCES
        self.hours = hours
        self.cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    def collect(self) -> list[RssItem]:
        """Fetch all feeds, filter for AI relevance, deduplicate."""
        all_items: list[RssItem] = []
        for source_name, feed_url in self.feeds.items():
            try:
                items = self._fetch_feed(source_name, feed_url)
                all_items.extend(items)
                if items:
                    logger.info("RSS [%s]: %d items", source_name, len(items))
            except Exception as e:
                logger.warning("RSS [%s] failed: %s", source_name, e)

        filtered = [item for item in all_items if self._is_ai_related(item)]
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

    def _fetch_feed(self, source_name: str, url: str) -> list[RssItem]:
        feed = feedparser.parse(url)
        items: list[RssItem] = []
        for entry in feed.entries:
            published = None
            for attr in ("published_parsed", "updated_parsed"):
                parsed = getattr(entry, attr, None)
                if parsed:
                    published = datetime(*parsed[:6], tzinfo=timezone.utc)
                    break
            if published and published < self.cutoff:
                continue

            summary = ""
            if hasattr(entry, "summary"):
                summary = re.sub(r"<[^>]+>", "", entry.summary)[:300]

            items.append(RssItem(
                title=entry.get("title", ""),
                summary=summary,
                url=entry.get("link", ""),
                source=source_name,
                published=published,
            ))
        return items

    def _is_ai_related(self, item: RssItem) -> bool:
        if item.source in self.ai_specific_sources:
            return True
        text = f"{item.title} {item.summary}".lower()
        return any(kw.lower() in text for kw in self.keywords)
