"""Report generation: Event Cards / Trending → Markdown via LLM.

Output is conservative Markdown (no tables / no code blocks / no horizontal rules)
so it renders well in any consumer (GitHub Pages, Obsidian, plain editors).
"""

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
    """Generate final Markdown reports using LLM fallback chain."""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    # Format instructions appended to system_prompt to reduce user_prompt tokens
    FORMAT_INSTRUCTIONS = """

## 输出格式

板块顺序：今日核心判断 → 🔥 重大发布与产品 → 🔬 技术与研究 → 💰 融资与市场 → 🔧 芯片与算力 → ⚡ 速览 → 💡 专家视角

**核心判断**：一段话，趋势洞察，不复述新闻

**新闻板块**每条三层：
emoji [**标题**](来源推文URL)
> ≤30字要点
2-3句分析

emoji规则：🔴仅限2-3个最重磅事件，其余用🚀产品/🔬研究/💰融资/🔧芯片/🤝合作/🌐开源/📜政策/📊市场
**输出报告中不要显示日期时间**（事件时间仅供分析参考，不出现在标题或正文中）

**⚡ 速览**：次要消息，每条• emoji一句话

**💡 专家视角**（四个子板块，用加粗+emoji做子标题，不要用###标题）：

**🔥 今日热议焦点**

每个热议主题用以下格式，主题之间空一行：

**主题名称**
共识：一句话 | 分歧：一句话
— [@专家名](链接)："观点"
— [@专家名](链接)："观点"

**另一个主题名称**
共识：一句话
— [@专家名](链接)："观点"

---

**💬 独到洞察**

每条用 — 开头，条目之间空一行：

— [@专家名](链接)："原话或精炼转述"

— [@专家名](链接)："原话或精炼转述"

— [@专家名](链接)："原话或精炼转述"

（最多5条，不求多求精）

---

**🛠 技术使用反馈**

每个产品用加粗产品名，✅正面 ⚠️吐槽，产品之间空一行：

**GPT-4o**
✅ 正面反馈内容
⚠️ 吐槽内容

**Claude Sonnet 4.6**
✅ 正面反馈内容
⚠️ 吐槽内容

---

**📊 今日社区情绪**

一段话总结即可，不用列表。

格式规则强调：
1. 子板块标题用 **加粗+emoji** 而不是 ### 标题（钉钉不支持###）
2. 子板块之间用 --- 分隔线隔开
3. 不要用 > 引用来做标题或主题名（钉钉会渲染成灰色块）
4. 热议主题名用 **加粗** 而不是 > 引用
5. 产品名用 **加粗** 而不是 > 引用
6. 专家引用统一用 — 开头（破折号），不用 - 或 •
7. 所有 @专家名 必须带超链接 [@名](https://x.com/名)

**分流规则**：opinion→专家视角，news→按category分入新闻板块
**格式限制**：钉钉Markdown，仅用#/**/>/-/[](url)，禁用表格/代码块/删除线/分割线

**链接规则（必须严格遵守）**：
- 每条新闻的标题必须是超链接：emoji [**标题**](URL)
- **优先使用媒体原文链接**：如果来源中有科技媒体（TechCrunch、The Verge、36氪、IT之家等非 x.com 的URL），标题链接必须优先使用该媒体原文URL
- 仅当来源全部是 Twitter 时，才使用 x.com 链接
- 每条新闻末尾标注所有来源，媒体用 [媒体名](URL)，Twitter 用 [@用户名](URL)
- 示例（有媒体来源时）：🚀 [**OpenAI发布GPT-5**](https://techcrunch.com/2026/02/26/openai-gpt5)
  来源：[TechCrunch](https://techcrunch.com/...) · [@OpenAI](https://x.com/OpenAI/status/123)
- 示例（仅Twitter）：🚀 [**Karpathy谈编程奇点**](https://x.com/karpathy/status/456)
- 速览中也优先媒体链接：• emoji [一句话摘要](媒体URL)
- 专家视角中的@名必须带超链接：[@专家名](https://x.com/专家名)："观点\""""

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
            def _fmt_source(s):
                # RSS sources: use media name; Twitter: use @handle
                if s.url and not s.url.startswith("https://x.com/"):
                    return f"{s.author} ({s.url})"
                return f"@{s.author.lstrip('@')} ({s.url})" if s.url else f"@{s.author.lstrip('@')}"

            # Sort RSS (non-x.com) sources first so LLM sees media URLs prominently
            sorted_sources = sorted(
                e.sources,
                key=lambda s: (s.url.startswith("https://x.com/") if s.url else True),
            )
            sources_str = ", ".join(
                _fmt_source(s) for s in sorted_sources
            ) if e.sources else "无"
            key_facts_str = "; ".join(e.key_facts) if e.key_facts else "无"

            return (
                f"{e.title} | 类别: {e.category.value} | "
                f"重要性: {e.importance} | 类型: {e.event_type}\n"
                f"关键事实: {key_facts_str}\n"
                f"分析师视角: {e.analyst_angle}\n"
                f"来源推文: {sources_str}"
            )

        # Limit to top 25 events by importance to avoid prompt token overflow
        MAX_EVENTS = 25
        if len(events) > MAX_EVENTS:
            logger.info("Trimming events from %d to %d (by importance)", len(events), MAX_EVENTS)
            events = sorted(events, key=lambda e: e.importance, reverse=True)[:MAX_EVENTS]

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
