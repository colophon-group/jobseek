# Crawler Architecture

The crawler is split into three layers so each can be deployed, tested, and reasoned about independently.

## Three Layers

```
┌─────────────────────────────────────────────────────┐
│  SCHEDULER (deployment-specific)                    │
│  Fly.io: async poll loop                            │
│  Apify: built-in cron → actor start                 │
│  GH Actions: workflow_dispatch / schedule            │
│  CLI: one-shot (for testing)                        │
├─────────────────────────────────────────────────────┤
│  BATCH PROCESSOR (portable)                         │
│  Claims due boards from DB (FOR UPDATE SKIP LOCKED) │
│  Runs N single jobs concurrently (asyncio TaskGroup)│
│  Reports results, handles errors                    │
├─────────────────────────────────────────────────────┤
│  SINGLE JOB (portable, pure async function)         │
│  monitor_one(board_config) → MonitorResult          │
│  scrape_one(url, scraper_config) → JobContent       │
│  No DB awareness — takes input, returns output      │
└─────────────────────────────────────────────────────┘
```

## Layer 1: Single Job

Pure async functions with no side effects beyond HTTP requests. Takes config in, returns data out.

### monitor_one

```python
async def monitor_one(board: BoardConfig, http: httpx.AsyncClient) -> MonitorResult:
    """Discover jobs on one board. Returns URLs and/or rich job data."""
```

- Input: board config (URL, monitor type, monitor config JSON)
- Output: `MonitorResult` with discovered URLs and optionally full job data
- Delegates to the appropriate monitor implementation (greenhouse, lever, sitemap, discover)

### scrape_one

```python
async def scrape_one(url: str, config: ScraperConfig, http: httpx.AsyncClient) -> JobContent:
    """Extract structured job data from one URL."""
```

- Input: job page URL + scraper config
- Output: `JobContent` with title, description, locations, salary, etc.
- Only called for URL-only monitors (sitemap, discover). API monitors skip this.
- Delegates to the appropriate scraper implementation (json-ld, html, browser)

### Key Property

Single job functions are testable in isolation — no database, no pool, no global state. Pass in an HTTP client and config, get back data.

## Layer 2: Batch Processor

Orchestrates a batch of single jobs. Handles DB reads/writes, concurrency, and error reporting. Portable across all environments.

### Monitor Batch

```python
async def process_monitor_batch(pool, http, limit=10) -> BatchResult:
    """Claim due boards, run monitor_one for each, write results to DB."""
```

1. Claims boards where `next_check_at <= now()` using `FOR UPDATE SKIP LOCKED`
2. Runs `monitor_one()` for each board concurrently via `asyncio.TaskGroup`
3. For each result:
   - Runs the diff algorithm (new / relisted / gone URLs)
   - Inserts new job postings (rich data) or enqueues URLs (for scraping)
   - Records success/failure with exponential backoff
4. Returns batch statistics

### Scrape Batch

```python
async def process_scrape_batch(pool, http, limit=10) -> BatchResult:
    """Claim due postings for scraping, run scrape_one for each, write results to DB."""
```

1. Claims postings where `next_scrape_at <= now()` using `FOR UPDATE SKIP LOCKED`
2. Groups by target hostname for domain-parallel execution
3. Runs `scrape_one()` for each URL, updates `job_posting` with extracted content
4. Uploads descriptions + extras to R2 (outside DB transaction)
5. Records success/failure, applies exponential backoff on failure

### Concurrency Control

- `limit` parameter controls how many boards/URLs are claimed per batch
- `asyncio.TaskGroup` runs them concurrently within a batch
- `FOR UPDATE SKIP LOCKED` prevents multiple crawlers from claiming the same board
- Failed boards get exponential backoff starting at 5 minutes (5 → 10 → 20 → 40 min, capped at 24h, auto-disabled at 5 consecutive failures)

## Layer 3: Scheduler

Thin, environment-specific wrapper that calls the batch processor on a schedule.

### Fly.io (default): Poll Loop

```python
async def run_poll_loop(interval=15):
    """Long-running process. Polls every `interval` seconds."""
    while not shutdown:
        await process_monitor_batch(pool, http)
        await process_scrape_batch(pool, http)
        await asyncio.sleep(interval)
```

Deployed as a Fly.io process. Handles SIGTERM/SIGINT for graceful shutdown. This is the default scheduler used in production.

### One-shot (CLI / testing)

```python
async def run_once():
    """Process one batch and exit."""
    await process_monitor_batch(pool, http)
    await process_scrape_batch(pool, http)
```

Useful for local testing, GitHub Actions, or any environment that triggers jobs externally.

### Running

```bash
cd apps/crawler

# Poll loop (production)
uv run scheduler

# One-shot
uv run scheduler --once

# Monitor-only (no scraping)
uv run scheduler --monitor-only

# Scrape-only (no monitoring)
uv run scheduler --scrape-only
```

## File Structure

```
apps/crawler/src/
├── core/                        # Pure business logic (Layer 1)
│   ├── monitors/                # Monitor implementations
│   │   ├── __init__.py          # Registry + DiscoveredJob dataclass
│   │   ├── greenhouse.py        # Greenhouse JSON API
│   │   ├── lever.py             # Lever Postings API
│   │   ├── sitemap.py           # XML sitemap parser
│   │   └── discover.py          # Playwright-based auto-discovery
│   ├── scrapers/                # Scraper implementations
│   │   ├── __init__.py          # Registry + JobContent dataclass
│   │   ├── jsonld.py            # JSON-LD extractor
│   │   ├── html.py              # CSS selector-based extraction
│   │   └── browser.py           # Playwright-based extraction
│   ├── description_store.py     # R2 upload/diff-track (descriptions + extras)
│   ├── enum_normalize.py        # employment_type + job_location_type normalizers
│   ├── location_resolve.py      # Location → GeoNames ID resolution
│   ├── monitor.py               # monitor_one() dispatcher
│   └── scrape.py                # scrape_one() dispatcher
├── batch.py                     # Batch processor (Layer 2)
├── scheduler.py                 # Scheduler (Layer 3)
├── sync.py                      # CSV → DB sync
├── inspect.py                   # CSV validation + diagnostics
├── db.py                        # DB connection pool
├── config.py                    # Settings (pydantic-settings)
├── scripts/                     # One-off migration scripts
│   ├── migrate_descriptions_to_r2.py
│   ├── backfill_locations.py
│   └── seed_geonames.py
└── shared/
    ├── api_sniff.py             # API sniffing utilities
    ├── http.py                  # HTTP client factory
    ├── logging.py               # Structured logging
    ├── constants.py             # DATA_DIR, WORKSPACE_DIR, etc.
    ├── csv_io.py                # CSV read/write utilities
    └── slug.py                  # Slugify utility
```

## Data Flow

```
Scheduler
  → process_monitor_batch()
    → claim due boards (SQL: FOR UPDATE SKIP LOCKED)
    → group by rate-limit domain (_throttle_key)
    → asyncio.TaskGroup: one pipeline per domain (serial within)
    → for each board:
        → monitor_one(board_config, http) → MonitorResult
        → diff against known postings (SQL: CTE — new/touched/relisted/gone)
        → if rich data: insert full job_posting rows
        → if URLs only: insert URL stubs + schedule for scraping
        → upload descriptions + extras to R2 (after DB transaction)
        → persist description_r2_hash for change detection
    → record success/failure per board

  → process_scrape_batch()
    → claim due postings (SQL: FOR UPDATE SKIP LOCKED, next_scrape_at <= now())
    → group by target hostname
    → asyncio.TaskGroup: one pipeline per hostname (serial within)
    → for each posting:
        → scrape_one(url, scraper_config, http) → JobContent
        → update job_posting with extracted content
        → upload descriptions + extras to R2
    → record success/failure per posting
```
