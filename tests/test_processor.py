"""Tests for processor layer."""

import json
from pathlib import Path

import numpy as np
import pytest

from src.schemas import EventCard, EventCategory, EventSource, TweetEmbedded, TweetRaw
from src.processor.clusterer import Clusterer
from src.processor.embedder import Embedder

FIXTURES = Path(__file__).parent / "fixtures"


def _make_tweet(text: str, handle: str = "test") -> TweetRaw:
    return TweetRaw(author_handle=handle, text=text)


def _make_embedded(text: str, embedding: list[float], handle: str = "test") -> TweetEmbedded:
    return TweetEmbedded(tweet=_make_tweet(text, handle), embedding=embedding)


class TestEmbedder:
    def test_cosine_similarity_matrix(self):
        embs = [[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]]
        mat = Embedder.cosine_similarity_matrix(embs)
        assert mat.shape == (3, 3)
        assert np.isclose(mat[0, 0], 1.0)
        assert np.isclose(mat[0, 2], 1.0)  # same vector
        assert np.isclose(mat[0, 1], 0.0)  # orthogonal

    def test_empty_tweets(self):
        embedder = Embedder.__new__(Embedder)
        result = embedder.embed_tweets([])
        assert result == []


class TestClusterer:
    def test_single_tweet(self):
        tweet = _make_embedded("test", [1.0, 0.0])
        clusterer = Clusterer()
        result = clusterer.cluster([tweet])
        assert len(result) == 1
        assert result[0].cluster_id == 0

    def test_similar_tweets_clustered(self):
        t1 = _make_embedded("GPT-5 release", [0.9, 0.1, 0.0])
        t2 = _make_embedded("GPT-5 launched", [0.85, 0.15, 0.0])
        t3 = _make_embedded("HBM4 chip news", [0.0, 0.1, 0.9])
        t4 = _make_embedded("HBM4 production", [0.0, 0.15, 0.85])

        clusterer = Clusterer(min_cluster_size=2, threshold=0.8)
        result = clusterer.cluster([t1, t2, t3, t4])

        # t1 and t2 should be in same cluster, t3 and t4 in another
        assert result[0].cluster_id == result[1].cluster_id
        assert result[2].cluster_id == result[3].cluster_id
        assert result[0].cluster_id != result[2].cluster_id

    def test_group_by_cluster(self):
        t1 = _make_embedded("a", [1.0])
        t2 = _make_embedded("b", [1.0])
        t1.cluster_id = 0
        t2.cluster_id = 0
        t3 = _make_embedded("c", [0.0])
        t3.cluster_id = 1

        groups = Clusterer.group_by_cluster([t1, t2, t3])
        assert len(groups) == 2
        assert len(groups[0]) == 2
        assert len(groups[1]) == 1


class TestEventCard:
    def test_load_from_fixture(self):
        with open(FIXTURES / "sample_events.json") as f:
            data = json.load(f)
        events = [EventCard(**e) for e in data]
        assert len(events) == 3
        assert events[0].importance == 9.2
        assert events[0].category == EventCategory.PRODUCT_LAUNCH
        assert len(events[0].sources) == 3
