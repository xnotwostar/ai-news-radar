"""Event Card generation from tweet clusters using Qwen-Plus."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import httpx

from ..schemas import EventCard, EventCategory, EventSource, TweetEmbedded

logger = logging.getLogger(__name__)

DASHSCOPE_CHAT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

EVENT_CARD_SYSTEM_PROMPT = """你是一个 AI 行业情报分析助手。给定一组讨论同一事件的推文，提取结构化的 Event Card。

输出严格 JSON 格式（不要 markdown code block）：
{
  "title": "事件标题（中文，保留专有名词英文）",
  "category": "product_launch|research|funding|chip_hardware|policy|partnership|open_source|market|other",
  "importance": 1-10的浮点数,
  "key_facts": ["关键事实1", "关键事实2"],
  "analyst_angle": "这对行业意味着什么（一句话分析师视角）"
}

评分标准：
- 9-10: 行业格局改变（大模型发布、重大融资、芯片突破）
- 7-8: 重要产品更新、有影响力的研究成果
- 5-6: 值得关注的动态
- 3-4: 一般信息
- 1-2: 噪声"""


class EventBuilder:
    """Build Event Cards from clustered tweets via Qwen-Plus."""

    def __init__(self, api_key: str | None = None, model: str = "qwen-plus"):
        self.api_key = api_key or os.environ["DASHSCOPE_API_KEY"]
        self.model = model

    def build_events(
        self,
        clusters: dict[int, list[TweetEmbedded]],
        date_str: str | None = None,
    ) -> list[EventCard]:
        """Generate one Event Card per cluster."""
        date_str = date_str or datetime.now(timezone.utc).strftime("%Y%m%d")
        events: list[EventCard] = []

        for cluster_id, tweets in clusters.items():
            try:
                event = self._build_single(cluster_id, tweets, date_str)
                events.append(event)
            except Exception as e:
                logger.warning("Failed to build event for cluster %d: %s", cluster_id, e)
                # Fallback: create a minimal event card from highest-engagement tweet
                events.append(self._fallback_event(cluster_id, tweets, date_str))

        logger.info("Built %d event cards from %d clusters", len(events), len(clusters))
        return events

    def _build_single(
        self, cluster_id: int, tweets: list[TweetEmbedded], date_str: str
    ) -> EventCard:
        # Sort by engagement, take top 5 tweets for context
        sorted_tweets = sorted(tweets, key=lambda t: t.tweet.engagement, reverse=True)[:5]

        tweets_text = "\n\n".join(
            f"@{t.tweet.author_handle}: {t.tweet.text} "
            f"[likes:{t.tweet.like_count} RT:{t.tweet.retweet_count}]"
            for t in sorted_tweets
        )

        user_prompt = f"以下推文讨论同一事件，请提取 Event Card：\n\n{tweets_text}"

        resp = httpx.post(
            DASHSCOPE_CHAT_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": EVENT_CARD_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
                "response_format": {"type": "json_object"},
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)

        sources = [
            EventSource(
                author=f"@{t.tweet.author_handle}",
                text=t.tweet.text[:200],
                engagement=t.tweet.engagement,
            )
            for t in sorted_tweets
        ]

        category = EventCategory.OTHER
        try:
            category = EventCategory(parsed.get("category", "other"))
        except ValueError:
            pass

        return EventCard(
            event_id=f"evt_{date_str}_{cluster_id:03d}",
            title=parsed.get("title", "未知事件"),
            category=category,
            importance=float(parsed.get("importance", 5.0)),
            sources=sources,
            key_facts=parsed.get("key_facts", []),
            analyst_angle=parsed.get("analyst_angle", ""),
            cluster_size=len(tweets),
        )

    @staticmethod
    def _fallback_event(
        cluster_id: int, tweets: list[TweetEmbedded], date_str: str
    ) -> EventCard:
        """Create minimal event card when LLM fails."""
        top = max(tweets, key=lambda t: t.tweet.engagement)
        return EventCard(
            event_id=f"evt_{date_str}_{cluster_id:03d}",
            title=top.tweet.text[:80],
            category=EventCategory.OTHER,
            importance=3.0,
            sources=[
                EventSource(
                    author=f"@{top.tweet.author_handle}",
                    text=top.tweet.text[:200],
                    engagement=top.tweet.engagement,
                )
            ],
            key_facts=[],
            analyst_angle="",
            cluster_size=len(tweets),
        )
