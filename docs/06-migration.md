# Migration

From the current draft architecture to the new agent-driven design. Since the current CSV is empty and the resolver is draft code, this is a clean cutover with no data migration needed.

## Phases

### Phase 1 — Add New Structure (non-breaking)

Everything in this phase is additive. Nothing existing breaks.

**Created**:
- `docs/` — architecture documentation (this directory)
- `AGENTS.md` — provider-agnostic agent instructions
- `CLAUDE.md` — one-line `@AGENTS.md` import for Claude Code
- `data/companies.csv` — updated schema (slug, name, website, logo_url, icon_url)
- `data/boards.csv` — new board config file
- `src/core/` — pure business logic module
  - `src/core/monitors/` — monitor implementations (adapted from crawler_types/)
  - `src/core/scrapers/` — scraper implementations (json-ld, html, browser)
  - `src/core/monitor.py` — `monitor_one()` dispatcher
  - `src/core/scrape.py` — `scrape_one()` dispatcher
- `src/batch.py` — portable batch processor
- `src/scheduler.py` — poll-loop scheduler (replaces monitor/main.py)
- `src/sync.py` — CSV → DB sync
- `src/inspect.py` — CSV validation

### Phase 2 — Wire Up GitHub Actions

**Created/activated**:
- `.github/workflows/resolve-company-requests.yml` — activated from `.example`, updated to use AGENTS.md instructions
- `.github/workflows/auto-merge-config.yml` — auto-merge for low-risk config PRs
- CSV validation added to CI pipeline

### Phase 3 — Remove Old Code

**Removed**:
| Component | Reason |
|-----------|--------|
| `src/resolver/` (entire directory) | Replaced by coding agent workflow |
| `src/monitor/` (directory) | Reorganized into `src/core/monitors/` |
| `src/extractor/` (stub) | Replaced by `src/core/scrapers/` |
| `openai` dependency | Resolver was the only consumer |
| `resolver` Fly.io process | No longer needed |
| `resolver` script entry point | No longer needed |
| `extractor` script entry point | Replaced by scraper in scheduler |

**Updated**:
- `pyproject.toml` — removed `openai` dep, updated entry points
- `fly.toml` — removed resolver process, updated monitor process
- `.env.example` — removed `OPENAI_API_KEY`, `RESOLVER_MODEL`
- `Dockerfile` — copies `data/` directory for sync

### Phase 4 — Deploy and Verify

1. Deploy to Fly.io with sync step (run `uv run python -m src.sync` on startup)
2. Manually add test companies via CSV PR to verify end-to-end flow
3. Verify the GitHub Actions workflow picks up a test issue
4. Monitor logs for successful board checks

## What Was Removed

| Old Component | New Replacement |
|--------------|-----------------|
| `src/resolver/` | AGENTS.md + coding agent workflow |
| `src/resolver/ai.py` (OpenAI web search) | Agent's built-in web browsing |
| `src/resolver/screening.py` (AI screening) | Agent judgment + PR review |
| `src/resolver/queries.py` (DB writes) | CSV config → DB sync on deploy |
| `src/resolver/resolver.py` (orchestrator) | Agent follows AGENTS.md steps |
| `src/monitor/main.py` (poll loop) | `src/scheduler.py` |
| `src/monitor/checker.py` (board check) | `src/batch.py` + `src/core/monitor.py` |
| `src/monitor/differ.py` (diff logic) | Kept in `src/batch.py` |
| `src/monitor/queries.py` (SQL) | Kept in `src/batch.py` |
| `src/monitor/crawler_types/` | `src/core/monitors/` |
| `src/extractor/` (empty stub) | `src/core/scrapers/` |
| `openai` Python package | Not needed — agents use their own tools |

## What Was Kept

- **Database schema** — `company`, `job_board`, `job_posting`, `job_posting_version`, `job_url_queue` tables unchanged
- **`company_request` table** — kept as audit log (no longer source of truth)
- **Core SQL patterns** — `FOR UPDATE SKIP LOCKED`, diff CTE, exponential backoff
- **Monitor logic** — greenhouse, lever, sitemap implementations moved to `src/core/monitors/`
- **Shared utilities** — HTTP client factory, structured logging, slugify
- **`asyncpg` + `httpx` + `structlog`** — same Python stack, minus OpenAI

## Rollback

If the new architecture has issues:
1. The old `src/monitor/` code is in git history
2. The DB schema is unchanged — old code can read the same tables
3. Re-add the `resolver` process to fly.toml to restore automated resolution
4. The CSV config is additive — it doesn't break anything if unused
