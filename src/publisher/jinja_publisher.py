"""Jinja2-based HTML publisher.

Renders daily / weekly reports using the FT × Bloomberg × Stratechery design system
defined in ``templates/intel.css``. Takes structured EventCard data (not just
markdown) so layout can use category buckets, author tiers, info density, etc.

Coexists with the legacy ``html_publisher.HtmlPublisher`` (markdown-to-HTML).
Use this for the new design; the legacy one stays for backwards compat.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..schemas import EventCard, EventCategory, EventSource

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
DOCS_DIR = PROJECT_ROOT / "docs"


# Map event categories → which template section they go in
CATEGORY_SECTION = {
    EventCategory.FUNDING: "capital",
    EventCategory.MARKET: "capital",
    EventCategory.CHIP_HARDWARE: "capital",
    EventCategory.PRODUCT_LAUNCH: "product",
    EventCategory.OPEN_SOURCE: "product",
    EventCategory.PARTNERSHIP: "product",
    EventCategory.POLICY: "product",
    EventCategory.RESEARCH: "research",
    EventCategory.OTHER: "glance",
}

CATEGORY_TAG_LABEL = {
    EventCategory.FUNDING: "融资",
    EventCategory.MARKET: "市场",
    EventCategory.CHIP_HARDWARE: "供应链",
    EventCategory.PRODUCT_LAUNCH: "Release",
    EventCategory.OPEN_SOURCE: "开源",
    EventCategory.PARTNERSHIP: "Partnership",
    EventCategory.POLICY: "政策",
    EventCategory.RESEARCH: "Research",
    EventCategory.OTHER: "Other",
}

# author_tier → KOL section role mapping
KOL_ROLE_MAP = {
    "s_investor": ("vc", "Investor"),
    "s_founder": ("founder", "Founder"),
    "a_engineering": ("eng", "Engineer"),
    "b_research": ("researcher", "Researcher"),
}

# RSS source tier → glance footer tier label
RSS_TIER_BADGE = {
    "t0_primary": "T0",
    "t1_curated": "T1",
    "t2_community": "T2",
}

# Regex to pull money / percentage / parameter metrics out of titles
MONEY_RE = re.compile(r"\$\s?(\d+(?:\.\d+)?)\s?([KMB])\b", re.I)
PERCENT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s?%")
PARAM_RE = re.compile(r"\b(\d+\.?\d*)\s?[Bb]\b(?!\$)")  # 200B params, not $200B
COST_RE = re.compile(r"\$\s?(\d+(?:\.\d+)?)\s?[MmBb](?:illion)?")


class JinjaPublisher:
    """Render daily / weekly HTML using Jinja2 + structured EventCard data."""

    def __init__(self, templates_dir: Path | None = None):
        self.templates_dir = Path(templates_dir or TEMPLATES_DIR)
        self.env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=select_autoescape(["html", "j2"]),
            trim_blocks=False,
            lstrip_blocks=False,
        )

    def _sync_css_to(self, output_dir: Path) -> None:
        """Copy intel.css from templates/ to the public root so href='../intel.css' resolves."""
        src = self.templates_dir / "intel.css"
        if not src.exists():
            return
        # Public root = parent of output_dir if output_dir is .../reports/
        public_root = output_dir.parent if output_dir.name == "reports" else output_dir
        dst = public_root / "intel.css"
        try:
            if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                logger.info("Synced intel.css to %s", dst)
        except OSError as e:
            logger.warning("CSS sync failed: %s", e)

    # ───────────────────────────────────────────────────────── public API

    def render_daily(
        self,
        events: list[EventCard],
        report_markdown: str,
        date_str: str,
        issue_no: int = 1,
        sources_scanned: int = 0,
        output_path: Path | None = None,
    ) -> str:
        """Render daily report HTML. Returns the HTML string and writes to file
        if ``output_path`` is given."""
        ctx = self._build_daily_context(
            events, report_markdown, date_str, issue_no, sources_scanned,
        )
        template = self.env.get_template("daily.html.j2")
        html = template.render(**ctx)

        if output_path:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(html, encoding="utf-8")
            self._sync_css_to(output_path.parent)
            logger.info("Wrote daily HTML to %s (%d bytes)", output_path, len(html))

        return html

    def render_weekly(
        self,
        weekly_markdown: str,
        meta: dict,
        output_path: Path | None = None,
    ) -> str:
        """Render weekly HTML. The weekly markdown is currently used as a single
        thesis blob; richer structured rendering will come once we have a
        weekly EventCard equivalent."""
        ctx = self._build_weekly_context(weekly_markdown, meta)
        template = self.env.get_template("weekly.html.j2")
        html = template.render(**ctx)

        if output_path:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(html, encoding="utf-8")
            self._sync_css_to(output_path.parent)
            logger.info("Wrote weekly HTML to %s (%d bytes)", output_path, len(html))

        return html

    # ───────────────────────────────────────────────────────── daily context

    def _build_daily_context(
        self,
        events: list[EventCard],
        report_markdown: str,
        date_str: str,
        issue_no: int,
        sources_scanned: int,
    ) -> dict[str, Any]:
        # Bucket events
        capital, product, research, opinion, glance = [], [], [], [], []
        for e in events:
            section = CATEGORY_SECTION.get(e.category, "glance")
            if e.event_type == "opinion":
                opinion.append(e)
                continue
            if section == "capital":
                capital.append(e)
            elif section == "product":
                product.append(e)
            elif section == "research":
                research.append(e)
            else:
                glance.append(e)

        capital = self._sort_by_importance(capital)[:6]
        product = self._sort_by_importance(product)[:6]
        research = self._sort_by_importance(research)[:4]
        glance = self._sort_by_importance(glance)[:10]

        # Stats for transparency footer
        all_sources = [s for e in events for s in e.sources]
        tier_counts = Counter(s.author_tier for s in all_sources)
        densities = [s.info_density for s in all_sources if s.like_count > 0]

        stats = {
            "sources_scanned": sources_scanned or len(all_sources),
            "signals_surfaced": len(events),
            "authors_filtered": len({s.author for s in all_sources}),
            "tier_mix": {
                "t0": tier_counts.get("t0_primary", 0),
                "t1": tier_counts.get("t1_curated", 0),
                "t2": tier_counts.get("t2_community", 0),
            },
            "tier_s": tier_counts.get("s_investor", 0) + tier_counts.get("s_founder", 0),
            "unique_sources": len({s.author for s in all_sources}),
            "avg_density": f"{(sum(densities) / len(densities)) if densities else 0:.2f}",
        }

        return {
            "date": date_str,
            "year": date_str[:4],
            "weekday_short": _weekday_short(date_str),
            "issue_no": issue_no,
            "vol": _vol_for_year(int(date_str[:4])),
            "stats": stats,

            "core_judgment_paragraphs": _extract_core_judgment(report_markdown),
            "lede_read_time": "2 min read",
            "total_read_time": "5 min",
            "filed_time": datetime.utcnow().strftime("%H:%M UTC"),
            "confidence": "medium-high",

            "capital_entries": [self._event_to_entry(e, "02", i+1) for i, e in enumerate(capital)],
            "product_entries": [self._event_to_entry(e, "03", i+1) for i, e in enumerate(product)],
            "research_entries": [self._event_to_entry(e, "04", i+1) for i, e in enumerate(research)],

            # Quant placeholder — will populate when openrouter_collector is wired
            "quant_blocks": [],
            "quant_updated_at": "",
            "ticker_items": [],
            "ticker_note": "",

            "kol_quotes": [self._event_to_kol(e) for e in opinion[:8] if self._event_to_kol(e)],

            "glance_items": [self._event_to_glance(e) for e in glance],
        }

    # ───────────────────────────────────────────────────────── weekly context

    def _build_weekly_context(self, weekly_markdown: str, meta: dict) -> dict[str, Any]:
        # Phase 1 weekly: render the markdown into thesis paragraphs only.
        # Richer parsing (capital table / timeline / etc.) is Phase 2.
        thesis = _extract_weekly_thesis(weekly_markdown)
        return {
            "year": meta.get("year"),
            "week_num": int(meta.get("week", 0)),
            "date_start": meta.get("date_start"),
            "date_end": meta.get("date_end"),

            "stats": {
                "daily_count": meta.get("source_daily_count", 0),
                "total_events": meta.get("total_events", 0),
                "surfaced": meta.get("surfaced", 0),
                "threads": meta.get("threads", 0),
            },
            "thesis_paragraphs": thesis,
            "thesis_read_time": "3 min read",
            "total_read_time": "8 min",
            "filed_time": datetime.utcnow().strftime("%H:%M UTC"),
            "confidence": meta.get("confidence", "medium"),

            # Empty placeholders — populated when weekly_writer outputs structured data
            "capital_rows": [],
            "capital_total": "",
            "capital_summary": "",
            "narrative_days": [],
            "narrative_conclusion": "",
            "reversed_items": [],
            "continuity_topics": [],
            "product_winners": [],
            "research_long": [],
            "kol_quotes": [],
            "next_week_calendar": [],
            "week_glance": [],
        }

    # ───────────────────────────────────────────────────────── transformers

    @staticmethod
    def _sort_by_importance(events: list[EventCard]) -> list[EventCard]:
        return sorted(events, key=lambda e: e.importance, reverse=True)

    def _event_to_entry(self, e: EventCard, sec: str, idx: int) -> dict:
        # Extract metric: $XM / +X% / NB params from title or key_facts
        text = " ".join([e.title or ""] + (e.key_facts or []))
        metric, metric_is_money = _extract_metric(text)

        meta_html = self._build_meta(e)
        chips = [self._source_to_chip(s) for s in e.sources[:5]]
        why = e.key_facts[0] if e.key_facts else None

        # Strip leading emoji from title for display (template provides own emoji via tag)
        clean_name = _strip_leading_emoji(e.title or "")

        return {
            "name": clean_name,
            "metric": metric,
            "metric_is_money": metric_is_money,
            "tag": CATEGORY_TAG_LABEL.get(e.category, "Other"),
            "meta": meta_html,
            "take": e.analyst_angle or "",
            "chips": chips,
            "why": why,
        }

    def _event_to_kol(self, e: EventCard) -> dict | None:
        if not e.sources:
            return None

        # Speaker selection priority:
        # 1) substantive text (≥40 chars, not pure URL/emoji)
        # 2) high info_density (bm/likes > 0.15) OR S/A tier author
        # 3) higher tier_rank wins ties
        def speaker_score(s: EventSource) -> tuple[float, ...]:
            text_len = len(s.text or "")
            substantive = text_len >= 40 and not (s.text or "").strip().startswith("http")
            density_ok = s.info_density >= 0.15
            tier_top = s.author_tier in ("s_investor", "s_founder", "a_engineering")
            return (
                int(substantive),
                int(density_ok or tier_top),
                _tier_rank(s.author_tier),
                s.bookmark_count,
                text_len,
            )

        candidates = sorted(e.sources, key=speaker_score, reverse=True)
        # Drop candidates that fail the substantive check entirely
        candidates = [c for c in candidates if speaker_score(c)[0] == 1] or candidates
        best = candidates[0]

        role_key, role_label = KOL_ROLE_MAP.get(best.author_tier, ("eng", "Voice"))
        # KOL quote = the speaker's own words. analyst_angle is editorial commentary,
        # never use it as a "quote".
        if best.text and len(best.text.strip()) > 10:
            quote = best.text
        elif e.title:
            quote = e.title
        else:
            quote = e.analyst_angle

        return {
            "role": role_key,
            "role_label": role_label,
            "handle": best.author.lstrip("@") if best.author else "unknown",
            "source_meta": _format_source_meta(best, e),
            "quote": _clean_quote(quote),
            "chips": [{
                "label": f"{best.like_count} likes" if best.like_count else "RSS",
                "tip": f"bm:{best.bookmark_count} likes:{best.like_count} ratio:{best.info_density:.2f}",
            }] if best.like_count else [],
        }

    def _event_to_glance(self, e: EventCard) -> dict:
        # Time hint (HH:MM if event_time)
        time_str = "—"
        if e.event_time:
            time_str = e.event_time.strftime("%H:%M")

        src_label = (e.sources[0].author if e.sources else "—")
        tier = e.sources[0].author_tier if e.sources else None
        tier_badge = self._tier_to_badge(tier)

        return {
            "time": time_str,
            "text": _strip_leading_emoji(e.title or "")[:120],
            "source": src_label,
            "tier": tier_badge,
        }

    def _source_to_chip(self, s: EventSource) -> dict:
        tier = s.author_tier or ""
        badge = self._tier_to_badge(tier) or ""
        label = s.author[:30] if s.author else "source"
        tip = f"bm:{s.bookmark_count} likes:{s.like_count} engage:{s.engagement} tier:{tier}"
        return {
            "label": label,
            "handle": "@" + s.author.lstrip("@") if s.author else None,
            "tip": tip,
            "tier": badge.lower() if badge else "",
        }

    def _tier_to_badge(self, tier: str | None) -> str | None:
        if not tier:
            return None
        if tier in RSS_TIER_BADGE:
            return RSS_TIER_BADGE[tier]
        if tier.startswith("s_"):
            return "S"
        if tier.startswith("a_"):
            return "A"
        if tier.startswith("b_"):
            return "B"
        return None

    @staticmethod
    def _build_meta(e: EventCard) -> str:
        bits: list[str] = []
        if e.key_facts:
            for f in e.key_facts[:3]:
                bits.append(_html_safe(f))
        if not bits:
            return ""
        return ' <span style="color:var(--ink-3)">·</span> '.join(bits)


# ───────────────────────────────────────────────────────────────────────────
# helpers
# ───────────────────────────────────────────────────────────────────────────

def _weekday_short(date_str: str) -> str:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d.weekday()]
    except ValueError:
        return ""

def _vol_for_year(year: int) -> str:
    # Simple Roman vol counter starting at I from 2024
    n = year - 2023
    return ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"][max(0, n - 1)] if n <= 10 else str(n)


def _strip_leading_emoji(s: str) -> str:
    if not s:
        return s
    # Strip leading non-letter / non-digit chars (emoji + whitespace) up to 4 chars
    cleaned = re.sub(r"^[^\w\u4e00-\u9fff]+", "", s, count=1).strip()
    return cleaned or s


def _extract_metric(text: str) -> tuple[str | None, bool]:
    """Return (metric, is_money) extracted from a title / key_facts."""
    if m := MONEY_RE.search(text):
        return m.group(0), True
    if m := PERCENT_RE.search(text):
        return m.group(0), False
    if m := PARAM_RE.search(text):
        return m.group(0), False
    return None, False


def _tier_rank(tier: str) -> int:
    """Higher = more authoritative for KOL selection."""
    return {
        "s_investor": 5,
        "s_founder": 5,
        "a_engineering": 3,
        "b_research": 2,
        "t0_primary": 4,
        "t1_curated": 3,
        "t2_community": 1,
        "downweight": -1,
        "unknown": 0,
    }.get(tier, 0)


def _format_source_meta(s: EventSource, e: EventCard) -> str:
    parts: list[str] = []
    if e.event_time:
        parts.append(e.event_time.strftime("%m-%d %H:%M"))
    if s.like_count:
        parts.append(f"{s.like_count} likes · {s.bookmark_count} bm")
    return " · ".join(parts) if parts else ""


def _clean_quote(s: str) -> str:
    if not s:
        return ""
    # Strip enclosing quotes if any
    s = s.strip().strip("“”\"'：:、。 ")
    return s[:280]


def _html_safe(s: str) -> str:
    return s.replace("<", "&lt;").replace(">", "&gt;")


def _extract_core_judgment(markdown: str) -> list[str]:
    """Pull the 今日核心判断 paragraphs out of the report markdown.

    The report writer uses a heading like "### 今日核心判断" followed by 1-2 paragraphs
    until the next "###" or horizontal rule.
    """
    if not markdown:
        return []
    lines = markdown.splitlines()
    out: list[str] = []
    in_section = False
    for ln in lines:
        s = ln.strip()
        if "今日核心判断" in s and (s.startswith("#") or s.startswith("**")):
            in_section = True
            continue
        if in_section:
            if s.startswith("---") or s.startswith("###"):
                break
            if s and not s.startswith("#"):
                # Strip blockquote markers
                cleaned = s.lstrip("> ").strip()
                if cleaned:
                    out.append(cleaned)
    return out or ["（核心判断待生成）"]


def _extract_weekly_thesis(markdown: str) -> list[str]:
    """Pull the Weekly Thesis paragraphs from the weekly markdown."""
    if not markdown:
        return []
    lines = markdown.splitlines()
    out: list[str] = []
    in_section = False
    for ln in lines:
        s = ln.strip()
        if ("Thesis" in s or "本周" in s and "判断" in s) and s.startswith("#"):
            in_section = True
            continue
        # Match the second-level heading "## Thesis" or "## 本周 Thesis" too
        if ("Thesis" in s or "thesis" in s) and s.startswith("##"):
            in_section = True
            continue
        if in_section:
            if s.startswith("##") or s.startswith("---"):
                if out:
                    break
                continue
            if s and not s.startswith("#"):
                cleaned = s.lstrip("> ").strip()
                if cleaned:
                    out.append(cleaned)
    return out or [markdown[:600]]
