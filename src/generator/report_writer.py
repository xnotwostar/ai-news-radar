"""Report generation: Event Cards / Trending → DingTalk Markdown via LLM."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import jieba

from ..schemas import EventCard, TrendingItem
from .llm_client import LLMClient

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class ReportWriter:
    """Generate final DingTalk Markdown reports using LLM fallback chain."""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    # Format instructions appended to system_prompt to reduce user_prompt tokens
    FORMAT_INSTRUCTIONS = """

## 输出格式

板块顺序：今日核心判断 → 🔥 重大发布与产品 → 🔬 技术与研究 → 💰 融资与市场 → 🔧 芯片与算力 → ⚡ 速览 → 💡 专家视角

**核心判断**：一段话，趋势洞察，不复述新闻

**新闻板块**每条三层：
emoji **标题** (MM-DD HH:MM UTC)
> ≤30字要点
2-3句分析

emoji规则：🔴仅限2-3个最重磅事件，其余用🚀产品/🔬研究/💰融资/🔧芯片/🤝合作/🌐开源/📜政策/📊市场
无精确时间标(MM-DD)

**⚡ 速览**：次要消息，每条• emoji一句话

**💡 专家视角**四块：
🔥今日热议：2-3主题，主题→分歧/共识→代表观点(@名 时间)
💬独到洞察：3-5条，@名(时间)："转述"
🛠技术反馈：模型/工具体验，同产品聚合，正面+吐槽
📊社区情绪：1句话

**分流规则**：opinion→专家视角，news→按category分入新闻板块
**格式限制**：钉钉Markdown，仅用#/**/>/-/[](url)，禁用表格/代码块/删除线/分割线"""

    def generate_twitter_report(
        self,
        events: list[EventCard],
        prompt_file: str,
        date_str: str,
    ) -> str:
        """Generate report from Event Cards (global / china pipeline)."""
        base_system, _ = self._load_prompt(prompt_file)
        system_prompt = base_system + self.FORMAT_INSTRUCTIONS

        def _format_event(e: EventCard) -> str:
            time_str = (
                e.event_time.strftime("%m-%d %H:%M UTC")
                if e.event_time
                else "未知"
            )
            sources_str = ", ".join(
                f"@{s.author.lstrip('@')}" for s in e.sources
            ) if e.sources else "无"
            key_facts_str = "; ".join(e.key_facts) if e.key_facts else "无"

            return (
                f"{e.title} | 类别: {e.category.value} | "
                f"重要性: {e.importance} | 类型: {e.event_type} | "
                f"时间: {time_str}\n"
                f"关键事实: {key_facts_str}\n"
                f"分析师视角: {e.analyst_angle}\n"
                f"来源推文: {sources_str}"
            )

        events_text = "\n\n".join(
            _format_event(e) for e in events
        )

        user_prompt = f"""日期：{date_str}
以下是今日 {len(events)} 个事件：

{events_text}"""

        return self.llm.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.5,
            max_tokens=8192,
        )

    def generate_merged_china_report(
        self,
        twitter_events: list[EventCard],
        trending_items: list[TrendingItem],
        prompt_file: str,
        date_str: str,
    ) -> str:
        """Generate merged china_ai report: Twitter report as-is + deduplicated trending appended."""

        # 1. Use the original method to generate the full Twitter report (untouched)
        twitter_report = self.generate_twitter_report(twitter_events, prompt_file, date_str)

        # 2. Deduplicate trending against Twitter events
        unique_trending = self._deduplicate_trending(twitter_events, trending_items)

        if not unique_trending:
            logger.info("All trending items duplicated with Twitter events, skipping trending section")
            return twitter_report

        # 3. Format deduplicated trending and append to Twitter report
        lines: list[str] = [
            "",
            "## 🇨🇳 国内热搜速递",
            "",
            "> 以下为国内科技媒体及社交平台热议话题，与上方 Twitter 信源互补。",
            "",
        ]

        for te in unique_trending:
            src = f"（{te.platform} Top {te.rank}）" if te.platform and te.rank else ""
            lines.append(f"- 🔥 **{te.title}**{src}")

        lines.append("")
        lines.append("---")

        trending_section = "\n".join(lines)

        logger.info(
            "Merged report: Twitter report + %d/%d unique trending items",
            len(unique_trending), len(trending_items),
        )
        return twitter_report.rstrip() + "\n" + trending_section + "\n"

    # 不参与去重比较的高频词/停用词
    _DEDUP_STOPWORDS = {
        "的", "了", "在", "是", "和", "与", "对", "于", "将", "为", "被",
        "AI", "人工智能", "大模型", "LLM", "发布", "宣布", "推出",
        "表示", "称", "说", "指出", "认为",
    }

    @staticmethod
    def _extract_keywords(text: str) -> set[str]:
        """提取文本中长度≥2的实质词（去掉停用词）。"""
        words = jieba.cut(text)
        return {
            w for w in words
            if len(w) >= 2 and w not in ReportWriter._DEDUP_STOPWORDS
        }

    def _deduplicate_trending(
        self,
        twitter_events: list[EventCard],
        trending_items: list[TrendingItem],
    ) -> list[TrendingItem]:
        """热搜去重：若热搜条目与任意 Twitter 事件共享 ≥2 个实质词，视为重复。"""
        if not trending_items:
            return []

        # 预计算 Twitter 事件关键词集合
        event_kw_list: list[set[str]] = []
        for e in twitter_events:
            event_kw_list.append(self._extract_keywords(e.title))

        unique: list[TrendingItem] = []
        for te in trending_items:
            te_kw = self._extract_keywords(te.title)
            is_dup = False
            for ev_kw in event_kw_list:
                if len(te_kw & ev_kw) >= 2:
                    is_dup = True
                    break
            if not is_dup:
                unique.append(te)

        logger.info(
            "热搜去重：原始 %d 条，去重后保留 %d 条（移除 %d 条重复）",
            len(trending_items), len(unique),
            len(trending_items) - len(unique),
        )
        return unique

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
