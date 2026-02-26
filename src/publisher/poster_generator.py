"""Poster image generator — renders a mobile-friendly summary poster from markdown reports."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Emoji → category label + color for poster event list
_EMOJI_CATEGORIES = {
    "\U0001f534": ("重磅", "#f85149"),   # 🔴
    "\U0001f680": ("产品", "#3fb950"),    # 🚀
    "\U0001f52c": ("研究", "#bc8cff"),    # 🔬
    "\U0001f4b0": ("融资", "#d29922"),    # 💰
    "\U0001f527": ("芯片", "#58a6ff"),    # 🔧
    "\U0001f91d": ("合作", "#58a6ff"),    # 🤝
    "\U0001f310": ("开源", "#3fb950"),    # 🌐
    "\U0001f4dc": ("政策", "#bc8cff"),    # 📜
    "\U0001f4ca": ("市场", "#d29922"),    # 📊
}

_EVENT_EMOJIS = set(_EMOJI_CATEGORIES.keys())

POSTER_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: 750px;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text",
                 "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif;
    background: #0d1117;
    color: #e6edf3;
    -webkit-font-smoothing: antialiased;
  }}
  .poster {{ width: 750px; }}

  /* === Header === */
  .header {{
    background: linear-gradient(135deg, #0d1117 0%, #161b22 50%, #1a1e2e 100%);
    padding: 48px 40px 36px;
    text-align: center;
    position: relative;
  }}
  .header::before {{
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 4px;
    background: linear-gradient(90deg, #58a6ff, #bc8cff, #f0883e);
  }}
  .badge {{
    display: inline-block;
    background: rgba(88,166,255,0.1);
    border: 1px solid rgba(88,166,255,0.2);
    border-radius: 20px;
    padding: 4px 16px;
    font-size: 12px;
    color: #58a6ff;
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 14px;
  }}
  .header h1 {{
    font-size: 28px;
    font-weight: 700;
    background: linear-gradient(135deg, #e6edf3, #58a6ff);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: 8px;
    line-height: 1.3;
  }}
  .date {{
    font-size: 13px;
    color: #6e7681;
    letter-spacing: 1px;
  }}

  /* === Core Judgment === */
  .core-section {{
    padding: 28px 36px 24px;
  }}
  .section-label {{
    font-size: 12px;
    color: #58a6ff;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .section-label::after {{
    content: '';
    flex: 1;
    height: 1px;
    background: rgba(88,166,255,0.15);
  }}
  .core-text {{
    font-size: 15px;
    line-height: 1.8;
    color: #c9d1d9;
    background: rgba(88,166,255,0.04);
    border: 1px solid rgba(88,166,255,0.1);
    border-radius: 10px;
    padding: 18px 20px;
  }}

  /* === Events === */
  .events-section {{
    padding: 4px 36px 28px;
  }}
  .event-item {{
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 12px 16px;
    margin-bottom: 8px;
    background: #1c2128;
    border: 1px solid #30363d;
    border-radius: 8px;
    transition: border-color 0.2s;
  }}
  .event-emoji {{
    font-size: 20px;
    flex-shrink: 0;
    margin-top: 1px;
  }}
  .event-content {{
    flex: 1;
    min-width: 0;
  }}
  .event-title {{
    font-size: 14px;
    font-weight: 600;
    color: #e6edf3;
    line-height: 1.5;
  }}
  .event-tag {{
    display: inline-block;
    font-size: 10px;
    padding: 1px 8px;
    border-radius: 4px;
    font-weight: 500;
    margin-left: 6px;
    vertical-align: middle;
  }}

  /* === Stats Bar === */
  .stats-bar {{
    display: flex;
    justify-content: center;
    gap: 32px;
    padding: 20px 36px;
    border-top: 1px solid #30363d;
  }}
  .stat {{
    text-align: center;
  }}
  .stat-number {{
    font-size: 24px;
    font-weight: 700;
    color: #58a6ff;
  }}
  .stat-label {{
    font-size: 11px;
    color: #6e7681;
    margin-top: 2px;
  }}

  /* === Footer === */
  .footer {{
    padding: 20px 36px;
    text-align: center;
    border-top: 1px solid #30363d;
  }}
  .brand {{
    font-size: 12px;
    color: #484f58;
    letter-spacing: 1px;
  }}
  .hint {{
    font-size: 11px;
    color: #6e7681;
    margin-top: 4px;
  }}
</style>
</head>
<body>
<div class="poster">
  <div class="header">
    <div class="badge">AI News Radar</div>
    <h1>{title}</h1>
    <div class="date">{date_display}</div>
  </div>
  <div class="core-section">
    <div class="section-label">今日核心判断</div>
    <div class="core-text">{core_judgment}</div>
  </div>
  <div class="events-section">
    <div class="section-label">重点事件</div>
    {event_items_html}
  </div>
  <div class="stats-bar">
    <div class="stat">
      <div class="stat-number">{total_events}</div>
      <div class="stat-label">事件总数</div>
    </div>
    <div class="stat">
      <div class="stat-number">{critical_count}</div>
      <div class="stat-label">重磅事件</div>
    </div>
    <div class="stat">
      <div class="stat-number">{section_count}</div>
      <div class="stat-label">板块数</div>
    </div>
  </div>
  <div class="footer">
    <div class="brand">AI News Radar · 自动采集 · 智能分析 · 每日推送</div>
    <div class="hint">向下滑动查看完整报告 ↓</div>
  </div>
</div>
</body>
</html>"""


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class PosterGenerator:
    """Generate mobile-friendly poster images from markdown reports."""

    def __init__(self, docs_dir: str | None = None):
        base = Path(docs_dir) if docs_dir else PROJECT_ROOT / "docs"
        self.posters_dir = base / "posters"
        self.posters_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        markdown_report: str,
        title: str,
        date_str: str,
        pipeline_name: str,
    ) -> str:
        """Generate poster PNG from markdown report, return file path."""
        parsed = self._parse_report(markdown_report)
        html = self._build_poster_html(parsed, title, date_str)
        poster_path = self.posters_dir / f"{date_str}_{pipeline_name}_poster.png"
        self._render_to_png(html, str(poster_path))
        logger.info("Generated poster: %s", poster_path)
        return str(poster_path)

    @staticmethod
    def _parse_report(markdown: str) -> dict:
        """Extract core judgment and top events from markdown."""
        lines = markdown.split("\n")
        result = {
            "core_judgment": "",
            "events": [],       # [(emoji, title, color)]
            "total_events": 0,
            "critical_count": 0,
            "sections": set(),
        }

        # Extract core judgment
        in_core = False
        core_parts: list[str] = []
        for line in lines:
            stripped = line.strip()
            if "核心判断" in stripped:
                in_core = True
                continue
            if in_core:
                if stripped == "---" or stripped.startswith("## "):
                    break
                if stripped:
                    core_parts.append(stripped)

        core_text = " ".join(core_parts)
        # Remove markdown bold markers
        core_text = re.sub(r'\*\*(.+?)\*\*', r'\1', core_text)
        # Truncate for poster
        if len(core_text) > 150:
            for sep in ("。", "，", "；", "、"):
                cut = core_text[:150].rfind(sep)
                if cut > 80:
                    core_text = core_text[:cut + 1] + "..."
                    break
            else:
                core_text = core_text[:147] + "..."
        result["core_judgment"] = core_text

        # Extract events and stats
        section_headings = set()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("## "):
                section_headings.add(stripped[3:].strip())
            if not stripped:
                continue
            first_char = stripped[0] if stripped else ""
            if first_char in _EVENT_EMOJIS:
                result["total_events"] += 1
                if first_char == "\U0001f534":  # 🔴
                    result["critical_count"] += 1
                # Extract bold title
                m = re.search(r'\*\*(.+?)\*\*', stripped)
                if m:
                    title_text = m.group(1)
                    if len(title_text) > 45:
                        title_text = title_text[:42] + "..."
                    _, color = _EMOJI_CATEGORIES.get(first_char, ("", "#58a6ff"))
                    result["events"].append((first_char, title_text, color))

        result["events"] = result["events"][:6]
        result["sections"] = section_headings
        return result

    @staticmethod
    def _build_poster_html(parsed: dict, title: str, date_str: str) -> str:
        """Build poster HTML from parsed report data."""
        # Date display
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
            date_display = f"{dt.year}年{dt.month}月{dt.day}日 · {weekdays[dt.weekday()]}"
        except Exception:
            date_display = date_str

        # Build event items HTML
        event_items: list[str] = []
        for emoji, event_title, color in parsed["events"]:
            label, _ = _EMOJI_CATEGORIES.get(emoji, ("", color))
            tag_html = ""
            if label:
                tag_html = (
                    f'<span class="event-tag" style="'
                    f'color: {color}; background: {color}18; '
                    f'border: 1px solid {color}33;">{label}</span>'
                )
            event_items.append(
                f'<div class="event-item">'
                f'<span class="event-emoji">{emoji}</span>'
                f'<div class="event-content">'
                f'<span class="event-title">{_html_escape(event_title)}{tag_html}</span>'
                f'</div></div>'
            )

        # Count sections (exclude 速览 and 专家视角)
        news_sections = [s for s in parsed["sections"]
                         if not any(k in s for k in ("速览", "专家", "核心", "热搜"))]

        return POSTER_TEMPLATE.format(
            title=_html_escape(title),
            date_display=_html_escape(date_display),
            core_judgment=_html_escape(parsed["core_judgment"]),
            event_items_html="\n".join(event_items),
            total_events=parsed["total_events"],
            critical_count=parsed["critical_count"],
            section_count=len(news_sections),
        )

    @staticmethod
    def _render_to_png(html: str, output_path: str) -> None:
        """Render HTML to PNG using Playwright."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 750, "height": 100})
            page.set_content(html, wait_until="load")
            # Get actual poster height
            height = page.evaluate("document.querySelector('.poster').offsetHeight")
            page.set_viewport_size({"width": 750, "height": height})
            page.screenshot(path=output_path, full_page=False, type="png")
            browser.close()
        logger.info("Rendered poster PNG: %s (%d px height)", output_path, height)
