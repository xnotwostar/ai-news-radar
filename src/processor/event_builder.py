"""Event Card generation from tweet clusters via async LLM.

Provider can be 'google' (default, OpenAI-compat Gemini) or 'dashscope'.
Auto-falls back to the secondary if the primary fails (e.g. arrears / 429).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import httpx

from ..schemas import EventCard, EventCategory, EventSource, TweetEmbedded

logger = logging.getLogger(__name__)

# Provider URL + key + default model + per-RPM safety concurrency
PROVIDER_DEFAULTS = {
    "google": {
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "key_env": "GOOGLE_API_KEY",
        "model": "gemini-2.5-flash",
    },
    "dashscope": {
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "key_env": "DASHSCOPE_API_KEY",
        "model": "qwen-plus",
    },
    "deepseek": {
        "url": "https://api.deepseek.com/v1/chat/completions",
        "key_env": "DEEPSEEK_API_KEY",
        "model": "deepseek-chat",
    },
}

FALLBACK_CHAIN = ["google", "dashscope", "deepseek"]

# 并发控制：5 路并发，安全在多数 provider 的限速内
MAX_CONCURRENCY = 5

# Backwards-compat constant for any external imports
DASHSCOPE_CHAT_URL = PROVIDER_DEFAULTS["dashscope"]["url"]

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

评分标准（importance）：
- 9-10: 行业格局改变（大模型发布、重大融资、芯片突破、IPO/并购）
- 7-8: 重要产品更新、有影响力的研究成果、融资 $10-50M
- 5-6: 值得关注的动态
- 3-4: 一般信息
- 1-2: 噪声

信号解读辅助（评分时参考）：
- 作者 tier：s_investor/s_founder 权威度最高；a_engineering/b_research 次之；downweight 要打折
- bm/likes 比：≥0.3 是"值得存"的高密度信号；<0.05 多为情绪反应，importance 应 ≤4
- 官方账号（OpenAI/Anthropic/Google 等）+ 产品发布 → 至少 7 分起评"""


class EventBuilder:
    """Build Event Cards from clustered tweets via async LLM with fallback chain."""

    def __init__(
        self,
        provider: str = "google",
        model: str | None = None,
        api_key: str | None = None,
        enable_fallback: bool = True,
    ):
        if provider not in PROVIDER_DEFAULTS:
            raise ValueError(f"Unknown LLM provider: {provider}")
        self.provider = provider
        defaults = PROVIDER_DEFAULTS[provider]
        self.model = model or defaults["model"]
        self._api_key_override = api_key
        self.enable_fallback = enable_fallback

    def _get_key(self, provider: str) -> str | None:
        if self._api_key_override and provider == self.provider:
            return self._api_key_override
        return os.environ.get(PROVIDER_DEFAULTS[provider]["key_env"])

    def _get_model(self, provider: str) -> str:
        return self.model if provider == self.provider else PROVIDER_DEFAULTS[provider]["model"]

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

    async def _call_llm_with_fallback(
        self,
        client: httpx.AsyncClient,
        provider: str,
        payload: dict,
    ) -> dict:
        """POST chat completion to a single provider, return parsed JSON content."""
        api_key = self._get_key(provider)
        if not api_key:
            raise RuntimeError(f"Missing key env: {PROVIDER_DEFAULTS[provider]['key_env']}")
        url = PROVIDER_DEFAULTS[provider]["url"]
        # Adapt the model name per-provider when falling back
        payload = {**payload, "model": self._get_model(provider)}
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    async def _call_llm(
        self,
        client: httpx.AsyncClient,
        cluster_id: int,
        tweets: list[TweetEmbedded],
        date_str: str,
    ) -> EventCard:
        """调用 LLM 生成单个事件卡片，带 provider fallback。"""
        # Ensure RSS sources are always included (they have 0 engagement)
        rss_tweets = [t for t in tweets if t.tweet.is_rss]
        twitter_tweets = sorted(
            [t for t in tweets if not t.tweet.is_rss],
            key=lambda t: t.tweet.engagement, reverse=True,
        )
        # RSS first, then fill remaining slots with top Twitter by engagement
        sorted_tweets = (rss_tweets + twitter_tweets)[:5]

        from .ranker import tier_for_handle

        def _format_tweet(t: TweetEmbedded) -> str:
            time_str = t.tweet.created_at.strftime('%m-%d %H:%M UTC') if t.tweet.created_at else 'unknown'
            if t.tweet.is_rss:
                return (
                    f"[{t.tweet.author_name}] ({time_str}): "
                    f"{t.tweet.text} [来源: {t.tweet.url}]"
                )
            tier = t.tweet.author_tier if t.tweet.author_tier != "unknown" \
                   else tier_for_handle(t.tweet.author_handle)
            density = f"{t.tweet.info_density:.2f}" if t.tweet.like_count > 0 else "n/a"
            return (
                f"@{t.tweet.author_handle} [tier={tier}] ({time_str}): "
                f"{t.tweet.text} "
                f"[likes:{t.tweet.like_count} bm:{t.tweet.bookmark_count} "
                f"bm/likes:{density} RT:{t.tweet.retweet_count}]"
            )

        tweets_text = "\n\n".join(_format_tweet(t) for t in sorted_tweets)

        user_prompt = f"以下推文/文章讨论同一事件，请提取 Event Card：\n\n{tweets_text}"

        # Build payload once, try each provider in fallback chain
        payload = {
            "messages": [
                {"role": "system", "content": EVENT_CARD_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }

        chain = [self.provider]
        if self.enable_fallback:
            chain.extend(p for p in FALLBACK_CHAIN if p != self.provider)

        last_err: Exception | None = None
        result_json: dict | None = None
        for prov in chain:
            try:
                result_json = await self._call_llm_with_fallback(client, prov, payload)
                break
            except Exception as e:
                logger.warning("EventBuilder via %s failed for cluster %d: %s",
                               prov, cluster_id, str(e)[:200])
                last_err = e
                continue
        if result_json is None:
            raise RuntimeError(f"All EventBuilder providers failed: {last_err}")

        content = result_json["choices"][0]["message"]["content"]
        parsed = json.loads(content)

        # Put RSS sources first so report writer presents media URLs prominently
        from .ranker import tier_for_handle
        sources = [
            EventSource(
                author=t.tweet.author_name if t.tweet.is_rss else t.tweet.author_handle.lstrip("@"),
                text=t.tweet.text[:200],
                engagement=t.tweet.engagement,
                bookmark_count=t.tweet.bookmark_count,
                like_count=t.tweet.like_count,
                author_tier=t.tweet.author_tier if t.tweet.author_tier != "unknown"
                           else tier_for_handle(t.tweet.author_handle),
                url=t.tweet.url,
            )
            for t in sorted(sorted_tweets, key=lambda t: (not t.tweet.is_rss, -t.tweet.engagement))
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
        from .ranker import tier_for_handle
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
                    bookmark_count=top.tweet.bookmark_count,
                    like_count=top.tweet.like_count,
                    author_tier=top.tweet.author_tier if top.tweet.author_tier != "unknown"
                               else tier_for_handle(top.tweet.author_handle),
                    url=top.tweet.url,
                )
            ],
            key_facts=[],
            analyst_angle="",
            cluster_size=len(tweets),
            event_time=min(times) if times else None,
            event_type="news",
        )
