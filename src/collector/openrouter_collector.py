"""OpenRouter collector — model availability + pricing signals.

What it actually delivers:
- Newly added models in the last 24-72h (created timestamp)
- Significant pricing changes (vs previous snapshot — TODO)
- New context-window / capability additions

What it does NOT deliver (yet):
- Live usage rankings (the /rankings page is React Server Components,
  needs a headless browser. Tracked as future enhancement.)

For now this gives us the "what's available to API users" signal,
which is a leading indicator of "what real users will pay for next week."

Data source: https://openrouter.ai/api/v1/models (public, no auth needed,
355+ models with pricing/architecture/context_length).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

from ..schemas import TweetRaw

logger = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/models"
SNAPSHOT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "raw" / "_openrouter_snapshot.json"


class OpenRouterCollector:
    """Track new model launches + pricing deltas on OpenRouter."""

    def __init__(self, lookback_hours: int = 72):
        self.lookback = timedelta(hours=lookback_hours)

    def collect(self) -> list[TweetRaw]:
        try:
            r = httpx.get(API_URL, timeout=20)
            r.raise_for_status()
            current = {m["id"]: m for m in r.json().get("data", [])}
        except Exception as e:
            logger.warning("OpenRouter fetch failed: %s", e)
            return []

        # Load previous snapshot (if any) to detect what's NEW vs the last run
        previous: dict = {}
        if SNAPSHOT_PATH.exists():
            try:
                previous = {m["id"]: m for m in json.loads(SNAPSHOT_PATH.read_text())}
            except Exception:
                pass

        # Always save current as next baseline
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(json.dumps(list(current.values()), indent=2))

        out: list[TweetRaw] = []
        cutoff = datetime.now(timezone.utc) - self.lookback
        now = datetime.now(timezone.utc)

        is_first_run = not previous
        if is_first_run:
            logger.info(
                "OpenRouter first run — only emitting models created in last %dh "
                "(future runs will detect new arrivals via snapshot diff)",
                int(self.lookback.total_seconds() / 3600),
            )

        # Detect new arrivals (in current but not previous, or fresh by created date)
        for model_id, m in current.items():
            created_ts = m.get("created", 0)
            created_at = datetime.fromtimestamp(created_ts, tz=timezone.utc) if created_ts else None
            # First run: rely on recency only (avoid emitting all 355 models)
            # Subsequent runs: anything not in previous is new
            is_new_to_snapshot = (not is_first_run) and (model_id not in previous)
            is_recent = created_at and created_at >= cutoff

            if not (is_new_to_snapshot or is_recent):
                continue

            org = model_id.split("/")[0]
            name = m.get("name") or model_id
            ctx = m.get("context_length", 0)
            pricing = m.get("pricing") or {}
            prompt_price = float(pricing.get("prompt", "0") or 0) * 1_000_000
            comp_price = float(pricing.get("completion", "0") or 0) * 1_000_000

            text = (
                f"🚀 OpenRouter 上架 {name}: "
                f"{ctx:,} context · "
                f"${prompt_price:.2f}/M prompt · ${comp_price:.2f}/M completion"
            )

            out.append(TweetRaw(
                tweet_id=f"or_{model_id.replace('/', '_')}",
                author_handle=f"or_{org}",
                author_name=f"OpenRouter · {org}",
                text=text,
                created_at=created_at or now,
                view_count=ctx,
                source_url=f"https://openrouter.ai/{model_id}",
                is_rss=True,
                author_tier="t0_primary",
            ))

        # Detect pricing changes (model present in both, prompt/completion changed)
        for model_id, m in current.items():
            if model_id not in previous:
                continue
            p_old = previous[model_id].get("pricing", {}) or {}
            p_new = m.get("pricing", {}) or {}
            old_prompt = float(p_old.get("prompt", "0") or 0) * 1_000_000
            new_prompt = float(p_new.get("prompt", "0") or 0) * 1_000_000
            if old_prompt > 0 and abs(new_prompt - old_prompt) / old_prompt > 0.10:
                # >10% price change
                pct = (new_prompt - old_prompt) / old_prompt * 100
                direction = "降价" if pct < 0 else "涨价"
                org = model_id.split("/")[0]
                text = (
                    f"💰 OpenRouter {direction}: {m.get('name', model_id)} "
                    f"prompt 从 ${old_prompt:.2f}/M → ${new_prompt:.2f}/M ({pct:+.1f}%)"
                )
                out.append(TweetRaw(
                    tweet_id=f"or_pricedelta_{model_id.replace('/','_')}",
                    author_handle=f"or_{org}",
                    author_name=f"OpenRouter · {org}",
                    text=text,
                    created_at=now,
                    source_url=f"https://openrouter.ai/{model_id}",
                    is_rss=True,
                    author_tier="t0_primary",
                ))

        logger.info(
            "OpenRouter: %d signals (current=%d models, prev=%d)",
            len(out), len(current), len(previous),
        )
        return out
