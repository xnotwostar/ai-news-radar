"""Obsidian vault publisher — writes weekly reports + maintains an index.

Behavior:
1. Writes ``<vault>/AI Intel/Weekly/YYYY-WXX.md`` with frontmatter for dataview
2. Updates ``<vault>/AI Intel/Weekly/_index.md`` with a dataview snippet
3. Commits and pushes via git (uses the vault's existing git repo)

The vault path defaults to ``$OBSIDIAN_VAULT_PATH`` env var, falling back
to ``~/Documents/Obsidian Vault``. The vault must already be a git repo.
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_VAULT = Path.home() / "Documents" / "Obsidian Vault"
SUBDIR = "AI Intel"


class ObsidianPublisher:
    """Write weekly markdown to an Obsidian vault subdir + git push."""

    def __init__(
        self,
        vault_path: Path | str | None = None,
        subdir: str = SUBDIR,
        git_push: bool = True,
        git_remote: str = "origin",
        git_branch: str | None = None,  # default: current branch
    ):
        if vault_path:
            self.vault_path = Path(vault_path)
        else:
            self.vault_path = Path(os.environ.get("OBSIDIAN_VAULT_PATH") or DEFAULT_VAULT)
        self.subdir = subdir
        self.git_push = git_push
        self.git_remote = git_remote
        self.git_branch = git_branch

        self.intel_dir = self.vault_path / subdir
        self.weekly_dir = self.intel_dir / "Weekly"
        self.daily_dir = self.intel_dir / "Daily"
        self.index_path = self.intel_dir / "_index.md"

    # ------------------------------------------------------------------ public

    def publish_weekly(self, markdown: str, meta: dict) -> Path:
        """Write a weekly report to the vault, update index, and git push.

        meta: dict from WeeklyWriter — needs week, date_start, date_end,
              source_daily_count, generated_at.
        Returns the absolute path of the written file.
        """
        self._ensure_dirs()

        year = meta.get("year") or int(meta["date_end"][:4])
        week = int(meta["week"])
        filename = f"{year}-W{week:02d}.md"
        target = self.weekly_dir / filename

        # Build frontmatter + content
        frontmatter = self._build_frontmatter(meta)
        full = frontmatter + "\n\n" + markdown.strip() + "\n"
        target.write_text(full, encoding="utf-8")
        logger.info("Wrote weekly to %s (%d bytes)", target, len(full))

        # Update _index.md
        self._update_index()

        # Git commit + push
        if self.git_push:
            self._git_commit_push([target, self.index_path], message=f"AI Weekly · {year}-W{week:02d}")

        return target

    def publish_daily(self, markdown: str, name: str, date_str: str) -> Path:
        """Optionally archive daily reports into the vault (off by default; called manually)."""
        self._ensure_dirs()
        filename = f"{date_str}_{name}.md"
        target = self.daily_dir / filename

        # Light frontmatter for daily
        fm = "\n".join([
            "---",
            f"type: ai-daily",
            f"name: {name}",
            f"date: {date_str}",
            f"tags: [ai-daily, {date_str[:7]}]",
            "---",
            "",
        ])
        target.write_text(fm + markdown.strip() + "\n", encoding="utf-8")
        logger.info("Wrote daily archive to %s", target)
        return target

    # ------------------------------------------------------------------ helpers

    def _ensure_dirs(self) -> None:
        if not self.vault_path.exists():
            raise FileNotFoundError(f"Vault path does not exist: {self.vault_path}")
        if not (self.vault_path / ".git").exists():
            raise RuntimeError(
                f"Vault {self.vault_path} is not a git repo. Initialize git first."
            )
        self.intel_dir.mkdir(exist_ok=True)
        self.weekly_dir.mkdir(exist_ok=True)
        self.daily_dir.mkdir(exist_ok=True)

    @staticmethod
    def _build_frontmatter(meta: dict) -> str:
        year = meta.get("year") or int(meta["date_end"][:4])
        week = int(meta["week"])
        lines = [
            "---",
            "type: ai-weekly",
            f"week: {year}-W{week:02d}",
            f"date_start: {meta['date_start']}",
            f"date_end: {meta['date_end']}",
            f"tags: [ai-weekly, {year}, week-{week:02d}]",
            f"sources: [ai-news-radar]",
            f"source_daily_count: {meta.get('source_daily_count', 0)}",
            f"generated_at: {meta.get('generated_at', datetime.utcnow().isoformat() + 'Z')}",
            f"aliases: [\"{year} Week {week:02d} AI Weekly\"]",
            "---",
        ]
        return "\n".join(lines)

    def _update_index(self) -> None:
        """Write/refresh `_index.md` with a Dataview block listing all weeklies."""
        content = """# AI Intel · Index

A self-curated AI industry intelligence archive. Daily dispatch + weekly synthesis.
Generated by `ai-news-radar`. Edited by self.

## 📰 Weekly Reports

```dataview
TABLE WITHOUT ID
  link(file.link, week) as "Week",
  date_start as "From",
  date_end as "To",
  source_daily_count as "Days",
  file.mtime as "Generated"
FROM "AI Intel/Weekly"
WHERE type = "ai-weekly"
SORT week DESC
```

## 📅 Daily Archive

```dataview
TABLE WITHOUT ID
  file.link as "Report",
  date as "Date",
  name as "Pipeline"
FROM "AI Intel/Daily"
WHERE type = "ai-daily"
SORT date DESC
LIMIT 30
```

## ⚙️ Setup

- Source: [ai-news-radar](https://github.com/xnotwostar/ai-news-radar)
- Cadence: daily 08:00 CST · weekly Sun 23:00 CST
- Editor: self
- Git: this vault
"""
        self.index_path.write_text(content, encoding="utf-8")

    def _git_commit_push(self, files: list[Path], message: str) -> None:
        """Stage given files, commit, push to origin. Quiet on already-clean trees."""
        rels = []
        for p in files:
            try:
                rels.append(str(p.relative_to(self.vault_path)))
            except ValueError:
                rels.append(str(p))

        try:
            # 1. Add specific files (don't `git add .` — vault has user edits)
            subprocess.run(
                ["git", "add"] + rels,
                cwd=self.vault_path, check=True, capture_output=True,
            )
            # 2. Check if anything is staged
            r = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=self.vault_path, capture_output=True,
            )
            if r.returncode == 0:
                logger.info("Nothing to commit in vault, skipping")
                return

            # 3. Commit
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self.vault_path, check=True, capture_output=True,
            )
            logger.info("Committed: %s", message)

            # 4. Push
            push_cmd = ["git", "push", self.git_remote]
            if self.git_branch:
                push_cmd.append(self.git_branch)
            r = subprocess.run(
                push_cmd,
                cwd=self.vault_path, capture_output=True,
            )
            if r.returncode != 0:
                logger.warning(
                    "git push failed (rc=%d): %s",
                    r.returncode, r.stderr.decode(errors="ignore"),
                )
            else:
                logger.info("Pushed to %s", self.git_remote)
        except subprocess.CalledProcessError as e:
            logger.error(
                "Git op failed: %s\nstderr: %s",
                e, e.stderr.decode(errors="ignore") if e.stderr else "",
            )
