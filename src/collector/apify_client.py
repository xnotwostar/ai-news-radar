"""Apify Twitter List Timeline collector."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from apify_client import ApifyClient

from ..schemas import TweetRaw

logger = logging.getLogger(__name__)

ACTOR_ID = "apidojo/twitter-list-scraper"
DEFAULT_MAX_ITEMS = 500


class ApifyCollector:
    """Fetch tweets from a Twitter List via Apify."""

    def __init__(self, token: str | None = None):
        self.token = token or os.environ["APIFY_TOKEN"]
        self.client = ApifyClient(self.token)

    def collect(self, list_id: str, max_items: int = DEFAULT_MAX_ITEMS) -> tuple[list[TweetRaw], dict]:
        """Run Apify actor and return parsed, deduplicated tweets plus collection stats."""
        logger.info("Starting Apify collection for list %s (max %d)", list_id, max_items)

        run_input = {
            "listIds": [list_id],
            "maxItems": max_items,
        }

        run = self.client.actor(ACTOR_ID).call(run_input=run_input)
        dataset_items = self.client.dataset(run["defaultDatasetId"]).list_items().items

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        tweets: list[TweetRaw] = []
        filtered_by_time = 0
        filtered_by_length = 0
        parse_errors = 0

        for item in dataset_items:
            try:
                tweet = self._parse_item(item)
                if tweet.created_at and tweet.created_at < cutoff:
                    filtered_by_time += 1
                    continue
                if len(tweet.text.strip()) < 20:
                    filtered_by_length += 1
                    continue
                tweets.append(tweet)
            except Exception as e:
                parse_errors += 1
                logger.warning("Failed to parse tweet item: %s", e)
                continue

        after_filter = len(tweets)
        tweets = self._dedup(tweets)

        stats = {
            "apify_max_items": max_items,
            "apify_returned": len(dataset_items),
            "filtered_by_24h": filtered_by_time,
            "filtered_by_min_length": filtered_by_length,
            "parse_errors": parse_errors,
            "after_basic_filter": after_filter,
            "dedup_removed": after_filter - len(tweets),
            "after_dedup": len(tweets),
        }

        logger.info(
            "Collector stats: apify_returned=%d → 24h=-%d, length=-%d, "
            "dedup=-%d → final=%d",
            stats["apify_returned"], filtered_by_time, filtered_by_length,
            stats["dedup_removed"], len(tweets),
        )
        return tweets, stats

    @staticmethod
    def _dedup(tweets: list[TweetRaw]) -> list[TweetRaw]:
        """Remove pure RTs, exact text duplicates, and near-duplicate tweets."""
        # 1. Remove pure RTs (text starts with "RT @")
        tweets = [t for t in tweets if not t.text.strip().startswith("RT @")]

        # 2. Exact same text: keep highest engagement
        text_best: dict[str, TweetRaw] = {}
        for t in tweets:
            key = t.text.strip()
            if key not in text_best or t.engagement > text_best[key].engagement:
                text_best[key] = t
        tweets = list(text_best.values())

        # 3. Same author + first 80 chars match + within 2 hours: keep one
        tweets.sort(key=lambda t: t.engagement, reverse=True)
        seen: dict[str, datetime] = {}
        result: list[TweetRaw] = []
        for t in tweets:
            prefix_key = f"{t.author_handle}:{t.text[:80]}"
            prev_time = seen.get(prefix_key)
            if prev_time is not None and t.created_at is not None:
                if abs((t.created_at - prev_time).total_seconds()) < 7200:
                    continue
            if t.created_at is not None:
                seen[prefix_key] = t.created_at
            result.append(t)

        return result

    @staticmethod
    def _parse_item(item: dict) -> TweetRaw:
        created_at = None
        raw_date = item.get("createdAt") or item.get("created_at") or ""
        if raw_date:
            try:
                created_at = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            except ValueError:
                pass

        return TweetRaw(
            tweet_id=str(item.get("id", item.get("tweetId", ""))),
            author_handle=item.get("author", {}).get("userName", "")
                if isinstance(item.get("author"), dict)
                else str(item.get("authorHandle", "")),
            author_name=item.get("author", {}).get("name", "")
                if isinstance(item.get("author"), dict)
                else str(item.get("authorName", "")),
            text=item.get("text", item.get("full_text", "")),
            created_at=created_at,
            retweet_count=int(item.get("retweetCount", 0)),
            like_count=int(item.get("likeCount", item.get("favoriteCount", 0))),
            reply_count=int(item.get("replyCount", 0)),
            quote_count=int(item.get("quoteCount", 0)),
            view_count=int(item.get("viewCount", 0)),
        )
