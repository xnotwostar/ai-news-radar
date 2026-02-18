"""Report generation: Event Cards / Trending → DingTalk Markdown via LLM."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..schemas import EventCard, TrendingItem
from .llm_client import LLMClient

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class ReportWriter:
    """Generate final DingTalk Markdown reports using LLM fallback chain."""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def generate_twitter_report(
        self,
        events: list[EventCard],
        prompt_file: str,
        date_str: str,
    ) -> str:
        """Generate report from Event Cards (global / china pipeline)."""
        system_prompt, one_shot = self._load_prompt(prompt_file)

        events_json = json.dumps(
            [e.model_dump(exclude={"cluster_size"}) for e in events],
            ensure_ascii=False,
            indent=2,
        )

        user_prompt = f"""以下是一份高质量 AI 日报范例，请严格学习其风格和结构：

{one_shot}

---

现在，请基于以下今日数据（{len(events)} 个 Event Cards），生成同样风格的日报。
日期：{date_str}

{events_json}"""

        return self.llm.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.5,
            max_tokens=8192,
        )

    def generate_trending_report(
        self,
        items: list[TrendingItem],
        prompt_file: str,
        date_str: str,
    ) -> str:
        """Generate report from trending items (trending pipeline)."""
        system_prompt, one_shot = self._load_prompt(prompt_file)

        items_text = "\n".join(
            f"- [{item.platform}] {item.title} (热度排名: {item.rank})"
            for item in items
        )

        user_prompt = f"""以下是一份高质量热搜速递范例，请严格学习其风格和结构：

{one_shot}

---

现在，请基于以下今日热搜数据（{len(items)} 条），生成同样风格的热搜速递。
日期：{date_str}

{items_text}"""

        return self.llm.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.5,
            max_tokens=4096,
        )

    @staticmethod
    def _load_prompt(prompt_file: str) -> tuple[str, str]:
        """Load prompt file, split into system prompt and one-shot example.

        Expected format:
        ---SYSTEM---
        <system prompt>
        ---ONESHOT---
        <one-shot example>
        """
        path = PROJECT_ROOT / prompt_file
        content = path.read_text(encoding="utf-8")

        if "---SYSTEM---" in content and "---ONESHOT---" in content:
            parts = content.split("---ONESHOT---", 1)
            system_part = parts[0].replace("---SYSTEM---", "").strip()
            one_shot = parts[1].strip()
            return system_part, one_shot

        # Fallback: entire file is one-shot, use default system prompt
        default_system = (
            "你是「阿里云出海·全球 AI 行业情报分析师」，"
            "每日为技术决策者和投资团队生成 AI 行业日报。"
        )
        return default_system, content
