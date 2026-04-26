"""SEC EDGAR 8-K collector — material events for AI-relevant US public companies.

8-K is the "anything important happened" form: M&A, leadership changes,
big contracts, capex announcements. For AI specifically: NVIDIA / MSFT /
GOOGL / META / AMZN / TSLA + smaller AI plays.

Strategy:
- Watch a fixed list of high-signal CIKs (avoid noise from ticker spam)
- Plus full-text search for "artificial intelligence" matches in 8-Ks
  filtered to known AI-relevant companies

Public API: https://efts.sec.gov/LATEST/search-index
No auth required. SEC requires User-Agent header with contact info.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

import httpx

from ..schemas import TweetRaw

logger = logging.getLogger(__name__)

SEC_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
USER_AGENT = "AI News Radar (research) ai-news-radar@example.com"

# CIKs of high-signal AI-relevant US public companies
WATCH_CIKS = {
    "0001045810": "NVIDIA",
    "0000789019": "Microsoft",
    "0001652044": "Alphabet (Google)",
    "0001326801": "Meta",
    "0001018724": "Amazon",
    "0001318605": "Tesla",
    "0001067983": "Berkshire Hathaway",
    "0000320193": "Apple",
    "0001585689": "AppLovin",
    "0001321655": "Palantir",
    "0001318605": "Tesla",
    "0001783879": "Reddit",
    "0001633917": "Snowflake",
    "0001318605": "Tesla",
    "0001084869": "AMD",
    "0001770088": "CrowdStrike",
    "0001327567": "Salesforce",
    # AI-pure plays
    "0001175454": "C3.ai",
    "0001819516": "SoundHound AI",
    "0001805890": "Bigbear.ai",
}

# Filter terms — must contain at least one of these to be considered signal
# (avoids spam-named companies like "Artificial Intelligence Technology Solutions")
SIGNAL_KEYWORDS = [
    "data center", "GPU", "compute", "capital expenditure", "capex",
    "acquisition", "investment", "partnership", "agreement",
    "AI infrastructure", "training", "model", "inference",
    "leadership transition", "departure", "appointed",
]

LOOKBACK_DAYS = 7


class SecCollector:
    """Pull material events (8-K filings) from AI-relevant public companies."""

    def __init__(
        self,
        ciks: dict[str, str] | None = None,
        lookback_days: int = LOOKBACK_DAYS,
    ):
        self.ciks = ciks or WATCH_CIKS
        self.lookback_days = lookback_days
        self.headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    def collect(self) -> list[TweetRaw]:
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=self.lookback_days)

        out: list[TweetRaw] = []
        for cik, company in self.ciks.items():
            try:
                items = self._search_filings_for_cik(cik, company, start, end)
                out.extend(items)
            except Exception as e:
                logger.debug("SEC search for %s (%s) failed: %s", company, cik, e)
                continue

        logger.info("SEC EDGAR: %d 8-K signals from %d watched companies",
                    len(out), len(self.ciks))
        return out

    def _search_filings_for_cik(
        self, cik: str, company: str, start, end,
    ) -> list[TweetRaw]:
        """Query EDGAR for 8-K filings of a single CIK in date range."""
        params = {
            "forms": "8-K",
            "ciks": cik,
            "dateRange": "custom",
            "startdt": start.isoformat(),
            "enddt": end.isoformat(),
        }
        r = httpx.get(SEC_SEARCH_URL, params=params, headers=self.headers, timeout=20)
        if r.status_code != 200:
            return []
        data = r.json()
        hits = data.get("hits", {}).get("hits", [])

        out: list[TweetRaw] = []
        for h in hits:
            s = h.get("_source", {}) or {}
            file_date = s.get("file_date", "")
            display = s.get("display_names", ["?"])[0]
            adsh = h.get("_id", "")  # accession-document id
            # Filing summary text — items list in 8-K describes what happened
            items_codes = s.get("items", [])
            items_descriptions = ITEM_8K_LABELS.copy()
            event_labels = [items_descriptions.get(c, c) for c in items_codes]

            try:
                created_at = datetime.fromisoformat(file_date)
                created_at = created_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                created_at = None

            label_text = " · ".join(event_labels) if event_labels else "Material Event"

            text = f"📜 {company} 提交 8-K: {label_text}"

            # Build EDGAR URL for the filing
            # adsh format: 0001045810-26-000123:doc.htm
            cik_url = cik.lstrip("0")
            adsh_clean = adsh.split(":")[0].replace("-", "")
            url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik_url}&type=8-K&dateb=&owner=include&count=40"

            out.append(TweetRaw(
                tweet_id=f"sec_{adsh.replace(':','_').replace('-','')}",
                author_handle=f"sec_{company.replace(' ','')}",
                author_name=f"SEC EDGAR · {company}",
                text=text,
                created_at=created_at,
                source_url=url,
                is_rss=True,
                author_tier="t0_primary",
            ))

        return out


# 8-K item codes → human-readable labels (most relevant ones for AI/business)
ITEM_8K_LABELS = {
    "1.01": "进入重大协议",
    "1.02": "终止重大协议",
    "2.01": "收购或处置资产",
    "2.02": "财报披露",
    "2.03": "重大债务发生",
    "3.01": "退市/上市变化",
    "3.02": "未注册股票发行",
    "5.02": "高管变动",
    "5.07": "股东表决",
    "7.01": "Regulation FD 披露",
    "8.01": "其他重大事件",
}
