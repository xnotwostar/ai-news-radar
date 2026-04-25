"""Weekly report CLI entry — generates a weekly digest and writes to Obsidian vault.

Usage:
    python -m src.weekly                     # ISO week ending today
    python -m src.weekly --date 2026-04-26   # week ending given date
    python -m src.weekly --no-push           # write but don't git push
    python -m src.weekly --vault /path/to/vault
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from .generator import LLMClient, WeeklyWriter
from .pipeline import load_configs
from .publisher import ObsidianPublisher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate weekly AI report and publish to Obsidian.")
    parser.add_argument(
        "--date", type=str, default=None,
        help="End date of the week, YYYY-MM-DD. Defaults to today.",
    )
    parser.add_argument(
        "--vault", type=str, default=None,
        help="Override vault path. Defaults to $OBSIDIAN_VAULT_PATH or ~/Documents/Obsidian Vault.",
    )
    parser.add_argument(
        "--no-push", action="store_true",
        help="Skip git commit + push (still writes the file).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the generated markdown, don't write or push.",
    )
    parser.add_argument(
        "--report-pattern", type=str, default="*_global_ai.md",
        help="Glob to find daily reports under data/reports/ (default: *_global_ai.md)",
    )
    args = parser.parse_args(argv)

    end_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date else date.today()
    )

    # Load LLM chain config
    _pipelines, report_chain, _embed = load_configs()
    llm = LLMClient(chain=report_chain)

    writer = WeeklyWriter(llm=llm)
    logger.info("Generating weekly ending %s ...", end_date)
    markdown, meta = writer.generate(end_date=end_date, report_pattern=args.report_pattern)

    if args.dry_run:
        print("=" * 60)
        print("DRY RUN — no file written")
        print("=" * 60)
        print(markdown)
        print()
        print("META:", meta)
        return 0

    publisher = ObsidianPublisher(
        vault_path=args.vault,
        git_push=not args.no_push,
    )
    target = publisher.publish_weekly(markdown, meta)
    logger.info("Weekly published to %s", target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
