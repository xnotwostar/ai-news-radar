"""Report screenshot generator — captures the full HTML report as a PNG image."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class PosterGenerator:
    """Screenshot full HTML report pages as PNG images for DingTalk push."""

    def __init__(self, docs_dir: str | None = None):
        base = Path(docs_dir) if docs_dir else PROJECT_ROOT / "docs"
        self.reports_dir = base / "reports"
        self.posters_dir = base / "posters"
        self.posters_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        date_str: str,
        pipeline_name: str,
    ) -> str:
        """Screenshot the HTML report as a full-page PNG, return file path."""
        html_path = self.reports_dir / f"{date_str}_{pipeline_name}.html"
        if not html_path.exists():
            raise FileNotFoundError(f"HTML report not found: {html_path}")

        poster_path = self.posters_dir / f"{date_str}_{pipeline_name}_poster.png"
        self._screenshot(str(html_path), str(poster_path))
        logger.info("Generated report screenshot: %s", poster_path)
        return str(poster_path)

    @staticmethod
    def _screenshot(html_path: str, output_path: str) -> None:
        """Open local HTML file in Playwright and take a full-page screenshot."""
        from playwright.sync_api import sync_playwright

        file_url = f"file://{html_path}"

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 750, "height": 800})
            page.goto(file_url, wait_until="networkidle")
            page.screenshot(path=output_path, full_page=True, type="png")
            browser.close()

        size_kb = Path(output_path).stat().st_size / 1024
        logger.info("Screenshot saved: %s (%.1f KB)", output_path, size_kb)
