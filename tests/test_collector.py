"""Tests for collector layer."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.schemas import TweetRaw, TrendingItem
from src.collector.apify_client import ApifyCollector
from src.collector.newsnow_client import NewsnowCollector

FIXTURES = Path(__file__).parent / "fixtures"


class TestApifyCollector:
    def test_parse_item(self):
        item = {
            "id": "12345",
            "author": {"userName": "sama", "name": "Sam Altman"},
            "text": "This is a test tweet with enough characters to pass filter.",
            "createdAt": "2026-02-18T06:00:00Z",
            "retweetCount": 100,
            "likeCount": 500,
            "replyCount": 20,
            "quoteCount": 10,
            "viewCount": 50000,
        }
        tweet = ApifyCollector._parse_item(item)
        assert tweet.author_handle == "sama"
        assert tweet.like_count == 500
        assert tweet.engagement == 630  # 100+500+20+10

    def test_parse_item_missing_fields(self):
        item = {"text": "Minimal tweet data but long enough to pass.", "authorHandle": "test"}
        tweet = ApifyCollector._parse_item(item)
        assert tweet.author_handle == "test"
        assert tweet.like_count == 0

    @patch("src.collector.apify_client.ApifyClient")
    def test_collect_filters_short_tweets(self, mock_apify_cls):
        mock_client = MagicMock()
        mock_dataset = MagicMock()
        mock_dataset.list_items.return_value.items = [
            {
                "id": "1",
                "author": {"userName": "a", "name": "A"},
                "text": "Short",
                "createdAt": "2026-02-18T06:00:00Z",
                "retweetCount": 0, "likeCount": 0,
                "replyCount": 0, "quoteCount": 0, "viewCount": 0,
            },
            {
                "id": "2",
                "author": {"userName": "b", "name": "B"},
                "text": "This is a long enough tweet to pass the minimum character filter easily.",
                "createdAt": "2026-02-18T06:00:00Z",
                "retweetCount": 10, "likeCount": 50,
                "replyCount": 5, "quoteCount": 2, "viewCount": 1000,
            },
        ]
        mock_client.actor.return_value.call.return_value = {"defaultDatasetId": "ds1"}
        mock_client.dataset.return_value = mock_dataset
        mock_apify_cls.return_value = mock_client

        collector = ApifyCollector(token="test-token")
        tweets = collector.collect("list123")
        assert len(tweets) == 1
        assert tweets[0].author_handle == "b"


class TestNewsnowCollector:
    def test_keyword_filter(self):
        collector = NewsnowCollector(keywords=["AI", "芯片"])
        assert collector._matches("新的AI模型发布") is True
        assert collector._matches("芯片价格上涨") is True
        assert collector._matches("今日天气预报") is False

    def test_parse_response_list(self):
        data = [
            {"title": "AI新突破", "url": "http://a.com", "source": "微博", "rank": 1},
            {"title": "天气预报", "url": "http://b.com", "source": "百度", "rank": 2},
        ]
        collector = NewsnowCollector()
        items = collector._parse_response(data)
        assert len(items) == 2
        assert items[0].title == "AI新突破"

    def test_parse_response_nested_dict(self):
        data = {
            "data": {
                "weibo": [{"title": "AI热搜", "rank": 1}],
                "zhihu": [{"title": "芯片讨论", "rank": 2}],
            }
        }
        collector = NewsnowCollector()
        items = collector._parse_response(data)
        assert len(items) == 2
        assert items[0].platform == "weibo"

    def test_filter_from_fixtures(self):
        with open(FIXTURES / "sample_trending.json") as f:
            raw = json.load(f)
        collector = NewsnowCollector()
        items = collector._parse_response(raw)
        filtered = [i for i in items if collector._matches(i.title)]
        # "今日菜价上涨" should be filtered out
        assert len(filtered) == 2
