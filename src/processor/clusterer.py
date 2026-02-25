"""HDBSCAN semantic clustering for tweets."""

from __future__ import annotations

import logging

import hdbscan
import numpy as np

from ..schemas import TweetEmbedded

logger = logging.getLogger(__name__)

NOISE_TOP_K = 15
NOISE_BATCH_SIZE = 5


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

    @staticmethod
    def group_by_cluster(tweets: list[TweetEmbedded]) -> tuple[dict[int, list[TweetEmbedded]], dict]:
        """Group tweets by cluster_id with smart noise filtering.

        Noise tweets (cluster_id == -1) are sorted by engagement; only the
        top NOISE_TOP_K are kept and batched into pseudo-clusters of
        NOISE_BATCH_SIZE. Low-value noise is discarded entirely.

        Returns (groups, stats) where stats contains clustering diagnostics.
        """
        groups: dict[int, list[TweetEmbedded]] = {}
        noise: list[TweetEmbedded] = []

        for t in tweets:
            if t.cluster_id == -1:
                noise.append(t)
            else:
                groups.setdefault(t.cluster_id, []).append(t)

        real_cluster_count = len(groups)
        real_cluster_sizes = [len(v) for v in groups.values()]

        # Smart noise filtering: keep top-K by engagement
        noise.sort(key=lambda t: t.tweet.engagement, reverse=True)
        kept_noise = noise[:NOISE_TOP_K]
        discarded = len(noise) - len(kept_noise)
        if discarded > 0:
            logger.info("Discarded %d low-value noise tweets, kept top %d", discarded, len(kept_noise))

        # Pack kept noise into pseudo-clusters of NOISE_BATCH_SIZE
        pseudo_cluster_count = 0
        next_id = max(groups.keys(), default=-1) + 1
        for i in range(0, len(kept_noise), NOISE_BATCH_SIZE):
            batch = kept_noise[i : i + NOISE_BATCH_SIZE]
            for t in batch:
                t.cluster_id = next_id
            groups[next_id] = batch
            next_id += 1
            pseudo_cluster_count += 1

        stats = {
            "total_tweets_input": len(tweets),
            "real_clusters": real_cluster_count,
            "real_cluster_sizes": real_cluster_sizes,
            "noise_total": len(noise),
            "noise_kept": len(kept_noise),
            "noise_discarded": discarded,
            "pseudo_clusters": pseudo_cluster_count,
            "total_clusters": real_cluster_count + pseudo_cluster_count,
        }

        logger.info(
            "Cluster stats: %d tweets → %d real clusters (sizes: %s) + "
            "%d noise (%d kept → %d pseudo) = %d total clusters",
            len(tweets), real_cluster_count, real_cluster_sizes,
            len(noise), len(kept_noise), pseudo_cluster_count,
            stats["total_clusters"],
        )

        return groups, stats
