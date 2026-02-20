"""HTML report publisher for GitHub Pages deployment."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — {date}</title>
<style>
  :root {{
    --bg-primary: #0d1117;
    --bg-secondary: #161b22;
    --bg-card: #1c2128;
    --bg-hover: #252d38;
    --border: #30363d;
    --text-primary: #e6edf3;
    --text-secondary: #8b949e;
    --text-muted: #6e7681;
    --accent-blue: #58a6ff;
    --accent-orange: #f0883e;
    --accent-green: #3fb950;
    --accent-red: #f85149;
    --accent-purple: #bc8cff;
    --accent-yellow: #d29922;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue",
                 "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif;
    background: var(--bg-primary);
    color: var(--text-primary);
    line-height: 1.8;
    font-size: 15px;
    -webkit-font-smoothing: antialiased;
  }}
  .header {{
    background: linear-gradient(135deg, #0d1117 0%, #161b22 50%, #1a1e2e 100%);
    border-bottom: 1px solid var(--border);
    padding: 2.5rem 1.5rem 2rem;
    text-align: center;
    position: relative;
    overflow: hidden;
  }}
  .header::before {{
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
    background: linear-gradient(90deg, var(--accent-blue), var(--accent-purple), var(--accent-orange));
  }}
  .header-badge {{
    display: inline-block;
    background: rgba(88, 166, 255, 0.1);
    border: 1px solid rgba(88, 166, 255, 0.2);
    border-radius: 20px;
    padding: 4px 14px;
    font-size: 12px;
    color: var(--accent-blue);
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-bottom: 12px;
  }}
  .header h1 {{
    font-size: 1.8rem;
    font-weight: 700;
    background: linear-gradient(135deg, var(--text-primary), var(--accent-blue));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: 8px;
  }}
  .header .date {{ color: var(--text-secondary); font-size: 0.9rem; }}
  .container {{ max-width: 780px; margin: 0 auto; padding: 24px 16px 60px; }}
  .section {{ margin-bottom: 32px; }}
  .section-title {{
    font-size: 1.15rem;
    font-weight: 700;
    color: var(--text-primary);
    padding-bottom: 10px;
    margin-bottom: 16px;
    border-bottom: 1px solid var(--border);
  }}
  .core-judgment {{
    background: linear-gradient(135deg, rgba(88,166,255,0.06), rgba(188,140,255,0.06));
    border: 1px solid rgba(88,166,255,0.15);
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 32px;
    line-height: 1.9;
    font-size: 14.5px;
  }}
  .event-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 18px 20px;
    margin-bottom: 14px;
    transition: border-color 0.2s, background 0.2s;
  }}
  .event-card:hover {{ border-color: rgba(88,166,255,0.3); background: var(--bg-hover); }}
  .event-card.critical {{ border-left: 3px solid var(--accent-red); }}
  .event-card.normal {{ border-left: 3px solid var(--accent-blue); }}
  .event-title {{
    font-size: 15px;
    font-weight: 600;
    color: var(--text-primary);
    margin-bottom: 8px;
    line-height: 1.5;
  }}
  .event-title a {{
    color: var(--text-primary);
    text-decoration: none;
    border-bottom: 1px dashed rgba(88,166,255,0.4);
    transition: color 0.2s, border-color 0.2s;
  }}
  .event-title a:hover {{
    color: var(--accent-blue);
    border-bottom-color: var(--accent-blue);
  }}
  .tag {{
    display: inline-block;
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 4px;
    font-weight: 500;
    margin-right: 6px;
    vertical-align: middle;
  }}
  .tag-critical {{ background: rgba(248,81,73,0.15); color: var(--accent-red); border: 1px solid rgba(248,81,73,0.25); }}
  .tag-product {{ background: rgba(63,185,80,0.12); color: var(--accent-green); border: 1px solid rgba(63,185,80,0.2); }}
  .tag-research {{ background: rgba(188,140,255,0.12); color: var(--accent-purple); border: 1px solid rgba(188,140,255,0.2); }}
  .tag-funding {{ background: rgba(210,153,34,0.12); color: var(--accent-yellow); border: 1px solid rgba(210,153,34,0.2); }}
  .tag-chip {{ background: rgba(88,166,255,0.12); color: var(--accent-blue); border: 1px solid rgba(88,166,255,0.2); }}
  .event-highlight {{
    background: rgba(88,166,255,0.06);
    border-left: 2px solid var(--accent-blue);
    padding: 8px 14px;
    margin: 8px 0;
    color: var(--text-secondary);
    font-size: 14px;
    border-radius: 0 6px 6px 0;
  }}
  .event-analysis {{
    color: var(--text-secondary);
    font-size: 14px;
    line-height: 1.8;
    margin-top: 8px;
  }}
  .event-analysis a {{
    color: var(--accent-blue);
    text-decoration: none;
    border-bottom: 1px dotted var(--accent-blue);
  }}
  .event-analysis a:hover {{ border-bottom-style: solid; }}
  .quick-list {{ list-style: none; }}
  .quick-list li {{
    padding: 10px 16px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    margin-bottom: 8px;
    font-size: 14px;
    color: var(--text-secondary);
    line-height: 1.6;
  }}
  .quick-list li:hover {{ background: var(--bg-hover); }}
  .expert-subtitle {{
    font-size: 14px;
    font-weight: 600;
    color: var(--accent-blue);
    margin: 16px 0 10px;
  }}
  .expert-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
    margin-bottom: 8px;
  }}
  .expert-card .author {{ color: var(--accent-purple); font-weight: 600; font-size: 13px; }}
  .expert-card .author a {{ color: var(--accent-purple); text-decoration: none; }}
  .expert-card .author a:hover {{ text-decoration: underline; }}
  .expert-card .quote {{
    color: var(--text-secondary);
    font-size: 14px;
    margin-top: 4px;
    font-style: italic;
    line-height: 1.7;
  }}
  .topic-block {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
    margin-bottom: 10px;
  }}
  .topic-name {{ font-weight: 600; font-size: 14px; color: var(--text-primary); margin-bottom: 6px; }}
  .topic-detail {{ font-size: 13.5px; color: var(--text-secondary); line-height: 1.7; }}
  .sentiment-bar {{
    background: linear-gradient(135deg, rgba(63,185,80,0.1), rgba(210,153,34,0.1));
    border: 1px solid rgba(63,185,80,0.2);
    border-radius: 8px;
    padding: 14px 16px;
    font-size: 14px;
    color: var(--text-secondary);
  }}
  .trending-section {{
    margin-top: 40px;
    padding-top: 24px;
    border-top: 2px solid var(--border);
  }}
  .trending-note {{
    font-size: 13px;
    color: var(--text-muted);
    margin-bottom: 16px;
    padding: 10px 14px;
    background: rgba(240,136,62,0.06);
    border-radius: 8px;
    border: 1px solid rgba(240,136,62,0.12);
  }}
  .toc {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 28px;
    padding: 14px;
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 10px;
  }}
  .toc a {{
    font-size: 12.5px;
    color: var(--text-secondary);
    text-decoration: none;
    padding: 4px 12px;
    border-radius: 16px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    transition: all 0.2s;
    white-space: nowrap;
  }}
  .toc a:hover {{ color: var(--accent-blue); border-color: var(--accent-blue); background: rgba(88,166,255,0.08); }}
  .footer {{
    text-align: center;
    padding: 32px 16px;
    color: var(--text-muted);
    font-size: 12px;
    border-top: 1px solid var(--border);
    margin-top: 40px;
  }}
  .footer .brand {{ font-size: 14px; margin-bottom: 4px; color: var(--text-secondary); }}
  @media (max-width: 600px) {{
    .header h1 {{ font-size: 1.4rem; }}
    .container {{ padding: 16px 12px 40px; }}
    .event-card {{ padding: 14px 16px; }}
  }}
  ::-webkit-scrollbar {{ width: 6px; }}
  ::-webkit-scrollbar-track {{ background: var(--bg-primary); }}
  ::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}
</style>
</head>
<body>
<div class="header">
  <div class="header-badge">AI News Radar</div>
  <h1>{title}</h1>
  <div class="date">{date_display}</div>
</div>
<div class="container">
  {content}
</div>
<div class="footer">
  <div class="brand">AI News Radar</div>
  <div>自动采集 · 智能分析 · 每日推送</div>
</div>
</body>
</html>"""

# Emoji → tag class mapping
_EMOJI_TAG_MAP = {
    "\U0001f534": ("tag-critical", "\U0001f534 重磅"),   # 🔴
    "\U0001f680": ("tag-product", "\U0001f680 产品"),     # 🚀
    "\U0001f52c": ("tag-research", "\U0001f52c 研究"),    # 🔬
    "\U0001f4b0": ("tag-funding", "\U0001f4b0 融资"),     # 💰
    "\U0001f527": ("tag-chip", "\U0001f527 芯片"),        # 🔧
    "\U0001f91d": ("tag-product", "\U0001f91d 合作"),     # 🤝
    "\U0001f310": ("tag-product", "\U0001f310 开源"),     # 🌐
    "\U0001f4dc": ("tag-research", "\U0001f4dc 政策"),    # 📜
    "\U0001f4ca": ("tag-funding", "\U0001f4ca 市场"),     # 📊
}

# Section emojis that start event cards
_EVENT_EMOJIS = set(_EMOJI_TAG_MAP.keys())


def _html_escape(text: str) -> str:
    """Escape HTML special characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline_markup(text: str) -> str:
    """Convert inline markdown to HTML: **bold**, [text](url), @username."""
    # [text](url)
    text = re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)',
        r'<a href="\2">\1</a>',
        text,
    )
    # **bold**
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # @username (but not inside an existing <a> tag)
    text = re.sub(
        r'(?<!["\w/@])@(\w+)',
        r'<a href="https://x.com/\1" class="author">@\1</a>',
        text,
    )
    return text


class HtmlPublisher:
    """Publish Markdown reports as styled HTML pages for GitHub Pages."""

    def __init__(self, docs_dir: str | None = None):
        base = Path(docs_dir) if docs_dir else PROJECT_ROOT / "docs"
        self.docs_dir = base
        self.reports_dir = base / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def publish(
        self,
        markdown_report: str,
        title: str,
        date_str: str,
        pipeline_name: str,
    ) -> str:
        """Convert markdown report to HTML, save to docs/reports/, return filepath."""
        html_content = self._markdown_to_html(markdown_report)

        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
            date_display = f"{dt.year}年{dt.month}月{dt.day}日 · {weekdays[dt.weekday()]}"
        except Exception:
            date_display = date_str

        html = HTML_TEMPLATE.format(
            title=title,
            date=date_str,
            date_display=date_display,
            content=html_content,
        )

        filename = f"{date_str}_{pipeline_name}.html"
        filepath = self.reports_dir / filename
        filepath.write_text(html, encoding="utf-8")
        logger.info("Published HTML report: %s", filepath)

        self._update_index()
        return str(filepath)

    # ------------------------------------------------------------------
    # Markdown → styled HTML converter
    # ------------------------------------------------------------------

    def _markdown_to_html(self, md_text: str) -> str:
        """Parse markdown report line-by-line into styled HTML sections."""
        lines = md_text.split("\n")
        out: list[str] = []
        toc_items: list[tuple[str, str]] = []  # (id, title)

        # State tracking
        in_event_card = False
        in_quick_list = False
        in_expert_section = False
        in_trending = False
        in_core_judgment = False
        after_quote = False  # next paragraph is event-analysis

        def _close_event_card():
            nonlocal in_event_card
            if in_event_card:
                out.append("</div>")
                in_event_card = False

        def _close_quick_list():
            nonlocal in_quick_list
            if in_quick_list:
                out.append("</ul>")
                in_quick_list = False

        def _close_core_judgment():
            nonlocal in_core_judgment
            if in_core_judgment:
                out.append("</div>")
                in_core_judgment = False

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # --- blank line ---
            if not stripped:
                after_quote = False
                i += 1
                continue

            # --- horizontal rule → trending section ---
            if stripped == "---":
                _close_event_card()
                _close_quick_list()
                _close_core_judgment()
                if not in_trending:
                    in_trending = True
                    out.append('<div class="trending-section">')
                i += 1
                continue

            # --- ## heading ---
            if stripped.startswith("## "):
                _close_event_card()
                _close_quick_list()
                _close_core_judgment()
                heading_text = stripped[3:].strip()
                anchor = f"section-{len(toc_items)}"
                toc_items.append((anchor, heading_text))

                # Detect section type
                if "速览" in heading_text:
                    in_expert_section = False
                elif "专家" in heading_text or "视角" in heading_text:
                    in_expert_section = True
                else:
                    in_expert_section = False

                safe = _html_escape(heading_text)
                out.append(f'<div class="section" id="{anchor}">')
                out.append(f'<div class="section-title">{safe}</div>')
                i += 1
                continue

            # --- # heading (top-level, skip since header is in template) ---
            if stripped.startswith("# ") and not stripped.startswith("## "):
                _close_event_card()
                _close_quick_list()
                _close_core_judgment()
                i += 1
                continue

            # --- core judgment detection ---
            if "核心判断" in stripped and not in_core_judgment:
                _close_event_card()
                _close_quick_list()
                # The heading itself may contain "核心判断"
                if not stripped.startswith("#"):
                    safe = _inline_markup(_html_escape(stripped))
                    out.append(f'<div class="section-title">{safe}</div>')
                in_core_judgment = True
                out.append('<div class="core-judgment">')
                i += 1
                continue

            # --- inside core judgment: collect until next ## or event emoji ---
            if in_core_judgment:
                first_char = stripped[0] if stripped else ""
                if first_char == "#" or first_char in _EVENT_EMOJIS:
                    _close_core_judgment()
                    continue  # re-process this line
                safe = _inline_markup(_html_escape(stripped))
                out.append(f"<p>{safe}</p>")
                i += 1
                continue

            # --- > quote → event-highlight ---
            if stripped.startswith("> "):
                content = stripped[2:].strip()
                safe = _inline_markup(_html_escape(content))
                out.append(f'<div class="event-highlight">{safe}</div>')
                after_quote = True
                i += 1
                continue

            # --- list items (- or •) ---
            if stripped.startswith("- ") or stripped.startswith("• "):
                item_text = stripped[2:].strip()
                safe = _inline_markup(_html_escape(item_text))

                if in_quick_list or "速览" in "".join(out[-10:]):
                    if not in_quick_list:
                        in_quick_list = True
                        out.append('<ul class="quick-list">')
                    out.append(f"<li>{safe}</li>")
                elif in_trending:
                    if not in_quick_list:
                        in_quick_list = True
                        out.append('<ul class="quick-list">')
                    out.append(f"<li>{safe}</li>")
                else:
                    # Generic list item inside event card or expert section
                    if in_expert_section:
                        out.append(f'<div class="expert-card"><div class="quote">{safe}</div></div>')
                    else:
                        out.append(f'<div class="event-analysis">{safe}</div>')
                i += 1
                continue

            # --- 📊 社区情绪 ---
            if stripped.startswith("\U0001f4ca") and "情绪" in stripped:
                _close_event_card()
                _close_quick_list()
                content = stripped
                safe = _inline_markup(_html_escape(content))
                out.append(f'<div class="sentiment-bar">{safe}</div>')
                i += 1
                continue

            # --- expert sub-headings (🔥今日热议, 💬独到洞察, 🛠技术反馈, etc.) ---
            if in_expert_section and any(
                stripped.startswith(e) for e in ("\U0001f525", "\U0001f4ac", "\U0001f6e0", "\U0001f4ca")
            ):
                _close_event_card()
                _close_quick_list()
                safe = _inline_markup(_html_escape(stripped))
                out.append(f'<div class="expert-subtitle">{safe}</div>')
                i += 1
                continue

            # --- event card (emoji-prefixed lines) ---
            first_char = stripped[0] if stripped else ""
            if first_char in _EVENT_EMOJIS:
                _close_event_card()
                _close_quick_list()

                is_critical = first_char == "\U0001f534"
                card_class = "critical" if is_critical else "normal"
                tag_class, tag_label = _EMOJI_TAG_MAP.get(
                    first_char, ("tag-product", f"{first_char}")
                )

                safe_title = _inline_markup(_html_escape(stripped))
                in_event_card = True
                out.append(f'<div class="event-card {card_class}">')
                out.append(f'<span class="tag {tag_class}">{_html_escape(tag_label)}</span>')
                out.append(f'<div class="event-title">{safe_title}</div>')
                i += 1
                continue

            # --- paragraph after quote → event-analysis ---
            if after_quote and in_event_card:
                safe = _inline_markup(_html_escape(stripped))
                out.append(f'<div class="event-analysis">{safe}</div>')
                after_quote = False
                i += 1
                continue

            # --- default: plain paragraph ---
            safe = _inline_markup(_html_escape(stripped))
            if in_event_card:
                out.append(f'<div class="event-analysis">{safe}</div>')
            elif in_expert_section:
                out.append(f'<div class="expert-card"><div class="quote">{safe}</div></div>')
            else:
                out.append(f"<p>{safe}</p>")
            i += 1

        # Close any open containers
        _close_event_card()
        _close_quick_list()
        _close_core_judgment()
        if in_trending:
            out.append("</div>")

        # Build TOC
        toc_html = ""
        if toc_items:
            links = "".join(
                f'<a href="#{anchor}">{_html_escape(title)}</a>'
                for anchor, title in toc_items
            )
            toc_html = f'<nav class="toc">{links}</nav>\n'

        return toc_html + "\n".join(out)

    # ------------------------------------------------------------------
    # Index page
    # ------------------------------------------------------------------

    def _update_index(self) -> None:
        """Generate docs/index.html with a listing of all report files."""
        reports = sorted(self.reports_dir.glob("*.html"), reverse=True)
        items = "\n".join(
            f'<li><a href="reports/{r.name}">{r.stem}</a></li>'
            for r in reports
        )

        index_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI News Radar - 报告归档</title>
<style>
body {{ font-family: -apple-system, "PingFang SC", sans-serif; background: #0d1117; color: #e6edf3; padding: 40px 20px; }}
h1 {{ text-align: center; margin-bottom: 30px; }}
ul {{ list-style: none; max-width: 600px; margin: 0 auto; }}
li {{ padding: 12px 16px; background: #161b22; border: 1px solid #30363d; border-radius: 8px; margin-bottom: 8px; }}
li:hover {{ background: #1c2128; }}
a {{ color: #58a6ff; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<h1>AI News Radar 报告归档</h1>
<ul>
{items}
</ul>
</body>
</html>"""
        index_path = self.docs_dir / "index.html"
        index_path.write_text(index_html, encoding="utf-8")
        logger.info("Updated index: %s", index_path)
