"""Weekly report generator.

Reads the past 7 days of daily reports (markdown files) from
``data/reports/`` and synthesizes a weekly digest with 5 unique angles
that the daily can't give you:

  1. Capital flow weekly summary
  2. Narrative arc (Mon → Sun convergence)
  3. Reversed / debunked stories
  4. Continuity signals (5+/7 days = real trend)
  5. Next week catalysts

Output is markdown that gets written into the Obsidian vault by
:class:`ObsidianPublisher`, then pushed via the GitHub Action.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from .llm_client import LLMClient

logger = logging.getLogger(__name__)


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_REPORTS_DIR = PROJECT_ROOT / "data" / "reports"


def _iso_week(dt: date) -> tuple[int, int]:
    iso = dt.isocalendar()
    return iso.year, iso.week


class WeeklyWriter:
    """Synthesize a weekly report from the past 7 days of daily reports."""

    def __init__(
        self,
        llm: LLMClient,
        prompt_file: str = "prompts/report_weekly.txt",
        report_dir: Path | None = None,
    ):
        self.llm = llm
        self.prompt_file = prompt_file
        self.report_dir = Path(report_dir) if report_dir else DATA_REPORTS_DIR

    def generate(
        self,
        end_date: date | None = None,
        report_pattern: str = "*_global_ai.md",
    ) -> tuple[str, dict]:
        """Generate a weekly report ending on ``end_date`` (default: today).

        Returns ``(markdown, meta)``. ``meta`` includes week_year, week_num,
        date_start, date_end, source_count for downstream Obsidian frontmatter.
        """
        end = end_date or date.today()
        start = end - timedelta(days=6)
        year, week = _iso_week(end)

        daily_reports = self._collect_daily_reports(start, end, report_pattern)

        if len(daily_reports) < 3:
            logger.warning(
                "Only %d daily reports found in %s..%s — weekly may be sparse",
                len(daily_reports), start, end,
            )

        system_prompt, oneshot = self._load_prompt()
        user_prompt = self._build_user_prompt(daily_reports, start, end, year, week, oneshot)

        markdown = self.llm.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.6,
            max_tokens=8192,
        )

        # Force the title line so it always matches the week label
        title_line = f"# 🌍 AI 周报 | Week {week:02d} · {start.isoformat()} → {end.isoformat()}"
        if markdown.lstrip().startswith("#"):
            # Replace LLM's first heading with our canonical one
            split = markdown.split("\n", 1)
            markdown = title_line + "\n" + (split[1] if len(split) > 1 else "")
        else:
            markdown = title_line + "\n\n" + markdown

        meta = {
            "year": year,
            "week": week,
            "date_start": start.isoformat(),
            "date_end": end.isoformat(),
            "source_daily_count": len(daily_reports),
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }
        return markdown, meta

    def _collect_daily_reports(
        self, start: date, end: date, pattern: str,
    ) -> list[tuple[date, str]]:
        """Return list of ``(date, markdown_text)`` for daily reports in range."""
        if not self.report_dir.exists():
            logger.warning("Report dir %s does not exist", self.report_dir)
            return []

        items: list[tuple[date, str]] = []
        for path in sorted(self.report_dir.glob(pattern)):
            stem = path.stem  # e.g. 2026-04-24_global_ai
            try:
                d = datetime.strptime(stem.split("_")[0], "%Y-%m-%d").date()
            except ValueError:
                continue
            if start <= d <= end:
                try:
                    text = path.read_text(encoding="utf-8")
                except Exception as e:
                    logger.warning("Failed to read %s: %s", path, e)
                    continue
                items.append((d, text))

        items.sort(key=lambda p: p[0])
        return items

    def _load_prompt(self) -> tuple[str, str]:
        """Load system / oneshot from a ---SYSTEM--- / ---ONESHOT--- divided file."""
        path = PROJECT_ROOT / self.prompt_file
        text = path.read_text(encoding="utf-8")

        system_part = ""
        oneshot_part = ""
        if "---SYSTEM---" in text:
            after_sys = text.split("---SYSTEM---", 1)[1]
            if "---ONESHOT---" in after_sys:
                system_part, oneshot_part = after_sys.split("---ONESHOT---", 1)
            else:
                system_part = after_sys
        else:
            system_part = text

        return system_part.strip(), oneshot_part.strip()

    @staticmethod
    def _build_user_prompt(
        daily_reports: list[tuple[date, str]],
        start: date,
        end: date,
        year: int,
        week: int,
        oneshot: str,
    ) -> str:
        bundle = [
            f"# 输入：过去 7 天的日报全文（{start.isoformat()} → {end.isoformat()}）",
            f"# 共 {len(daily_reports)} 天有日报\n",
        ]
        for d, md in daily_reports:
            weekday = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d.weekday()]
            bundle.append(f"\n\n=== {d.isoformat()} ({weekday}) ===\n")
            bundle.append(md)

        bundle.append("\n\n---\n\n")
        bundle.append(f"# 任务\n\n请按下方范例的结构，写一份 Week {week:02d} ({start.isoformat()} → {end.isoformat()}) 的 AI 周报。\n\n")
        bundle.append("范例（仅参考结构与口吻，**事实与口径必须基于上方真实日报**，不要复用范例事实）：\n\n")
        bundle.append(oneshot)

        return "\n".join(bundle)
