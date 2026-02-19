"""Apify Twitter List Timeline collector."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from apify_client import ApifyClient

from ..schemas import TweetRaw

logger = logging.getLogger(__name__)

ACTOR_ID = "voyager/twitter-list-timeline"
DEFAULT_MAX_ITEMS = 500


class ApifyCollector:
    """Fetch tweets from a Twitter List via Apify."""

    def __init__(self, token: str | None = None):
        self.token = token or os.environ["APIFY_TOKEN"]
        self.client = ApifyClient(self.token)

    def collect(self, list_id: str, max_items: int = DEFAULT_MAX_ITEMS) -> list[TweetRaw]:
        """Run Apify actor and return parsed tweets."""
        logger.info("Starting Apify collection for list %s (max %d)", list_id, max_items)

        run_input = {
            "listId": list_id,
            "maxItems": max_items,
            "sort": "Latest",
        }

        run = self.client.actor(ACTOR_ID).call(run_input=run_input)
        dataset_items = self.client.dataset(run["defaultDatasetId"]).list_items().items

        cutoff = datetime.now(timezone.utc) - timedelta(hours=36)
        tweets: list[TweetRaw] = []

        for item in dataset_items:
            try:
                tweet = self._parse_item(item)
                if tweet.created_at and tweet.created_at < cutoff:
                    continue
                if len(tweet.text.strip()) < 20:
                    continue
                tweets.append(tweet)
            except Exception as e:
                logger.warning("Failed to parse tweet item: %s", e)
                continue

        logger.info("Collected %d tweets (from %d raw items)", len(tweets), len(dataset_items))
        return tweets

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
