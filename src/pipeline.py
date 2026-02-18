"""Pipeline Engine — unified orchestration for all three pipelines.

Usage:
    python -m src.pipeline                   # Run all pipelines
    python -m src.pipeline global_ai         # Run single pipeline
    python -m src.pipeline china_ai trending # Run specific pipelines
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .schemas import (
    EmbeddingConfig,
    EventCard,
    LLMModelEntry,
    PipelineConfig,
    TrendingItem,
    TweetRaw,
)
from .collector import ApifyCollector, NewsnowCollector
from .processor import Clusterer, Embedder, EventBuilder, Ranker
from .generator import LLMClient, ReportWriter
from .pusher import DingTalkPusher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


def _resolve_env(value: str) -> str:
    """Resolve ${ENV_VAR} references in config values."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        env_key = value[2:-1]
        return os.environ.get(env_key, value)
    return value


def load_configs() -> tuple[dict[str, PipelineConfig], list[LLMModelEntry], EmbeddingConfig]:
    """Load pipeline.yaml and models.yaml."""
    pipeline_path = PROJECT_ROOT / "config" / "pipeline.yaml"
    models_path = PROJECT_ROOT / "config" / "models.yaml"

    with open(pipeline_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    pipelines: dict[str, PipelineConfig] = {}
    for name, cfg in raw["pipelines"].items():
        # Resolve env vars in source config
        if cfg["source"].get("list_id"):
            cfg["source"]["list_id"] = _resolve_env(cfg["source"]["list_id"])
        pipelines[name] = PipelineConfig(**cfg)

    with open(models_path, encoding="utf-8") as f:
        models_raw = yaml.safe_load(f)

    report_chain = [LLMModelEntry(**m) for m in models_raw["report_generation"]]
    embed_cfg = EmbeddingConfig(**models_raw["embedding"])

    return pipelines, report_chain, embed_cfg


def run_twitter_pipeline(
    name: str,
    config: PipelineConfig,
    report_chain: list[LLMModelEntry],
    embed_cfg: EmbeddingConfig,
    date_str: str,
) -> str | None:
    """Execute a Twitter-based pipeline (global_ai or china_ai)."""
    logger.info("=" * 60)
    logger.info("PIPELINE: %s", name)
    logger.info("=" * 60)

    # Step 1: Collect
    collector = ApifyCollector()
    tweets: list[TweetRaw] = collector.collect(config.source.list_id or "")
    _save_raw(tweets, f"{date_str}_{name}.json")

    if not tweets:
        logger.warning("No tweets collected for %s, skipping", name)
        return None

    # Step 2: Embed
    embedder = Embedder(
        model=embed_cfg.model,
        dimensions=embed_cfg.dimensions,
    )
    embedded = embedder.embed_tweets(tweets)

    # Step 3: Cluster
    clusterer = Clusterer(
        threshold=config.processing.cluster_threshold,
    )
    embedded = clusterer.cluster(embedded)
    clusters = Clusterer.group_by_cluster(embedded)

    # Step 4: Build Event Cards
    builder = EventBuilder()
    events: list[EventCard] = builder.build_events(clusters, date_str.replace("-", ""))
    _save_events(events, f"{date_str}_{name}_events.json")

    # Step 5: Rank
    ranker = Ranker()
    events = ranker.rank(events)

    # Step 6: Generate Report
    llm = LLMClient(chain=report_chain)
    writer = ReportWriter(llm)
    report = writer.generate_twitter_report(events, config.generation.prompt_file, date_str)
    _save_report(report, f"{date_str}_{name}.md")

    # Step 7: Push
    try:
        pusher = DingTalkPusher(webhook_env=config.push.webhook_env)
        title_map = {"global_ai": "🌍 全球 AI 日报", "china_ai": "🇨🇳 中国 AI 日报"}
        pusher.push(title_map.get(name, name), report)
    except Exception as e:
        logger.error("Push failed for %s: %s", name, e)

    logger.info("Pipeline %s completed: %d events → report", name, len(events))
    return report


def run_trending_pipeline(
    config: PipelineConfig,
    report_chain: list[LLMModelEntry],
    date_str: str,
) -> str | None:
    """Execute the trending (newsnow) pipeline."""
    logger.info("=" * 60)
    logger.info("PIPELINE: trending")
    logger.info("=" * 60)

    # Step 1: Collect
    collector = NewsnowCollector(keywords=config.source.keywords)
    items: list[TrendingItem] = collector.collect()
    _save_raw(
        [i.model_dump() for i in items],
        f"{date_str}_trending.json",
    )

    if not items:
        logger.warning("No trending items collected, skipping")
        return None

    # Step 2: Generate Report (no embedding/clustering for trending)
    llm = LLMClient(chain=report_chain)
    writer = ReportWriter(llm)
    report = writer.generate_trending_report(items, config.generation.prompt_file, date_str)
    _save_report(report, f"{date_str}_trending.md")

    # Step 3: Push
    try:
        pusher = DingTalkPusher(webhook_env=config.push.webhook_env)
        pusher.push("🔥 国内 AI 热搜速递", report)
    except Exception as e:
        logger.error("Push failed for trending: %s", e)

    logger.info("Trending pipeline completed: %d items → report", len(items))
    return report


# ---------------------------------------------------------------------------
# Data persistence helpers
# ---------------------------------------------------------------------------

def _save_raw(data, filename: str) -> None:
    path = DATA_DIR / "raw" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if isinstance(data, list) and data and hasattr(data[0], "model_dump"):
            json.dump([d.model_dump(mode="json") for d in data], f, ensure_ascii=False, indent=2)
        else:
            json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("Saved raw data: %s", path)


def _save_events(events: list[EventCard], filename: str) -> None:
    path = DATA_DIR / "events" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([e.model_dump() for e in events], f, ensure_ascii=False, indent=2)
    logger.info("Saved events: %s", path)


def _save_report(report: str, filename: str) -> None:
    path = DATA_DIR / "reports" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    logger.info("Saved report: %s", path)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main(pipeline_names: list[str] | None = None) -> None:
    """Run specified pipelines (or all if none specified)."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logger.info("AI News Radar — %s", date_str)

    pipelines, report_chain, embed_cfg = load_configs()

    # Determine which pipelines to run
    if pipeline_names:
        to_run = {k: v for k, v in pipelines.items() if k in pipeline_names}
    else:
        to_run = pipelines

    if not to_run:
        logger.error("No matching pipelines found. Available: %s", list(pipelines.keys()))
        sys.exit(1)

    # Execute in order: global → china → trending
    execution_order = ["global_ai", "china_ai", "trending"]
    push_interval = 30  # seconds between pipeline pushes

    for i, name in enumerate(execution_order):
        if name not in to_run:
            continue

        config = to_run[name]

        try:
            if config.source.type == "apify_list":
                run_twitter_pipeline(name, config, report_chain, embed_cfg, date_str)
            elif config.source.type == "newsnow_api":
                run_trending_pipeline(config, report_chain, date_str)
            else:
                logger.error("Unknown source type: %s", config.source.type)
        except Exception as e:
            logger.error("Pipeline %s FAILED: %s", name, e, exc_info=True)
            # Continue with next pipeline even if one fails
            continue

        # Wait between pushes (per design doc)
        remaining = [n for n in execution_order[i+1:] if n in to_run]
        if remaining:
            logger.info("Waiting %ds before next pipeline...", push_interval)
            time.sleep(push_interval)

    logger.info("All pipelines completed.")


if __name__ == "__main__":
    args = sys.argv[1:]
    main(args if args else None)
