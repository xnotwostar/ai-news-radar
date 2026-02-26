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
from dotenv import load_dotenv

load_dotenv()

from .schemas import (
    EmbeddingConfig,
    EventCard,
    LLMModelEntry,
    PipelineConfig,
    TrendingItem,
    TweetRaw,
)
import hashlib

from .collector import ApifyCollector, NewsnowCollector, RssCollector
from .processor import Clusterer, Embedder, EventBuilder, Ranker
from .generator import LLMClient, ReportWriter
from .publisher import HtmlPublisher
from .pusher import DingTalkPusher, ServerChanPusher

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
    trending_config: PipelineConfig | None = None,
) -> str | None:
    """Execute a Twitter-based pipeline (global_ai or china_ai).

    For china_ai, if *trending_config* is provided, also fetches Newsnow
    trending data and appends deduplicated results to the Twitter report.
    """
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

    # Step 1.5: Merge RSS feeds (global_ai only)
    if name == "global_ai":
        try:
            rss_collector = RssCollector(hours=24)
            rss_items = rss_collector.collect()
            _save_raw(
                [{"title": r.title, "summary": r.summary, "url": r.url,
                  "source": r.source, "published": str(r.published)} for r in rss_items],
                f"{date_str}_{name}_rss.json",
            )
            for r in rss_items:
                tweets.append(TweetRaw(
                    tweet_id=hashlib.md5(r.url.encode()).hexdigest()[:16],
                    author_handle=r.source.replace(" ", ""),
                    author_name=r.source,
                    text=f"{r.title}. {r.summary}" if r.summary else r.title,
                    created_at=r.published,
                    source_url=r.url,
                    is_rss=True,
                ))
            logger.info("Merged %d RSS items into %d total items", len(rss_items), len(tweets))
        except Exception as e:
            logger.warning("RSS collection failed, continuing with Twitter only: %s", e)

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
    clusters = clusterer.group_by_cluster(embedded)

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

    if name == "china_ai" and trending_config is not None:
        # Fetch Newsnow trending data, deduplicate, and append to Twitter report
        trending_items = _collect_trending(trending_config, date_str)
        report = writer.generate_merged_china_report(
            events, trending_items, config.generation.prompt_file, date_str,
        )
    else:
        report = writer.generate_twitter_report(events, config.generation.prompt_file, date_str)

    # Force correct report title (LLM may not follow one-shot exactly)
    title_line_map = {
        "global_ai": f"# 🌍 全球AI洞察 | {date_str}",
        "china_ai": f"# 🇨🇳 中文圈AI洞察 | {date_str}",
    }
    if name in title_line_map:
        # Replace the first # heading line
        lines = report.split("\n", 1)
        report = title_line_map[name] + "\n" + (lines[1] if len(lines) > 1 else "")

    _save_report(report, f"{date_str}_{name}.md")

    # Step 7: Publish HTML for GitHub Pages
    report_url = None
    pages_base = os.environ.get("PAGES_URL", "").rstrip("/")
    try:
        title_map = {"global_ai": "🌍 全球AI洞察", "china_ai": "🇨🇳 中文圈AI洞察"}
        pub = HtmlPublisher()
        pub.publish(report, title_map.get(name, name), date_str, name)
        if pages_base:
            report_url = f"{pages_base}/reports/{date_str}_{name}.html"
            logger.info("Report URL: %s", report_url)
    except Exception as e:
        logger.error("HTML publish failed for %s: %s", name, e)

    # Step 8: Push to DingTalk (skip if --no-push)
    if not os.environ.get("NO_PUSH"):
        try:
            pusher = DingTalkPusher(webhook_env=config.push.webhook_env)
            title_map = {"global_ai": "🌍 全球AI洞察", "china_ai": "🇨🇳 中文圈AI洞察"}
            pusher.push(title_map.get(name, name), report, report_url=report_url)
        except Exception as e:
            logger.error("DingTalk push failed for %s: %s", name, e)

        # Step 9: Push to ServerChan (WeChat)
        if config.push.serverchan_key_env and os.environ.get(config.push.serverchan_key_env):
            try:
                sc_pusher = ServerChanPusher(key_env=config.push.serverchan_key_env)
                title_map = {"global_ai": "🌍 全球AI洞察", "china_ai": "🇨🇳 中文圈AI洞察"}
                sc_pusher.push(title_map.get(name, name), report, report_url=report_url)
            except Exception as e:
                logger.error("ServerChan push failed for %s: %s", name, e)
    else:
        logger.info("Skipping push for %s (NO_PUSH=1)", name)

    logger.info("Pipeline %s completed: %d events → report", name, len(events))
    return report


def _collect_trending(config: PipelineConfig, date_str: str) -> list[TrendingItem]:
    """Collect Newsnow trending data (used by china_ai merged pipeline)."""
    logger.info("Fetching Newsnow trending data for china_ai merge...")
    newsnow = NewsnowCollector(keywords=config.source.keywords)
    items: list[TrendingItem] = newsnow.collect()
    _save_raw(
        [i.model_dump() for i in items],
        f"{date_str}_trending.json",
    )
    logger.info("Collected %d trending items for merge", len(items))
    return items


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
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    logger.info("Saved raw data: %s", path)


def _save_events(events: list[EventCard], filename: str) -> None:
    path = DATA_DIR / "events" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([e.model_dump(mode="json") for e in events], f, ensure_ascii=False, indent=2)
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

    # Execute in order: global → china (with trending merged in)
    # trending data is automatically merged into china_ai, no separate push
    execution_order = ["global_ai", "china_ai"]
    push_interval = 30  # seconds between pipeline pushes

    # Get trending config so china_ai can pull its data
    trending_config = pipelines.get("trending")

    for i, name in enumerate(execution_order):
        if name not in to_run:
            continue

        config = to_run[name]

        try:
            if config.source.type == "apify_list":
                # For china_ai, pass trending_config to merge Newsnow data
                t_cfg = trending_config if name == "china_ai" else None
                run_twitter_pipeline(name, config, report_chain, embed_cfg, date_str, t_cfg)
            else:
                logger.error("Unknown source type: %s", config.source.type)
        except Exception as e:
            logger.error("Pipeline %s FAILED: %s", name, e, exc_info=True)
            continue

        # Wait between pushes (per design doc)
        remaining = [n for n in execution_order[i+1:] if n in to_run]
        if remaining:
            logger.info("Waiting %ds before next pipeline...", push_interval)
            time.sleep(push_interval)

    logger.info("All pipelines completed.")


def push_only(date_str: str | None = None) -> None:
    """Read saved reports from data/reports/ and push to DingTalk + ServerChan.

    Used after Pages deployment to send notifications with valid URLs.
    """
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    pipelines, _, _ = load_configs()
    pages_base = os.environ.get("PAGES_URL", "").rstrip("/")
    title_map = {"global_ai": "🌍 全球AI洞察", "china_ai": "🇨🇳 中文圈AI洞察"}
    push_interval = 30

    for i, name in enumerate(["global_ai", "china_ai"]):
        if name not in pipelines:
            continue
        config = pipelines[name]

        report_path = DATA_DIR / "reports" / f"{date_str}_{name}.md"
        if not report_path.exists():
            logger.warning("No report found: %s, skipping push", report_path)
            continue

        report = report_path.read_text(encoding="utf-8")
        report_url = f"{pages_base}/reports/{date_str}_{name}.html" if pages_base else None
        title = title_map.get(name, name)

        logger.info("Pushing %s (%d chars), URL: %s", name, len(report), report_url)

        try:
            pusher = DingTalkPusher(webhook_env=config.push.webhook_env)
            pusher.push(title, report, report_url=report_url)
        except Exception as e:
            logger.error("DingTalk push failed for %s: %s", name, e)

        if config.push.serverchan_key_env and os.environ.get(config.push.serverchan_key_env):
            try:
                sc_pusher = ServerChanPusher(key_env=config.push.serverchan_key_env)
                sc_pusher.push(title, report, report_url=report_url)
            except Exception as e:
                logger.error("ServerChan push failed for %s: %s", name, e)

        if i == 0:
            logger.info("Waiting %ds before next push...", push_interval)
            time.sleep(push_interval)

    logger.info("Push-only completed.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--push-only":
        push_only(args[1] if len(args) > 1 else None)
    else:
        if "--no-push" in args:
            os.environ["NO_PUSH"] = "1"
            args = [a for a in args if a != "--no-push"]
        main(args if args else None)
