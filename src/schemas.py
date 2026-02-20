"""Pydantic schemas for AI News Radar data structures."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Priority(str, Enum):
    MUST = "🔴必监控"
    RECOMMEND = "🟡推荐"
    FOLLOW = "🟢关注"


class EventCategory(str, Enum):
    PRODUCT_LAUNCH = "product_launch"
    RESEARCH = "research"
    FUNDING = "funding"
    CHIP_HARDWARE = "chip_hardware"
    POLICY = "policy"
    PARTNERSHIP = "partnership"
    OPEN_SOURCE = "open_source"
    MARKET = "market"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Raw Data
# ---------------------------------------------------------------------------

class TweetRaw(BaseModel):
    """Single tweet from Apify scraper."""
    tweet_id: str = ""
    author_handle: str
    author_name: str = ""
    text: str
    created_at: Optional[datetime] = None
    retweet_count: int = 0
    like_count: int = 0
    reply_count: int = 0
    quote_count: int = 0
    view_count: int = 0

    @property
    def engagement(self) -> int:
        return self.retweet_count + self.like_count + self.reply_count + self.quote_count

    @property
    def url(self) -> str:
        handle = self.author_handle.lstrip("@")
        if self.tweet_id:
            return f"https://x.com/{handle}/status/{self.tweet_id}"
        return f"https://x.com/{handle}"


class TrendingItem(BaseModel):
    """Single item from newsnow API."""
    title: str
    url: str = ""
    platform: str = ""
    rank: int = 0
    hot_value: Optional[float] = None
    published_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Processed Data
# ---------------------------------------------------------------------------

class TweetEmbedded(BaseModel):
    """Tweet with embedding vector attached."""
    tweet: TweetRaw
    embedding: list[float] = Field(default_factory=list)
    cluster_id: int = -1  # -1 = noise


class EventSource(BaseModel):
    author: str
    text: str
    engagement: int = 0
    url: str = ""


class EventCard(BaseModel):
    """Core data unit — one event aggregated from multiple tweets."""
    event_id: str
    title: str
    category: EventCategory = EventCategory.OTHER
    importance: float = 5.0
    sources: list[EventSource] = Field(default_factory=list)
    key_facts: list[str] = Field(default_factory=list)
    analyst_angle: str = ""
    cluster_size: int = 1
    event_time: Optional[datetime] = None
    event_type: str = "news"


# ---------------------------------------------------------------------------
# Pipeline Config (parsed from YAML)
# ---------------------------------------------------------------------------

class SourceConfig(BaseModel):
    type: str  # "apify_list" | "newsnow_api"
    list_id: Optional[str] = None
    keywords: Optional[list[str]] = None


class ProcessingConfig(BaseModel):
    embed_model: Optional[str] = None
    cluster_threshold: float = 0.82
    cluster_algorithm: str = "hdbscan"


class GenerationConfig(BaseModel):
    prompt_file: str


class PushConfig(BaseModel):
    webhook_env: str


class PipelineConfig(BaseModel):
    source: SourceConfig
    processing: ProcessingConfig
    generation: GenerationConfig
    push: PushConfig


# ---------------------------------------------------------------------------
# LLM Model Config (parsed from models.yaml)
# ---------------------------------------------------------------------------

class LLMModelEntry(BaseModel):
    provider: str  # "anthropic" | "dashscope" | "deepseek"
    model: str
    priority: int = 1
    timeout: int = 60


class EmbeddingConfig(BaseModel):
    provider: str
    model: str
    dimensions: int = 1024
