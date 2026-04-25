"""Apify Twitter List Timeline collector."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone

from apify_client import ApifyClient

from ..schemas import TweetRaw

logger = logging.getLogger(__name__)

ACTOR_ID = "apidojo/twitter-list-scraper"
DEFAULT_MAX_ITEMS = 500

# Marketing / lead-magnet / low-signal phrase patterns. Tweets matching any of
# these are discarded regardless of engagement — they inflate numbers but
# carry no information.
SPAM_PATTERNS = [
    re.compile(r"comment\s+['\"]?\w+['\"]?\s+(and|&)\s+i\s*['']?ll\s+dm", re.I),
    re.compile(r"i\s*['']?ll\s+(dm|send)\s+you\s+(my|the)\s+(guide|prompts?|workflow|automation)", re.I),
    re.compile(r"^\s*\d{1,2}\s+ai\s+tools\s+to\s+level\s+up", re.I),
    re.compile(r"^\s*\d{1,2}\s+(ai|free)\s+tools\s+that\s+will", re.I),
    re.compile(r"thread\s*[:\U0001F9F5]+\s*\d+\s+ai\s+tools", re.I),
    re.compile(r"steal\s+(these|my)\s+(prompts?|automations?)", re.I),
    re.compile(r"follow\s+me\s+for\s+more\s+ai", re.I),
]

# Per-author daily cap — even S-tier authors can't flood the digest.
PER_AUTHOR_DAILY_CAP = 3


class ApifyCollector:
    """Fetch tweets from a Twitter List via Apify."""

    def __init__(self, token: str | None = None):
        self.token = token or os.environ["APIFY_TOKEN"]
        self.client = ApifyClient(self.token)

    def collect(self, list_id: str, max_items: int = DEFAULT_MAX_ITEMS) -> list[TweetRaw]:
        """Run Apify actor and return parsed, deduplicated, filtered tweets."""
        logger.info("Starting Apify collection for list %s (max %d)", list_id, max_items)

        run_input = {
            "listIds": [list_id],
            "maxItems": max_items,
        }

        run = self.client.actor(ACTOR_ID).call(run_input=run_input)
        dataset_items = self.client.dataset(run["defaultDatasetId"]).list_items().items

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
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

        raw_count = len(tweets)

        spam_filtered = self._filter_marketing_spam(tweets)
        spam_removed = raw_count - len(spam_filtered)

        deduped = self._dedup(spam_filtered)
        dedup_removed = len(spam_filtered) - len(deduped)

        quota_capped = self._apply_author_quota(deduped)
        quota_removed = len(deduped) - len(quota_capped)

        logger.info(
            "Apify %s: %d raw -> %d after spam (-%d) -> %d after dedup (-%d) -> %d after quota (-%d)",
            list_id, raw_count, len(spam_filtered), spam_removed,
            len(deduped), dedup_removed, len(quota_capped), quota_removed,
        )
        return quota_capped

    @staticmethod
    def _filter_marketing_spam(tweets: list[TweetRaw]) -> list[TweetRaw]:
        """Drop tweets that match lead-magnet / listicle / engagement-bait patterns."""
        kept: list[TweetRaw] = []
        for t in tweets:
            text = t.text or ""
            if any(p.search(text) for p in SPAM_PATTERNS):
                continue
            # Short pure-emoji / reaction tweets: already handled by 20-char minimum
            # but also kill tweets that are >60% non-alphabet chars (emoji spam)
            alpha = sum(1 for c in text if c.isalpha())
            if len(text) > 0 and alpha / len(text) < 0.3:
                continue
            kept.append(t)
        return kept

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
    def _apply_author_quota(tweets: list[TweetRaw], cap: int = PER_AUTHOR_DAILY_CAP) -> list[TweetRaw]:
        """Cap each author at `cap` tweets per day; keep highest info_density, then engagement."""
        # Sort so the best-per-author is kept first
        tweets_sorted = sorted(
            tweets,
            key=lambda t: (t.info_density, t.engagement),
            reverse=True,
        )
        count: dict[str, int] = {}
        kept: list[TweetRaw] = []
        for t in tweets_sorted:
            handle = (t.author_handle or "").lstrip("@").lower()
            if not handle:
                kept.append(t)
                continue
            if count.get(handle, 0) >= cap:
                continue
            count[handle] = count.get(handle, 0) + 1
            kept.append(t)
        return kept

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
            bookmark_count=int(item.get("bookmarkCount", item.get("bookmark_count", 0))),
        )
