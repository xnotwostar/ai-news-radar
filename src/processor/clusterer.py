"""HDBSCAN semantic clustering for tweets."""

from __future__ import annotations

import logging

import hdbscan
import numpy as np

from ..schemas import TweetEmbedded

logger = logging.getLogger(__name__)

NOISE_TOP_K = 15
NOISE_BATCH_SIZE = 5
MAX_CLUSTER_SIZE = 30  # Clusters larger than this get sub-clustered


class Clusterer:
    """Cluster embedded tweets using HDBSCAN on cosine distance."""

    def __init__(self, min_cluster_size: int = 2, threshold: float = 0.82):
        self.min_cluster_size = min_cluster_size
        self.threshold = threshold

    def cluster(self, tweets: list[TweetEmbedded]) -> list[TweetEmbedded]:
        """Assign cluster_id to each tweet. -1 = noise."""
        if len(tweets) < 2:
            for i, t in enumerate(tweets):
                t.cluster_id = i
            return tweets

        embeddings = np.array([t.embedding for t in tweets], dtype=np.float64)

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

        # Keep noise as -1, don't promote to singleton clusters
        for i, tweet in enumerate(tweets):
            tweet.cluster_id = int(labels[i])

        return tweets

    def _sub_cluster(self, tweets: list[TweetEmbedded], cluster_id: int) -> list[list[TweetEmbedded]]:
        """Re-cluster a mega-cluster with a tighter threshold to find sub-topics."""
        tighter_threshold = min(self.threshold + 0.08, 0.95)

        embeddings = np.array([t.embedding for t in tweets], dtype=np.float64)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        normalized = embeddings / norms

        distance_matrix = 1 - (normalized @ normalized.T)
        np.fill_diagonal(distance_matrix, 0)
        distance_matrix = np.clip(distance_matrix, 0, 2)

        sub_clusterer = hdbscan.HDBSCAN(
            min_cluster_size=self.min_cluster_size,
            metric="precomputed",
            cluster_selection_epsilon=1 - tighter_threshold,
        )
        sub_labels = sub_clusterer.fit_predict(distance_matrix)

        n_sub = len(set(sub_labels) - {-1})
        if n_sub > 1:
            logger.info(
                "Sub-clustered mega-cluster %d (%d tweets) → %d sub-clusters (threshold=%.2f)",
                cluster_id, len(tweets), n_sub, tighter_threshold,
            )
            sub_groups: dict[int, list[TweetEmbedded]] = {}
            noise: list[TweetEmbedded] = []
            for i, t in enumerate(tweets):
                label = int(sub_labels[i])
                if label == -1:
                    noise.append(t)
                else:
                    sub_groups.setdefault(label, []).append(t)
            # Distribute noise tweets into the nearest sub-cluster
            result = list(sub_groups.values())
            if noise and result:
                for t in noise:
                    result[0].append(t)
            return result

        # Sub-clustering failed — fallback: split by engagement into groups of NOISE_BATCH_SIZE
        logger.info(
            "Sub-clustering failed for mega-cluster %d (%d tweets), splitting by engagement",
            cluster_id, len(tweets),
        )
        tweets_sorted = sorted(tweets, key=lambda t: t.tweet.engagement, reverse=True)
        chunks: list[list[TweetEmbedded]] = []
        for i in range(0, len(tweets_sorted), MAX_CLUSTER_SIZE):
            chunks.append(tweets_sorted[i : i + MAX_CLUSTER_SIZE])
        return chunks

    def group_by_cluster(self, tweets: list[TweetEmbedded]) -> dict[int, list[TweetEmbedded]]:
        """Group tweets by cluster_id with mega-cluster splitting and noise filtering.

        Mega-clusters (> MAX_CLUSTER_SIZE) are sub-clustered with a tighter
        threshold to extract sub-topics. Noise tweets are filtered and batched.
        """
        groups: dict[int, list[TweetEmbedded]] = {}
        noise: list[TweetEmbedded] = []

        for t in tweets:
            if t.cluster_id == -1:
                noise.append(t)
            else:
                groups.setdefault(t.cluster_id, []).append(t)

        # Split mega-clusters into sub-clusters
        final_groups: dict[int, list[TweetEmbedded]] = {}
        next_id = 0
        for cid, members in groups.items():
            if len(members) > MAX_CLUSTER_SIZE:
                sub_clusters = self._sub_cluster(members, cid)
                for sub in sub_clusters:
                    for t in sub:
                        t.cluster_id = next_id
                    final_groups[next_id] = sub
                    next_id += 1
            else:
                for t in members:
                    t.cluster_id = next_id
                final_groups[next_id] = members
                next_id += 1

        # Smart noise filtering: keep top-K by engagement
        noise.sort(key=lambda t: t.tweet.engagement, reverse=True)
        kept_noise = noise[:NOISE_TOP_K]
        discarded = len(noise) - len(kept_noise)
        if discarded > 0:
            logger.info("Discarded %d low-value noise tweets, kept top %d", discarded, len(kept_noise))

        # Pack kept noise into pseudo-clusters of NOISE_BATCH_SIZE
        for i in range(0, len(kept_noise), NOISE_BATCH_SIZE):
            batch = kept_noise[i : i + NOISE_BATCH_SIZE]
            for t in batch:
                t.cluster_id = next_id
            final_groups[next_id] = batch
            next_id += 1

        return final_groups
