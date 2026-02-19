"""Text embedding via Alibaba DashScope text-embedding-v4."""

from __future__ import annotations

import logging
import os
from typing import Sequence

import httpx
import numpy as np

from ..schemas import TweetEmbedded, TweetRaw

logger = logging.getLogger(__name__)

DASHSCOPE_EMBED_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
BATCH_SIZE = 10  # DashScope batch limit
DEFAULT_MODEL = "text-embedding-v4"
DEFAULT_DIMENSIONS = 1024


class Embedder:
    """Batch-embed tweets using DashScope text-embedding-v4."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        dimensions: int = DEFAULT_DIMENSIONS,
    ):
        self.api_key = api_key or os.environ["DASHSCOPE_API_KEY"]
        self.model = model
        self.dimensions = dimensions

    def embed_tweets(self, tweets: list[TweetRaw]) -> list[TweetEmbedded]:
        """Embed all tweets and return TweetEmbedded list."""
        if not tweets:
            return []

        texts = [t.text for t in tweets]
        all_embeddings = self._batch_embed(texts)

        results: list[TweetEmbedded] = []
        for tweet, emb in zip(tweets, all_embeddings):
            results.append(TweetEmbedded(tweet=tweet, embedding=emb))

        logger.info("Embedded %d tweets (%d dimensions)", len(results), self.dimensions)
        return results

    def _batch_embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Call DashScope embedding API in batches."""
        all_embs: list[list[float]] = []

        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i : i + BATCH_SIZE]
            embs = self._call_api(batch)
            all_embs.extend(embs)

        return all_embs

    def _call_api(self, texts: list[str]) -> list[list[float]]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "input": texts,
            "encoding_format": "float",
        }

        resp = httpx.post(
            DASHSCOPE_EMBED_URL,
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        # Sort by index to maintain order
        embeddings_data = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in embeddings_data]

    @staticmethod
    def cosine_similarity_matrix(embeddings: list[list[float]]) -> np.ndarray:
        """Compute pairwise cosine similarity matrix."""
        arr = np.array(embeddings, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        normalized = arr / norms
        return normalized @ normalized.T
