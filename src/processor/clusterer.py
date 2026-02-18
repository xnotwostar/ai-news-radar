"""HDBSCAN semantic clustering for tweets."""

from __future__ import annotations

import logging

import hdbscan
import numpy as np

from ..schemas import TweetEmbedded

logger = logging.getLogger(__name__)


class Clusterer:
    """Cluster embedded tweets using HDBSCAN on cosine distance."""

    def __init__(self, min_cluster_size: int = 2, threshold: float = 0.82):
        self.min_cluster_size = min_cluster_size
        self.threshold = threshold

    def cluster(self, tweets: list[TweetEmbedded]) -> list[TweetEmbedded]:
        """Assign cluster_id to each tweet. -1 = noise (singleton event)."""
        if len(tweets) < 2:
            for i, t in enumerate(tweets):
                t.cluster_id = i
            return tweets

        embeddings = np.array([t.embedding for t in tweets], dtype=np.float32)

        # Normalize for cosine distance
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        normalized = embeddings / norms

        # HDBSCAN with cosine → use precomputed distance matrix
        distance_matrix = 1 - (normalized @ normalized.T)
        np.fill_diagonal(distance_matrix, 0)
        distance_matrix = np.clip(distance_matrix, 0, 2)

        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=self.min_cluster_size,
            metric="precomputed",
            cluster_selection_epsilon=1 - self.threshold,
        )
        labels = clusterer.fit_predict(distance_matrix)

        n_clusters = len(set(labels) - {-1})
        n_noise = (labels == -1).sum()
        logger.info(
            "Clustered %d tweets → %d clusters + %d noise points",
            len(tweets), n_clusters, n_noise,
        )

        # Assign labels; promote noise points to singleton clusters
        next_cluster_id = max(labels) + 1 if len(labels) > 0 else 0
        for i, tweet in enumerate(tweets):
            if labels[i] == -1:
                tweet.cluster_id = next_cluster_id
                next_cluster_id += 1
            else:
                tweet.cluster_id = int(labels[i])

        return tweets

    @staticmethod
    def group_by_cluster(tweets: list[TweetEmbedded]) -> dict[int, list[TweetEmbedded]]:
        """Group tweets by cluster_id."""
        groups: dict[int, list[TweetEmbedded]] = {}
        for t in tweets:
            groups.setdefault(t.cluster_id, []).append(t)
        return groups
