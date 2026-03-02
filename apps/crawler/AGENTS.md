# AGENTS.md — Crawler

Crawler-specific instructions. See the root [AGENTS.md](../../AGENTS.md) for project-wide context.

## Architecture

Three-layer design:

1. **Single Job** (`src/core/`) — pure async functions, no DB awareness
2. **Batch Processor** (`src/batch.py`) — claims work from DB, runs jobs concurrently
3. **Scheduler** (`src/scheduler.py`) — environment-specific loop/trigger

See [docs/03-crawler-architecture.md](../../docs/03-crawler-architecture.md) for full details.

## Key Files

```
src/
├── core/
│   ├── monitors/          # Monitor implementations
│   │   ├── __init__.py    # Registry + DiscoveredJob dataclass
│   │   ├── greenhouse.py  # Greenhouse JSON API
│   │   ├── lever.py       # Lever Postings API
│   │   ├── sitemap.py     # XML sitemap parser
│   │   └── discover.py    # Playwright-based discovery
│   ├── scrapers/          # Scraper implementations
│   │   ├── __init__.py    # Registry + JobContent dataclass
│   │   ├── jsonld.py      # JSON-LD extractor
│   │   ├── html.py        # CSS selector extraction
│   │   └── browser.py     # Playwright extraction
│   ├── monitor.py         # monitor_one() dispatcher
│   └── scrape.py          # scrape_one() dispatcher
├── batch.py               # Batch processor
├── scheduler.py           # Scheduler (entry point)
├── sync.py                # CSV → DB sync
├── validate.py            # CSV validation
├── db.py                  # asyncpg pool
├── config.py              # pydantic-settings
└── shared/
    ├── http.py            # httpx client factory
    ├── logging.py         # structlog config
    └── slug.py            # slugify utility
```

## Commands

```bash
# Install deps
uv sync

# Run crawler (poll loop)
uv run scheduler

# Run one batch
uv run scheduler --once

# Sync CSVs to DB
uv run python -m src.sync

# Validate CSVs
uv run python -m src.validate

# Run tests
uv run pytest tests/
```

## Adding a New Monitor Type

1. Create `src/core/monitors/<name>.py`
2. Implement `async def discover(board: dict, client: httpx.AsyncClient) -> list[DiscoveredJob] | set[str]`
3. Optionally implement `async def can_handle(url: str, client: httpx.AsyncClient) -> dict | None`
4. Register at module bottom: `register("<name>", discover, cost=<N>, can_handle=can_handle)`
5. Import in `src/core/monitors/__init__.py`

## Adding a New Scraper Type

1. Create `src/core/scrapers/<name>.py`
2. Implement `async def scrape(url: str, config: dict, http: httpx.AsyncClient) -> JobContent`
3. Register at module bottom: `register("<name>", scrape)`
4. Import in `src/core/scrapers/__init__.py`

## Proposing Code Changes

When existing monitors/scrapers can't handle a site, agents may propose code changes.

### Before writing code

1. Exhaust all config options (different monitor type, different scraper type, different selectors)
2. Document what was tried and why it failed
3. Check if a similar issue exists or was resolved before

### Code change scope

- Prefer extending existing types over adding new ones
- If adding a new type, follow the "Adding a New Monitor/Scraper Type" sections above
- Keep changes minimal — fix the specific issue, don't refactor
- Include tests for new code when feasible

### PR requirements

- Label: `review-code`
- Branch: `fix-crawler/<description>`
- PR body: what was tried, what failed, what the code change does
- Include CSV config for the company alongside the code change

## Code Conventions

- `from __future__ import annotations` in every module
- Async everywhere — `asyncpg`, `httpx.AsyncClient`
- Structured logging: `log = structlog.get_logger()`, then `log.info("event.name", key=value)`
- SQL in raw strings (no ORM), using `$1` positional params for asyncpg
- Concurrency: `asyncio.TaskGroup` for parallel work, `FOR UPDATE SKIP LOCKED` for DB claims
- Error handling: exponential backoff on failures (interval doubles, capped at 24h, auto-disable at 5 consecutive failures)
