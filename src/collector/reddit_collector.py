"""Reddit collector — top discussion from AI engineering communities.

Targets:
- r/LocalLLaMA      — open-weight model practitioners
- r/MachineLearning — academic/industry research [D]iscussion
- r/ChatGPTPro      — applied prompting/agent users (optional)

Strategy: top of week, score >= threshold, drop autotag bots & meta posts.
Items are mapped to TweetRaw so the rest of the pipeline is unchanged.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone, timedelta

from apify_client import ApifyClient

from ..schemas import TweetRaw

logger = logging.getLogger(__name__)

ACTOR_ID = "trudax/reddit-scraper-lite"

DEFAULT_TARGETS = [
    {"sub": "LocalLLaMA",       "url": "https://www.reddit.com/r/LocalLLaMA/top/?t=day", "min_score": 100},
    {"sub": "MachineLearning",  "url": "https://www.reddit.com/r/MachineLearning/top/?t=week", "min_score": 100},
    {"sub": "singularity",      "url": "https://www.reddit.com/r/singularity/top/?t=day",  "min_score": 200},
]

LOOKBACK_HOURS = 72
META_PATTERNS = [
    re.compile(r"^\[D\]?$", re.I),
    re.compile(r"weekly\s+(thread|discussion)", re.I),
    re.compile(r"this\s+week\s+in\s+(machine|ai)", re.I),
]


class RedditCollector:
    """Pull top AI-relevant Reddit posts via Apify reddit-scraper-lite."""

    def __init__(
        self,
        token: str | None = None,
        targets: list[dict] | None = None,
        max_items: int = 30,
    ):
        self.token = token or os.environ.get("APIFY_TOKEN")
        if not self.token:
            raise RuntimeError("APIFY_TOKEN env required")
        self.client = ApifyClient(self.token)
        self.targets = targets or DEFAULT_TARGETS
        self.max_items = max_items

    def collect(self) -> list[TweetRaw]:
        run_input = {
            "startUrls": [{"url": t["url"]} for t in self.targets],
            "skipComments": True,
            "skipUserPosts": True,
            "skipCommunity": True,
            "maxItems": self.max_items,
            "maxPostCount": 10,
            "ignoreStartUrls": False,
            "includeNSFW": False,
            "sort": "top",
        }

        try:
            logger.info("Reddit: starting Apify run (%d subreddits)", len(self.targets))
            run = self.client.actor(ACTOR_ID).call(run_input=run_input)
            dataset_items = self.client.dataset(run["defaultDatasetId"]).list_items().items
        except Exception as e:
            logger.warning("Reddit Apify call failed: %s", e)
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
        # Min score per subreddit (lookup table)
        min_scores = {t["sub"].lower(): t.get("min_score", 100) for t in self.targets}

        out: list[TweetRaw] = []
        for item in dataset_items:
            try:
                # reddit-scraper-lite returns a mix of posts + comments + community pages.
                # Keep only items with both a title and an upVotes count (= posts).
                title = item.get("title") or ""
                if not title:
                    continue
                up_votes = int(item.get("upVotes", 0))
                comments = int(item.get("numberOfComments", 0))
                sub = (item.get("parsedCommunityName") or item.get("communityName") or "").strip()
                # Filter: minimum score for this sub
                if up_votes < min_scores.get(sub.lower(), 100):
                    continue
                # Filter: skip meta/weekly threads
                if any(p.search(title) for p in META_PATTERNS):
                    continue
                # Time filter
                created_raw = item.get("createdAt") or ""
                created_at = None
                if created_raw:
                    try:
                        created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                    except ValueError:
                        pass
                if created_at and created_at < cutoff:
                    continue

                body = item.get("body") or ""
                text = f"📰 r/{sub} · {title}"
                if body:
                    text += f". {body[:300]}"

                out.append(TweetRaw(
                    tweet_id=f"reddit_{item.get('id','')}",
                    author_handle=item.get("username") or f"r_{sub}",
                    author_name=f"Reddit · r/{sub}",
                    text=text,
                    created_at=created_at,
                    like_count=up_votes,
                    reply_count=comments,
                    source_url=item.get("url", ""),
                    is_rss=True,
                    author_tier="t2_community",
                ))
            except Exception as e:
                logger.debug("Reddit item parse failed: %s", e)
                continue

        logger.info("Reddit: kept %d posts from %d raw items", len(out), len(dataset_items))
        return out
