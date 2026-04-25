"""HackerNews collector — fetches top + Show HN stories filtered for AI relevance.

Why HN matters: it's the highest-signal community pulse for engineering / research
discussion. A story crossing 150 points + AI keywords means real engineers care.

Public API (no auth needed):
  - https://hacker-news.firebaseio.com/v0/topstories.json
  - https://hacker-news.firebaseio.com/v0/showstories.json
  - https://hacker-news.firebaseio.com/v0/item/{id}.json

Output is a list of :class:`TweetRaw` so the existing pipeline (cluster / rank /
report) can consume it without special-casing.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta

import httpx

from ..schemas import TweetRaw

logger = logging.getLogger(__name__)

HN_BASE = "https://hacker-news.firebaseio.com/v0"
TOP_STORIES_URL = f"{HN_BASE}/topstories.json"
SHOW_STORIES_URL = f"{HN_BASE}/showstories.json"

DEFAULT_TOP_LIMIT = 80          # top N IDs to fetch from each list
MIN_SCORE = 150                 # minimum points to be considered signal
MIN_SHOW_HN_SCORE = 80          # Show HN gets a lower bar (newer products)
MAX_CONCURRENCY = 10
LOOKBACK_HOURS = 36             # HN stories rise/fall within ~24-36h

# Word-boundary patterns. Bare "ai" matches "email"/"iliad"/etc, so we
# require it to be a standalone word; multi-word phrases pass through.
AI_KEYWORDS = [
    "ai", "llm", "gpt", "claude", "gemini", "openai", "anthropic", "deepmind",
    "huggingface", "hugging face", "mistral", "llama", "qwen", "deepseek",
    "machine learning", "neural network", "transformer", "diffusion model",
    "agent", "agentic", "agi", "rag", "embedding", "fine-tun", "inference",
    "nvidia", "gpu", "cuda", "tensor", "tpu",
    "stable diffusion", "midjourney", "sora", "runway",
    "copilot", "cursor", "perplexity",
    "alignment", "hallucination",
    "open source model", "open-source model", "open weight",
    "foundation model", "language model", "vision model",
    "prompt engineering", "chain of thought", "reasoning model",
]
AI_KEYWORDS_RE = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:" + "|".join(re.escape(k) for k in AI_KEYWORDS) + r")(?:[^a-z0-9]|$)"
)


class HnCollector:
    """Fetch HackerNews top + Show HN stories filtered for AI relevance."""

    def __init__(
        self,
        top_limit: int = DEFAULT_TOP_LIMIT,
        min_score: int = MIN_SCORE,
        min_show_hn_score: int = MIN_SHOW_HN_SCORE,
        lookback_hours: int = LOOKBACK_HOURS,
    ):
        self.top_limit = top_limit
        self.min_score = min_score
        self.min_show_hn_score = min_show_hn_score
        self.cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    def collect(self) -> list[TweetRaw]:
        """Synchronous facade over the async fetch."""
        return asyncio.run(self._collect_async())

    async def _collect_async(self) -> list[TweetRaw]:
        async with httpx.AsyncClient(timeout=15) as client:
            top_ids, show_ids = await asyncio.gather(
                self._fetch_id_list(client, TOP_STORIES_URL),
                self._fetch_id_list(client, SHOW_STORIES_URL),
            )
            top_ids = top_ids[: self.top_limit]
            show_ids = show_ids[: self.top_limit]

            sem = asyncio.Semaphore(MAX_CONCURRENCY)
            top_items, show_items = await asyncio.gather(
                self._fetch_items(client, sem, top_ids, is_show_hn=False),
                self._fetch_items(client, sem, show_ids, is_show_hn=True),
            )

        all_items = top_items + show_items
        # Dedup by id (Show HN sometimes appears in top)
        seen_ids: set[str] = set()
        deduped: list[TweetRaw] = []
        for t in all_items:
            if t.tweet_id in seen_ids:
                continue
            seen_ids.add(t.tweet_id)
            deduped.append(t)

        logger.info(
            "HN: top=%d show=%d -> %d after dedup",
            len(top_items), len(show_items), len(deduped),
        )
        return deduped

    async def _fetch_id_list(self, client: httpx.AsyncClient, url: str) -> list[int]:
        try:
            r = await client.get(url)
            r.raise_for_status()
            return r.json() or []
        except Exception as e:
            logger.warning("HN id-list fetch failed (%s): %s", url, e)
            return []

    async def _fetch_items(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        ids: list[int],
        is_show_hn: bool,
    ) -> list[TweetRaw]:
        async def fetch_one(item_id: int) -> TweetRaw | None:
            async with sem:
                try:
                    r = await client.get(f"{HN_BASE}/item/{item_id}.json")
                    r.raise_for_status()
                    data = r.json()
                except Exception as e:
                    logger.debug("HN item %d failed: %s", item_id, e)
                    return None
                if not data or data.get("type") != "story":
                    return None

                score = int(data.get("score", 0))
                threshold = self.min_show_hn_score if is_show_hn else self.min_score
                if score < threshold:
                    return None

                title = data.get("title", "") or ""
                if not title:
                    return None
                if not self._is_ai_related(title):
                    return None

                ts = data.get("time")
                created_at = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
                if created_at and created_at < self.cutoff:
                    return None

                return TweetRaw(
                    tweet_id=f"hn_{item_id}",
                    author_handle=data.get("by", "hn_user"),
                    author_name=f"Hacker News [{data.get('by', 'unknown')}]",
                    text=self._format_text(title, data, is_show_hn),
                    created_at=created_at,
                    like_count=score,
                    reply_count=int(data.get("descendants", 0)),
                    source_url=data.get("url") or f"https://news.ycombinator.com/item?id={item_id}",
                    is_rss=True,
                    author_tier="t2_community",
                )

        tasks = [fetch_one(iid) for iid in ids]
        results = await asyncio.gather(*tasks)
        return [t for t in results if t is not None]

    @staticmethod
    def _is_ai_related(title: str) -> bool:
        return bool(AI_KEYWORDS_RE.search(title))

    @staticmethod
    def _format_text(title: str, data: dict, is_show_hn: bool) -> str:
        prefix = "Show HN: " if is_show_hn and not title.lower().startswith("show hn") else ""
        text = f"{prefix}{title}"
        url = data.get("url") or ""
        if url:
            text += f" — {url}"
        score = data.get("score", 0)
        comments = data.get("descendants", 0)
        text += f" [{score} pts · {comments} comments]"
        return text
