"""Quick test: load saved event cards → generate report → push to DingTalk.

Skips Apify / embedding / clustering. Usage:
    python scripts/test_report.py                  # auto-detect latest events
    python scripts/test_report.py global_ai        # specify pipeline name
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── project imports ──────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.schemas import EventCard, LLMModelEntry
from src.generator.llm_client import LLMClient
from src.generator.report_writer import ReportWriter
from src.pusher.dingtalk import DingTalkPusher

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("test_report")

EVENTS_DIR = PROJECT_ROOT / "data" / "events"

# ── pipeline → webhook env / title mapping ───────────────────────────
PIPELINE_META = {
    "global_ai": {
        "webhook_env": "DINGTALK_GLOBAL_WEBHOOK",
        "title": "🌍 全球 AI 日报",
        "prompt_file": "prompts/report_global.txt",
    },
    "china_ai": {
        "webhook_env": "DINGTALK_CHINA_WEBHOOK",
        "title": "🇨🇳 中国 AI 日报",
        "prompt_file": "prompts/report_china.txt",
    },
}


def find_latest_events(pipeline: str | None = None) -> tuple[Path | None, str]:
    """Find the most recent events JSON file. Returns (path, pipeline_name)."""
    if not EVENTS_DIR.exists():
        return None, ""

    pattern = f"*_{pipeline}_events.json" if pipeline else "*_events.json"
    files = sorted(EVENTS_DIR.glob(pattern), reverse=True)
    if not files:
        return None, ""

    path = files[0]
    # Extract pipeline name from filename: 2026-02-18_global_ai_events.json → global_ai
    name = path.stem.replace("_events", "")  # 2026-02-18_global_ai
    parts = name.split("_", 1)
    pipeline_name = parts[1] if len(parts) > 1 else "global_ai"
    # Remove date prefix: "2026-02-18_global_ai" → strip date
    if len(parts[0]) == 10:  # YYYY-MM-DD
        pipeline_name = parts[1] if len(parts) > 1 else "global_ai"

    return path, pipeline_name


def main() -> None:
    pipeline_arg = sys.argv[1] if len(sys.argv) > 1 else None
    events_path, pipeline_name = find_latest_events(pipeline_arg)

    if not events_path:
        logger.error(
            "No event cards found in %s\n"
            "Run the full pipeline first: python -m src.pipeline global_ai",
            EVENTS_DIR,
        )
        sys.exit(1)

    logger.info("Loading events from: %s", events_path)
    logger.info("Pipeline: %s", pipeline_name)

    # ── Load events ──────────────────────────────────────────────────
    with open(events_path, encoding="utf-8") as f:
        raw = json.load(f)
    events = [EventCard(**e) for e in raw]
    logger.info("Loaded %d event cards", len(events))

    # ── Extract date from filename ───────────────────────────────────
    date_str = events_path.stem.split("_")[0]  # 2026-02-18

    # ── Load LLM chain ───────────────────────────────────────────────
    models_path = PROJECT_ROOT / "config" / "models.yaml"
    with open(models_path, encoding="utf-8") as f:
        models_raw = yaml.safe_load(f)
    report_chain = [LLMModelEntry(**m) for m in models_raw["report_generation"]]

    # ── Get pipeline config ──────────────────────────────────────────
    meta = PIPELINE_META.get(pipeline_name, PIPELINE_META["global_ai"])

    # ── Generate report ──────────────────────────────────────────────
    llm = LLMClient(chain=report_chain)
    writer = ReportWriter(llm)
    report = writer.generate_twitter_report(events, meta["prompt_file"], date_str)

    # ── Save report ──────────────────────────────────────────────────
    report_path = PROJECT_ROOT / "data" / "reports" / f"{date_str}_{pipeline_name}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    logger.info("Report saved: %s (%d chars)", report_path, len(report))

    # ── Push to DingTalk ─────────────────────────────────────────────
    try:
        pusher = DingTalkPusher(webhook_env=meta["webhook_env"])
        pusher.push(meta["title"], report)
        logger.info("DingTalk push succeeded")
    except Exception as e:
        logger.error("DingTalk push failed: %s", e)

    logger.info("Done.")


if __name__ == "__main__":
    main()
