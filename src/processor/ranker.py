"""Event Card importance ranking via Qwen-Plus."""

from __future__ import annotations

import json
import logging
import os

import httpx

from ..schemas import EventCard

logger = logging.getLogger(__name__)

DASHSCOPE_CHAT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

RANKER_SYSTEM_PROMPT = """你是 AI 行业情报精排助手。给定一批 Event Card 摘要，重新排序并筛选出最重要的 25-35 条。

评判标准：
1. 行业影响力（是否改变竞争格局）
2. 时效性（是否刚发生）
3. 信息密度（是否有实质性内容而非PR话术）
4. 受众价值（技术决策者和投资团队是否关心）

输出严格 JSON 格式：
{"ranked_ids": ["evt_xxx", "evt_yyy", ...]}

只返回排序后的 event_id 列表，最多 35 条。"""


class Ranker:
    """Re-rank Event Cards by importance using LLM."""

    def __init__(self, api_key: str | None = None, model: str = "qwen-plus"):
        self.api_key = api_key or os.environ["DASHSCOPE_API_KEY"]
        self.model = model
        self.top_n = 35

    def rank(self, events: list[EventCard]) -> list[EventCard]:
        """Return top events sorted by importance."""
        if len(events) <= self.top_n:
            return sorted(events, key=lambda e: e.importance, reverse=True)

        try:
            return self._llm_rank(events)
        except Exception as e:
            logger.warning("LLM ranking failed, falling back to score sort: %s", e)
            return self._score_rank(events)

    def _llm_rank(self, events: list[EventCard]) -> list[EventCard]:
        summaries = "\n".join(
            f"- {e.event_id}: [{e.importance}] {e.title}"
            for e in events
        )

        resp = httpx.post(
            DASHSCOPE_CHAT_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": RANKER_SYSTEM_PROMPT},
                    {"role": "user", "content": f"请精排以下 {len(events)} 条事件：\n\n{summaries}"},
                ],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
            timeout=45,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        ranked_ids = parsed.get("ranked_ids", [])

        id_to_event = {e.event_id: e for e in events}
        ranked = [id_to_event[eid] for eid in ranked_ids if eid in id_to_event]

        # Append any missing events at the end (sorted by importance)
        seen = set(ranked_ids)
        remaining = sorted(
            [e for e in events if e.event_id not in seen],
            key=lambda e: e.importance,
            reverse=True,
        )
        ranked.extend(remaining)

        return ranked[: self.top_n]

    def _score_rank(self, events: list[EventCard]) -> list[EventCard]:
        """Simple fallback: sort by importance * cluster_size weight."""
        def score(e: EventCard) -> float:
            size_boost = min(e.cluster_size / 3, 2.0)
            return e.importance * (1 + size_boost * 0.2)

        return sorted(events, key=score, reverse=True)[: self.top_n]
