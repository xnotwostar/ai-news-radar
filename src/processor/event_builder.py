"""Event Card generation from tweet clusters using Qwen-Plus (async concurrent)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import httpx

from ..schemas import EventCard, EventCategory, EventSource, TweetEmbedded

logger = logging.getLogger(__name__)

DASHSCOPE_CHAT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

# 并发控制：5路并发 ≈ 75 RPM，安全在 DashScope 120 RPM 限速内
MAX_CONCURRENCY = 5

EVENT_CARD_SYSTEM_PROMPT = """你是一个 AI 行业情报分析助手。给定一组讨论同一事件的推文，提取结构化的 Event Card。

输出严格 JSON 格式（不要 markdown code block）：
{
  "title": "📌 事件标题（中文，保留专有名词英文，标题前加合适的 emoji）",
  "category": "product_launch|research|funding|chip_hardware|policy|partnership|open_source|market|other",
  "importance": 1-10的浮点数,
  "type": "news 或 opinion（news=产品发布/融资/技术突破等客观事件；opinion=专家个人观点/评论/分析/预测）",
  "key_facts": ["关键事实1", "关键事实2"],
  "analyst_angle": "这对行业意味着什么（一句话分析师视角）"
}

title 的 emoji 规则：
- product_launch → 🚀
- research → 🔬
- funding → 💰
- chip_hardware → 🔧
- policy → 📜
- partnership → 🤝
- open_source → 🌐
- market → 📊
- opinion → 💡
- other → 📌

type 判断规则：
- 如果推文来自公司/机构官方账号发布的产品/融资/技术公告 → news
- 如果推文是个人专家表达观点、评论、预测、分析 → opinion
- 如果混合，以主要信息类型为准

评分标准：
- 9-10: 行业格局改变（大模型发布、重大融资、芯片突破）
- 7-8: 重要产品更新、有影响力的研究成果
- 5-6: 值得关注的动态
- 3-4: 一般信息
- 1-2: 噪声"""


class EventBuilder:
    """Build Event Cards from clustered tweets via Qwen-Plus (async concurrent)."""

    def __init__(self, api_key: str | None = None, model: str = "qwen-plus"):
        self.api_key = api_key or os.environ["DASHSCOPE_API_KEY"]
        self.model = model

    def build_events(
        self,
        clusters: dict[int, list[TweetEmbedded]],
        date_str: str | None = None,
    ) -> list[EventCard]:
        """Generate one Event Card per cluster (concurrent)."""
        date_str = date_str or datetime.now(timezone.utc).strftime("%Y%m%d")
        logger.info("Building event cards for %d clusters (concurrency=%d)...", len(clusters), MAX_CONCURRENCY)

        events = asyncio.run(self._build_all_async(clusters, date_str))

        logger.info("Built %d event cards from %d clusters", len(events), len(clusters))
        return events

    async def _build_all_async(
        self,
        clusters: dict[int, list[TweetEmbedded]],
        date_str: str,
    ) -> list[EventCard]:
        """并发构建所有事件卡片，用 semaphore 控制并发数。"""
        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

        async with httpx.AsyncClient(timeout=30) as client:
            tasks = [
                self._build_single_async(client, semaphore, cluster_id, tweets, date_str)
                for cluster_id, tweets in clusters.items()
            ]
            results = await asyncio.gather(*tasks)

        return list(results)

    async def _build_single_async(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        cluster_id: int,
        tweets: list[TweetEmbedded],
        date_str: str,
    ) -> EventCard:
        """单个事件卡片的异步构建。"""
        async with semaphore:
            try:
                return await self._call_llm(client, cluster_id, tweets, date_str)
            except Exception as e:
                logger.warning("Failed to build event for cluster %d: %s", cluster_id, e)
                return self._fallback_event(cluster_id, tweets, date_str)

    @staticmethod
    def _extract_event_time(tweets: list[TweetEmbedded]) -> datetime | None:
        """从聚类推文中提取事件时间（取最早的推文时间）。"""
        times = [t.tweet.created_at for t in tweets if t.tweet.created_at]
        return min(times) if times else None

    async def _call_llm(
        self,
        client: httpx.AsyncClient,
        cluster_id: int,
        tweets: list[TweetEmbedded],
        date_str: str,
    ) -> EventCard:
        """调用 LLM 生成单个事件卡片。"""
        sorted_tweets = sorted(tweets, key=lambda t: t.tweet.engagement, reverse=True)[:5]

        tweets_text = "\n\n".join(
            f"@{t.tweet.author_handle} ({t.tweet.created_at.strftime('%m-%d %H:%M UTC') if t.tweet.created_at else 'unknown'}): "
            f"{t.tweet.text} "
            f"[likes:{t.tweet.like_count} RT:{t.tweet.retweet_count}]"
            for t in sorted_tweets
        )

        user_prompt = f"以下推文讨论同一事件，请提取 Event Card：\n\n{tweets_text}"

        resp = await client.post(
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
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)

        sources = [
            EventSource(
                author=t.tweet.author_handle.lstrip("@"),
                text=t.tweet.text[:200],
                engagement=t.tweet.engagement,
                url=t.tweet.url,
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
            event_time=self._extract_event_time(tweets),
            event_type=parsed.get("type", "news"),
        )

    @staticmethod
    def _fallback_event(
        cluster_id: int, tweets: list[TweetEmbedded], date_str: str
    ) -> EventCard:
        """Create minimal event card when LLM fails."""
        top = max(tweets, key=lambda t: t.tweet.engagement)
        times = [t.tweet.created_at for t in tweets if t.tweet.created_at]
        return EventCard(
            event_id=f"evt_{date_str}_{cluster_id:03d}",
            title=f"📌 {top.tweet.text[:80]}",
            category=EventCategory.OTHER,
            importance=3.0,
            sources=[
                EventSource(
                    author=top.tweet.author_handle.lstrip("@"),
                    text=top.tweet.text[:200],
                    engagement=top.tweet.engagement,
                    url=top.tweet.url,
                )
            ],
            key_facts=[],
            analyst_angle="",
            cluster_size=len(tweets),
            event_time=min(times) if times else None,
            event_type="news",
        )
