"""Re-render today's daily report HTML using JinjaPublisher (new FT/Bloomberg theme).

Usage:
    python scripts/render_jinja.py                  # rerender today
    python scripts/render_jinja.py 2026-04-25       # specific date
    python scripts/render_jinja.py --pipeline china_ai
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date as date_type
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.publisher.jinja_publisher import JinjaPublisher
from src.schemas import EventCard, EventSource, EventCategory

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def load_events(events_path: Path) -> list[EventCard]:
    raw = json.loads(events_path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "events" in raw:
        raw = raw["events"]
    out: list[EventCard] = []
    for d in raw:
        # Reconstruct sources
        sources = []
        for s in d.get("sources", []):
            sources.append(EventSource(
                author=s.get("author", ""),
                text=s.get("text", ""),
                engagement=s.get("engagement", 0),
                bookmark_count=s.get("bookmark_count", 0),
                like_count=s.get("like_count", 0),
                author_tier=s.get("author_tier", "unknown"),
                url=s.get("url", ""),
            ))
        try:
            cat = EventCategory(d.get("category", "other"))
        except ValueError:
            cat = EventCategory.OTHER
        out.append(EventCard(
            event_id=d.get("event_id", ""),
            title=d.get("title", ""),
            category=cat,
            importance=float(d.get("importance", 5.0)),
            sources=sources,
            key_facts=d.get("key_facts", []),
            analyst_angle=d.get("analyst_angle", ""),
            cluster_size=d.get("cluster_size", 1),
            event_type=d.get("event_type", "news"),
        ))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default=date_type.today().isoformat())
    ap.add_argument("--pipeline", default="global_ai", help="pipeline name (global_ai/china_ai)")
    ap.add_argument("--issue", type=int, default=147, help="issue number")
    args = ap.parse_args()

    date_str = args.date
    name = args.pipeline

    events_path = ROOT / "data" / "events" / f"{date_str}_{name}_events.json"
    md_path = ROOT / "data" / "reports" / f"{date_str}_{name}.md"
    raw_path = ROOT / "data" / "raw" / f"{date_str}_{name}.json"

    if not events_path.exists():
        sys.exit(f"events file missing: {events_path}")
    if not md_path.exists():
        sys.exit(f"markdown report missing: {md_path}")

    events = load_events(events_path)
    report_md = md_path.read_text(encoding="utf-8")
    sources_scanned = 0
    if raw_path.exists():
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        sources_scanned = len(raw) if isinstance(raw, list) else 0

    out_path = ROOT / "docs" / "reports" / f"{date_str}_{name}_v2.html"

    pub = JinjaPublisher()
    html = pub.render_daily(
        events=events,
        report_markdown=report_md,
        date_str=date_str,
        issue_no=args.issue,
        sources_scanned=sources_scanned,
        output_path=out_path,
    )

    logger.info("Loaded %d events, wrote %d-byte HTML to %s", len(events), len(html), out_path)
    print(f"\n✓ Rendered: {out_path}")
    print(f"  Open: open {out_path}")


if __name__ == "__main__":
    main()
