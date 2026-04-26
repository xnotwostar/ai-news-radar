"""HuggingFace trending models collector.

Why it matters: HF download/like numbers are the closest thing we have to
"actual enterprise adoption" of open-weight models. Pure facts, no opinion.

Output is a list of :class:`TweetRaw` so the existing pipeline (cluster /
rank / report) can consume it without special-casing. Each "tweet" is one
trending model with its download/like delta.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from ..schemas import TweetRaw

logger = logging.getLogger(__name__)

TRENDING_URL = "https://huggingface.co/api/trending?type=model&limit=20"
DEFAULT_MIN_DOWNLOADS = 5_000      # filter out micro-models
DEFAULT_MIN_LIKES = 50


class HfCollector:
    """Fetch HuggingFace trending models snapshot."""

    def __init__(
        self,
        limit: int = 20,
        min_downloads: int = DEFAULT_MIN_DOWNLOADS,
        min_likes: int = DEFAULT_MIN_LIKES,
    ):
        self.limit = limit
        self.min_downloads = min_downloads
        self.min_likes = min_likes

    def collect(self) -> list[TweetRaw]:
        try:
            r = httpx.get(TRENDING_URL, timeout=15, follow_redirects=True)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.warning("HF trending fetch failed: %s", e)
            return []

        items = data.get("recentlyTrending", []) or []
        out: list[TweetRaw] = []
        now = datetime.now(timezone.utc)

        for entry in items[: self.limit]:
            repo = entry.get("repoData", {}) or {}
            model_id = repo.get("id", "")
            if not model_id:
                continue
            downloads = int(repo.get("downloads", 0))
            likes = int(repo.get("likes", 0))
            if downloads < self.min_downloads and likes < self.min_likes:
                continue

            org = model_id.split("/")[0] if "/" in model_id else model_id
            text = (
                f"🤗 {model_id} 进入 HuggingFace trending: "
                f"{downloads:,} 下载 · {likes:,} likes · "
                f"by {org}"
            )

            out.append(TweetRaw(
                tweet_id=f"hf_{model_id.replace('/', '_')}",
                author_handle=f"hf_{org}",
                author_name=f"HuggingFace · {org}",
                text=text,
                created_at=now,
                like_count=likes,
                view_count=downloads,   # repurpose view_count as download count
                source_url=f"https://huggingface.co/{model_id}",
                is_rss=True,
                author_tier="t0_primary",  # HF download data is fact-layer signal
            ))

        logger.info("HF trending: kept %d/%d models", len(out), len(items))
        return out
