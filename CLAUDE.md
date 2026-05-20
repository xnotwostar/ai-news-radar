# CLAUDE.md

Guidance for Claude / AI assistants working in this repository.

## What this project is

**AI News Radar** is an automated daily + weekly intelligence pipeline for the AI
industry. It scrapes Twitter lists, RSS feeds, HackerNews, Reddit, HuggingFace,
OpenRouter, and SEC EDGAR; deduplicates and clusters the signals; uses LLMs to
synthesize structured Event Cards; then writes Chinese-language Markdown reports
that are published as HTML to GitHub Pages and archived to an Obsidian vault.

Reports are bilingual in surface (English source quotes preserved, structured
analysis in Chinese) and follow an FT × Bloomberg × Stratechery visual aesthetic.

Two daily pipelines run side-by-side:
- `global_ai` — Western AI ecosystem (KOL tweets + RSS + HN + Reddit + HF + OpenRouter + SEC)
- `china_ai`  — Chinese AI ecosystem (KOL tweets + CN RSS + Newsnow trending merged in)

Plus a weekly digest synthesized from the daily Markdown files.

## Repository layout

```
src/
├── pipeline.py           # Orchestrator — main daily entry point
├── weekly.py             # Weekly digest CLI entry point
├── schemas.py            # Pydantic models — TweetRaw, EventCard, EventSource, PipelineConfig, ...
├── collector/            # Source fetchers (one file per source type)
│   ├── apify_client.py     # Twitter lists via Apify actor (primary source)
│   ├── rss_collector.py    # RSS — uses changelog_feeds.yaml + newsletters.yaml
│   ├── hn_collector.py     # HackerNews Algolia API
│   ├── reddit_collector.py # Engineering subreddits
│   ├── hf_collector.py     # HuggingFace trending models
│   ├── openrouter_collector.py # New model launches / price changes
│   ├── sec_collector.py    # SEC EDGAR 8-K filings
│   └── newsnow_client.py   # Chinese trending (china_ai only)
├── processor/            # Embed → cluster → dedup → build events → rank
│   ├── embedder.py         # Gemini embeddings (DashScope fallback)
│   ├── clusterer.py        # HDBSCAN on cosine distance
│   ├── dedup.py            # HistoryDeduplicator — strip events seen in last 3 days
│   ├── event_builder.py    # LLM → EventCard (concurrent, 5-way)
│   └── ranker.py           # LLM re-rank + tier/info-density boost
├── generator/            # EventCards → Markdown report
│   ├── llm_client.py       # Unified fallback chain (Gemini → DashScope → DeepSeek)
│   ├── report_writer.py    # Daily report generator
│   └── weekly_writer.py    # Weekly synthesis from daily MDs
└── publisher/            # Markdown → output destinations
    ├── html_publisher.py   # Legacy single-file HTML (markdown → styled HTML)
    ├── jinja_publisher.py  # New Jinja2 templates (FT/Bloomberg theme, _v2.html)
    └── obsidian_publisher.py # Write to local Obsidian vault + git push

config/                   # YAML config — see "Config files" below
prompts/                  # System prompts (Chinese) for LLM stages
templates/                # Jinja2 HTML templates + intel.css
docs/                     # GitHub Pages output (committed; reports/ + posters/)
data/
├── raw/                  # Collector dumps (JSON, by date)
├── events/               # EventCard JSON (one file per pipeline-date)
└── reports/              # Final Markdown reports (and reports/weekly/)
scripts/                  # Standalone utilities (re-render HTML, test report)
tests/                    # pytest tests (collector + processor + generator)
.github/workflows/        # daily_report.yml (06:30 CST) + weekly.yml (Mon 07:00 CST)
```

## Daily pipeline lifecycle (`src/pipeline.py`)

For each pipeline (global_ai → wait 60s → china_ai):

1. **Collect** — Apify Twitter list → primary tweets. Then merge (global_ai only):
   creator narrative list (downweighted ×0.3), RSS, HackerNews, Reddit, HuggingFace,
   OpenRouter, SEC. For china_ai: only CN RSS is merged; Newsnow trending is fetched
   and passed to ReportWriter for a separate appended section.
2. **Embed** — Each tweet/article text → embedding via `Embedder` (Gemini primary,
   DashScope fallback). RSS items are converted to `TweetRaw` shape with `is_rss=True`.
3. **Cluster** — HDBSCAN on cosine distance (`cluster_threshold` from `pipeline.yaml`,
   default 0.82). Mega-clusters (>30) get sub-clustered with a tighter threshold.
   Noise (label -1) is kept only for the top-K by engagement, batched into pseudo-clusters.
4. **Build Event Cards** — For each cluster, call LLM concurrently (semaphore=5) to
   produce structured `EventCard` JSON: title, category, importance (1-10), key_facts,
   analyst_angle, event_type (news/opinion). Falls back through Gemini → DashScope → DeepSeek.
5. **History dedup** — Strip events whose titles share ≥2 keywords with events from
   the prior `lookback_days=3`. Skip with env `NO_DEDUP=1` or `--no-dedup` flag.
6. **Rank** — Pre-boost importance by max author-tier weight × info-density multiplier
   (`bookmark_count / like_count`). Then LLM re-ranks if >35 events; otherwise sort by
   importance. Top 35 returned.
7. **Generate Report** — LLM produces Markdown with sections: 今日核心判断 → 🔥 重大发布 →
   🔬 技术研究 → 💰 融资市场 → 🔧 芯片算力 → ⚡ 速览 → 💡 专家视角.
8. **Publish** — Three outputs in parallel (best-effort, failures logged not fatal):
   - `HtmlPublisher` → `docs/reports/{date}_{pipeline}.html` (legacy single-file)
   - `JinjaPublisher` → `docs/reports/{date}_{pipeline}_v2.html` (new theme)
   - `ObsidianPublisher` → writes to `$OBSIDIAN_VAULT_PATH/AI Intel/Daily/` (skip via `NO_OBSIDIAN=1`)
9. **Persist** — `data/raw/`, `data/events/`, `data/reports/` get committed by CI.

## Running locally

```bash
# Install
pip install -r requirements.txt
cp .env.example .env  # fill in API keys

# Run everything (both pipelines)
python -m src.pipeline

# One pipeline only
python -m src.pipeline global_ai
python -m src.pipeline china_ai

# Skip history dedup (useful for testing / backfills)
python -m src.pipeline --no-dedup

# Skip Obsidian (CI does this — vault is on user's mac, not CI runner)
NO_OBSIDIAN=1 python -m src.pipeline global_ai

# Weekly digest (reads from data/reports/*.md, doesn't re-scrape)
python -m src.weekly                    # current ISO week ending today
python -m src.weekly --date 2026-04-26  # week ending given date
python -m src.weekly --dry-run          # print to stdout, don't write
python -m src.weekly --no-push          # write to vault but skip git push

# Re-render a daily HTML from existing event JSON (skip the whole scrape+LLM cycle)
python scripts/render_jinja.py                  # today, both pipelines
python scripts/render_jinja.py 2026-04-25       # specific date
python scripts/render_jinja.py --pipeline china_ai

# Tests
pytest tests/
```

## Required environment variables

```
APIFY_TOKEN              # Twitter scraping (apidojo/twitter-list-scraper)
APIFY_GLOBAL_LIST_ID     # Curated global AI KOL list
APIFY_CHINA_LIST_ID      # Curated China AI KOL list
APIFY_CREATOR_LIST_ID    # Optional — creator narrative list (downweight ×0.3)

GOOGLE_API_KEY           # Gemini — primary for embeddings, report generation, EventBuilder, Ranker
DASHSCOPE_API_KEY        # Alibaba Qwen — fallback
DEEPSEEK_API_KEY         # DeepSeek — last-resort fallback
ANTHROPIC_API_KEY        # Optional — Anthropic Claude (referenced in llm_client but not in models.yaml chain)

OBSIDIAN_VAULT_PATH      # Optional, defaults to ~/Documents/Obsidian Vault
NO_OBSIDIAN=1            # Skip Obsidian publish (always set in CI)
NO_DEDUP=1               # Skip history dedup (also: --no-dedup flag)
```

API keys are loaded via `python-dotenv` from `.env`. **Never log keys.** `Embedder`
sanitizes error messages — follow the same pattern in any new external API code
(`_sanitize_error` strips the key, caps length).

## Config files (`config/`)

- **`pipeline.yaml`** — Per-pipeline source / processing / generation config. Env vars
  resolved at load time via `${VAR_NAME}` syntax.
- **`models.yaml`** — LLM fallback chain for report generation, plus embedding provider.
  Order is significant: `priority: 1` tried first.
- **`authors.yaml`** — KOL whitelist by tier (`s_investor` 4.0 → `s_founder` 3.0 →
  `a_engineering` 2.0 → `b_research` 1.0 → `downweight` 0.3). RSS tiers `t0_primary` /
  `t1_curated` / `t2_community` mapped to weights too. Also holds spam blacklist
  patterns and `per_author_daily_cap`.
- **`changelog_feeds.yaml`** — T0/T1 RSS feeds (model company blogs, hardware,
  individual technical blogs). Each has `tier`, `category`, `weight`, `lookback_hours`,
  `keyword_filter`.
- **`newsletters.yaml`** — T1 curated newsletters (StrictlyVC, Stratechery, etc).
  All native RSS — no Kill the Newsletter dependency.

When adding a new RSS source: pick the right YAML file by content type (changelog vs
newsletter), set realistic `lookback_hours` (most blogs publish every 1-3 days; some
weekly newsletters need 336), and decide `keyword_filter` — generally only AI-focused
publishers can run unfiltered.

## Key conventions

- **Python 3.11** with type hints throughout. Pydantic v2 (`model_dump()`, not `.dict()`).
- **All logs go through `logging`**, not `print`. Format: `%(asctime)s [%(level)s] %(name)s: %(message)s`.
- **Multi-provider fallback is a pattern**, repeated in `Embedder`, `EventBuilder`,
  `Ranker`, and `LLMClient`. When adding a new external LLM/embedding call, follow the
  same `PROVIDER_DEFAULTS` dict + `FALLBACK_CHAIN` list shape — don't reinvent it.
- **Async only where it matters** — `EventBuilder` uses `asyncio` + semaphore=5 because
  it makes N parallel LLM calls. Everything else is sync httpx.
- **Time** — always work in UTC (`datetime.now(timezone.utc)`). Date strings are
  `YYYY-MM-DD`. Report file naming: `{date}_{pipeline}.{ext}`.
- **Tweet vs RSS** — RSS articles are normalized into `TweetRaw` with `is_rss=True` and
  `tweet_id` set to `hashlib.md5(url).hexdigest()[:16]`. EventBuilder pinholes RSS
  sources to the top of `sources` so URLs render prominently.
- **`author_tier`** is the canonical tier value on `TweetRaw` and `EventSource`.
  Apify collector sets it from `authors.yaml`; RSS collector sets it via `tier_map` in
  `pipeline.py`; everything downstream reads it without re-lookup. `tier_for_handle()`
  is the fallback when `author_tier == "unknown"`.
- **Importance scoring** — `EventBuilder` produces a raw 1-10 from the LLM. `Ranker._apply_tier_boost`
  multiplies by `max_tier_weight × density_multiplier` (0.6 / 1.0 / 1.15 / 1.4 based on
  avg `bookmark_count / like_count`). The boosted importance is what `_score_rank`
  falls back to when LLM ranking fails.
- **Mega-cluster handling** — clusters >30 tweets get sub-clustered with a tighter
  threshold (default + 0.08, capped 0.95). If sub-clustering still fails, chunk by
  engagement into MAX_CLUSTER_SIZE batches.
- **No emojis in code or commits** unless they're already part of the data flow
  (Chinese report headings, category emojis, priority markers).
- **Best-effort publishers** — `HtmlPublisher`, `JinjaPublisher`, `ObsidianPublisher`
  all wrap their calls in try/except in `pipeline.py`. One failing should not abort
  the whole run.

## Adding a new collector

1. Create `src/collector/foo_collector.py` exposing a `FooCollector` class with a
   `collect()` method returning `list[TweetRaw]`.
2. Map foreign data into `TweetRaw`: pick the right `author_tier` (or leave `unknown`),
   set `is_rss=True` for non-Twitter, build a stable `tweet_id` (md5 of canonical URL
   is fine), set `source_url`, set `created_at` (UTC).
3. Export from `src/collector/__init__.py`.
4. Wire into `pipeline.py::run_twitter_pipeline` at the appropriate step number,
   wrapped in try/except (collection failures must not abort the pipeline).
5. Save raw output via `_save_raw(items, f"{date_str}_{name}_foo.json")` for debugging.
6. Add a smoke test in `tests/test_collector.py` (mock the external HTTP).

## Adding a new LLM provider

1. Add the provider's `{url, key_env, model}` triple to `PROVIDER_DEFAULTS` in each of
   `embedder.py`, `event_builder.py`, `ranker.py`, and `llm_client.py` (the four
   independent fallback chains). Yes, it's duplicative — that's the current shape.
2. Add to the `FALLBACK_CHAIN` list in each file.
3. If it's OpenAI-compatible: nothing else needed. If not: add a branch in `LLMClient._call`.
4. Optionally add to `config/models.yaml::report_generation` as a priority entry.

## Tests

```bash
pytest tests/                    # everything
pytest tests/test_collector.py   # one file
pytest tests/test_processor.py -k clusterer  # one test
```

Tests are unit-level and mock external APIs (Apify client, HTTP calls). There is no
integration / E2E test suite — full validation is by running the pipeline locally on
the previous day's data with `--no-dedup`.

## CI / Schedules (`.github/workflows/`)

- **`daily_report.yml`** — `cron: '30 22 * * *'` (UTC 22:30 = Beijing 06:30 next day).
  Runs `python -m src.pipeline`, commits `data/` + `docs/`, waits for GitHub Pages
  to deploy. `workflow_dispatch` supports `no_dedup: true` toggle. CI sets `NO_OBSIDIAN=1`.
- **`weekly.yml`** — `cron: '0 23 * * 0'` (Sun 23:00 UTC = Mon 07:00 CST). Runs
  `src.weekly --dry-run`, strips the dry-run banner, writes to `data/reports/weekly/`,
  commits + uploads artifact. Doesn't push to Obsidian (vault not present in CI).

Both jobs need the LLM API key secrets + Apify secrets configured on the repository.

## Common tasks

**Backfill a missed day** — `--no-dedup` so old events aren't suppressed by newer
similar events, then commit the resulting `data/` + `docs/`:
```bash
python -m src.pipeline --no-dedup
```

**Iterate on report formatting** — Re-run only step 7 against existing events:
```bash
python scripts/render_jinja.py 2026-04-25 --pipeline global_ai
```

**Debug a clustering issue** — Drop a breakpoint in `Clusterer.cluster` or load
`data/raw/{date}_{pipeline}.json` directly and re-run only the processor.

**Tune the spam filter** — Patterns live in `config/authors.yaml::blacklist_patterns`
AND mirrored in `src/collector/apify_client.py::SPAM_PATTERNS` for hot-path speed —
keep them in sync if you add a new one.

**Tune ranker behavior** — Edit `RANKER_SYSTEM_PROMPT` in `src/processor/ranker.py`,
or adjust the `_apply_tier_boost` density thresholds.

**Add a downweighted author** — Append handle (no `@`) under `tiers.downweight.handles`
in `authors.yaml`. Weight 0.3 means their tweets still appear but rarely rank highly.

## Output destinations

- **GitHub Pages** — `docs/reports/{date}_{pipeline}.html` (legacy) and
  `docs/reports/{date}_{pipeline}_v2.html` (Jinja2 theme). `docs/intel.css` is synced
  from `templates/intel.css` on each render.
- **Obsidian vault** — `<vault>/AI Intel/Daily/{date}_{pipeline}.md` and
  `<vault>/AI Intel/Weekly/{year}-W{week}.md`. The vault must be a git repo; publisher
  commits and pushes via subprocess (skip with `--no-push`).
- **Repo commit** — CI commits both `data/` and `docs/` after each daily run, so the
  source-of-truth Markdown lives in `data/reports/` in this repo.

## What this project deliberately does NOT do

- **No DingTalk / ServerChan / Slack push** — removed in commit c8b7db3.
  Output is GitHub Pages + Obsidian, period.
- **No real-time / streaming** — daily batch only. Don't add webhooks or websockets.
- **No on-the-fly model selection** — providers come from `models.yaml` and fallback
  chains in code. Don't add user-controlled routing.

## Branch + commit conventions

Daily reports auto-commit as `daily: YYYY-MM-DD` from `AI News Radar Bot`. Weekly as
`weekly: YYYY-W##` from `github-actions[bot]`. Feature work uses conventional-ish
prefixes: `feat:`, `fix:`, `refactor:`, `chore(sources):`, etc. (see `git log`).

Feature branches: develop on the branch the task assigns; never push to `main`
directly except via the CI bots above.
