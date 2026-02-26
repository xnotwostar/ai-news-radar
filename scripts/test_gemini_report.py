"""Standalone script: generate china_ai report with Gemini via OpenAI SDK."""

import json
import os
import sys
from pathlib import Path

# Project root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from openai import OpenAI
from src.schemas import EventCard
from src.publisher.html_publisher import HtmlPublisher

# ── 1. Load latest china_ai events ──────────────────────────────────────

events_dir = ROOT / "data" / "events"
event_files = sorted(events_dir.glob("*global_ai_events.json"), reverse=True)
if not event_files:
    print("No global_ai events found in data/events/")
    sys.exit(1)

events_file = event_files[0]
print(f"Loading events: {events_file.name}")
raw = json.loads(events_file.read_text("utf-8"))
events = [EventCard(**e) for e in raw]
print(f"  {len(events)} events loaded")

# Extract date from filename: 2026-02-20_china_ai_events.json → 2026-02-20
date_str = events_file.stem.replace("_global_ai_events", "")

# ── 2. Build system_prompt + user_prompt (copied from report_writer.py) ─

# System prompt from prompts/report_china.txt
prompt_path = ROOT / "prompts" / "report_global.txt"
content = prompt_path.read_text("utf-8")
parts = content.split("---ONESHOT---", 1)
base_system = parts[0].replace("---SYSTEM---", "").strip()

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
- 每条新闻的标题本身就是超链接，格式：emoji [**标题**](来源推文URL)
- 来源推文数据中每位作者都附带了完整URL（如 https://x.com/author/status/123456），直接使用
- 如果来源有多个作者，标题链接使用engagement最高的那条URL
- 专家视角中的@名也必须是超链接：[@专家名](https://x.com/专家名)："观点"
- 速览中的每条也要带链接：• emoji [一句话摘要](URL)
- 示例：🚀 [**OpenAI发布GPT-5**](https://x.com/OpenAI/status/123456)"""

system_prompt = base_system + FORMAT_INSTRUCTIONS


def format_event(e: EventCard) -> str:
    sources_str = ", ".join(
        f"@{s.author.lstrip('@')} ({s.url})" if s.url
        else f"@{s.author.lstrip('@')}"
        for s in e.sources
    ) if e.sources else "无"
    key_facts_str = "; ".join(e.key_facts) if e.key_facts else "无"
    return (
        f"{e.title} | 类别: {e.category.value} | "
        f"重要性: {e.importance} | 类型: {e.event_type}\n"
        f"关键事实: {key_facts_str}\n"
        f"分析师视角: {e.analyst_angle}\n"
        f"来源推文: {sources_str}"
    )


# Limit to top 25 by importance
MAX_EVENTS = 25
if len(events) > MAX_EVENTS:
    print(f"  Trimming from {len(events)} to {MAX_EVENTS} events (by importance)")
    events = sorted(events, key=lambda e: e.importance, reverse=True)[:MAX_EVENTS]

events_text = "\n\n".join(format_event(e) for e in events)

user_prompt = f"""日期：{date_str}
以下是今日 {len(events)} 个事件：

{events_text}"""

print(f"  system_prompt: {len(system_prompt)} chars")
print(f"  user_prompt:   {len(user_prompt)} chars")

# ── 3. Call Gemini via OpenAI SDK ────────────────────────────────────────

print("\nCalling Gemini 2.5 Flash ...")
client = OpenAI(
    api_key=os.environ["GOOGLE_API_KEY"],
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
)
response = client.chat.completions.create(
    model="gemini-2.5-pro",
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    timeout=300,
)
report = response.choices[0].message.content
print(f"  Report generated: {len(report)} chars")

# ── 4. Save markdown report ─────────────────────────────────────────────

reports_dir = ROOT / "data" / "reports"
reports_dir.mkdir(parents=True, exist_ok=True)
md_path = reports_dir / "gemini_test_global_ai.md"
md_path.write_text(report, encoding="utf-8")
print(f"  Saved: {md_path}")

# ── 5. Generate HTML via HtmlPublisher ───────────────────────────────────

publisher = HtmlPublisher()
html_path = publisher.publish(
    markdown_report=report,
    title="🌍 全球AI洞察 [Gemini 2.5 Pro]",
    date_str=date_str,
    pipeline_name="gemini_test_global_ai",
)
print(f"\n  HTML: {Path(html_path).resolve()}")
