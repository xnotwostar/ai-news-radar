"""Event Card importance ranking via Qwen-Plus, boosted by author tiers and info density."""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

import httpx
import yaml

from ..schemas import EventCard

logger = logging.getLogger(__name__)

DASHSCOPE_CHAT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

AUTHORS_YAML = Path(__file__).resolve().parent.parent.parent / "config" / "authors.yaml"

RANKER_SYSTEM_PROMPT = """你是 AI 行业情报精排助手，按投资优先 → 产品次之 → 研究调料的顺序排序。

给定一批 Event Card 摘要（含作者档位和信息密度信号），重新排序并筛选出最重要的 25-35 条。

评判标准（按重要性从高到低）：
1. 投资与资金流向信号（融资 / M&A / IPO / 财报 / capex / 供应链）
2. 一手真相权重优先（官方 changelog / changelog / release notes）
3. 作者档位（S 投资人 / S 创始人 > A 工程 > B 研究；降权 list 只在别无选择时保留）
4. 信息密度（avg_info_density = avg bookmark/like ratio，>0.3 为高密度信号，<0.05 为情绪反应丢弃）
5. 行业影响力（是否改变竞争格局）
6. 时效性（是否刚发生）
7. 受众价值（技术决策者和投资团队是否关心）

🔴 必报（放在前 5）：
- 融资 > $50M / 估值跨 10X / 大厂 AI capex 指引调整
- GPU/HBM 供应变化 / 头部高管离职 / 头部模型 API 降价 > 30%

❌ 直接丢弃：
- 纯个人作品秀 / AI 生成图片 demo
- KOL 纯情绪表达（info_density < 0.05）
- 普通 hackathon / 学生项目

输出严格 JSON 格式：
{"ranked_ids": ["evt_xxx", "evt_yyy", ...]}

只返回排序后的 event_id 列表，最多 35 条。"""


@lru_cache(maxsize=1)
def _load_authors_config() -> dict:
    """Load KOL tier config from authors.yaml once."""
    if not AUTHORS_YAML.exists():
        logger.warning("authors.yaml not found at %s, using empty tier map", AUTHORS_YAML)
        return {"tiers": {}, "_handle_to_tier": {}, "_tier_weights": {}}

    with open(AUTHORS_YAML, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    tiers = cfg.get("tiers", {})
    handle_to_tier: dict[str, str] = {}
    tier_weights: dict[str, float] = {}
    for tier_name, tier_cfg in tiers.items():
        tier_weights[tier_name] = float(tier_cfg.get("weight", 1.0))
        for h in tier_cfg.get("handles", []) or []:
            handle_to_tier[h.lower().lstrip("@")] = tier_name

    cfg["_handle_to_tier"] = handle_to_tier
    cfg["_tier_weights"] = tier_weights
    return cfg


def tier_for_handle(handle: str) -> str:
    """Return tier name for a given Twitter handle; 'unknown' if not whitelisted."""
    if not handle:
        return "unknown"
    cfg = _load_authors_config()
    return cfg.get("_handle_to_tier", {}).get(handle.lower().lstrip("@"), "unknown")


def weight_for_tier(tier: str) -> float:
    """Return weight for a tier name; 1.0 if unknown."""
    cfg = _load_authors_config()
    return cfg.get("_tier_weights", {}).get(tier, 1.0)


class Ranker:
    """Re-rank Event Cards by importance using LLM, boosted by author tier and info density."""

    def __init__(self, api_key: str | None = None, model: str = "qwen-plus"):
        self.api_key = api_key or os.environ["DASHSCOPE_API_KEY"]
        self.model = model
        self.top_n = 35

    def rank(self, events: list[EventCard]) -> list[EventCard]:
        """Return top events sorted by importance (LLM + tier boost)."""
        # Pre-boost every event's importance using author tier + info density signals.
        self._apply_tier_boost(events)

        if len(events) <= self.top_n:
            return sorted(events, key=lambda e: e.importance, reverse=True)

        try:
            return self._llm_rank(events)
        except Exception as e:
            logger.warning("LLM ranking failed, falling back to score sort: %s", e)
            return self._score_rank(events)

    @staticmethod
    def _apply_tier_boost(events: list[EventCard]) -> None:
        """Mutate event.importance based on max author tier weight and avg info density.

        importance_new = importance_raw * max_tier_weight * density_multiplier
        """
        for e in events:
            if not e.sources:
                continue
            # Max author tier weight among sources (authoritative voices dominate)
            max_tier_w = max(
                (weight_for_tier(s.author_tier or tier_for_handle(s.author))
                 for s in e.sources),
                default=1.0,
            )
            # Average info density — >0.3 is high, <0.05 is noise
            densities = [s.info_density for s in e.sources if s.like_count > 0]
            avg_density = sum(densities) / len(densities) if densities else 0.0
            if avg_density >= 0.3:
                density_mult = 1.4
            elif avg_density >= 0.15:
                density_mult = 1.15
            elif avg_density >= 0.05:
                density_mult = 1.0
            else:
                density_mult = 0.6  # likely emotional reaction / low signal

            e.importance = round(e.importance * max_tier_w * density_mult, 2)

    def _llm_rank(self, events: list[EventCard]) -> list[EventCard]:
        summaries: list[str] = []
        for e in events:
            tiers_seen = sorted({(s.author_tier or tier_for_handle(s.author)) for s in e.sources})
            tiers_str = ",".join(tiers_seen) or "unknown"
            densities = [s.info_density for s in e.sources if s.like_count > 0]
            avg_density = sum(densities) / len(densities) if densities else 0.0
            summaries.append(
                f"- {e.event_id}: [imp={e.importance:.1f} tier={tiers_str} density={avg_density:.2f} cat={e.category.value} sz={e.cluster_size}] {e.title}"
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
                    {"role": "user", "content": f"请精排以下 {len(events)} 条事件：\n\n" + "\n".join(summaries)},
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

        seen = set(ranked_ids)
        remaining = sorted(
            [e for e in events if e.event_id not in seen],
            key=lambda e: e.importance,
            reverse=True,
        )
        ranked.extend(remaining)

        return ranked[: self.top_n]

    def _score_rank(self, events: list[EventCard]) -> list[EventCard]:
        """Fallback: importance (already tier-boosted) * cluster_size weight."""
        def score(e: EventCard) -> float:
            size_boost = min(e.cluster_size / 3, 2.0)
            return e.importance * (1 + size_boost * 0.2)

        return sorted(events, key=score, reverse=True)[: self.top_n]
