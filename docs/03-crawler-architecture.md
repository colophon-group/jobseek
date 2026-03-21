# Crawler Architecture

The crawler is split into three layers so each can be deployed, tested, and reasoned about independently.

## Three Layers

```
┌─────────────────────────────────────────────────────┐
│  SCHEDULER (deployment-specific)                    │
│  Fly.io: async poll loop with adaptive interval     │
│  CLI: --once, --board, --dry-run                    │
├─────────────────────────────────────────────────────┤
│  BATCH PROCESSOR (portable)                         │
│  Claims due boards from DB (FOR UPDATE SKIP LOCKED) │
│  Runs N single jobs concurrently (asyncio TaskGroup)│
│  Handles fallback chains, enrichment, R2 uploads    │
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
async def monitor_one(board: dict, http: httpx.AsyncClient) -> MonitorResult:
    """Discover jobs on one board. Returns URLs and/or rich job data."""
```

- Input: board config (URL, monitor type, monitor config JSON)
- Output: `MonitorResult` with discovered URLs and optionally full job data
- Delegates to the appropriate monitor implementation (30+ types)
- Rich monitors (`rich=True`) return full job data — scraper step is skipped
- Streaming monitors (`stream=<generator>`) yield batches for large datasets

### scrape_one

```python
async def scrape_one(url: str, scraper_type: str, config: dict, http: httpx.AsyncClient) -> JobContent:
    """Extract structured job data from one URL."""
```

- Input: job page URL + scraper type + config
- Output: `JobContent` with title, description, locations, salary, etc.
- Called for URL-only monitors (sitemap, dom) and for enrichment of rich monitors
- Delegates to scraper implementations (json-ld, nextdata, embedded, dom, api_sniffer, etc.)
- Supports fallback chains: `scraper_config.fallback` defines secondary scrapers
- Field-level fallbacks: `fallback.fields` limits which fields come from the fallback

### Key Property

Single job functions are testable in isolation — no database, no pool, no global state. Pass in an HTTP client and config, get back data.

## Layer 2: Batch Processor

Orchestrates a batch of single jobs. Handles DB reads/writes, concurrency, error reporting, R2 uploads, and field enrichment. Portable across all environments.

### Monitor Batch

```python
async def process_monitor_batch(pool, http, limit=10) -> BatchResult:
    """Claim due boards, run monitor_one for each, write results to DB."""
```

1. Claims boards where `next_check_at <= now()` using `FOR UPDATE SKIP LOCKED`
2. Runs `monitor_one()` for each board concurrently via `asyncio.TaskGroup`
3. For each result:
   - Runs the diff algorithm (new / relisted / gone URLs)
   - If rich data: inserts full job_posting rows
   - If URLs only: inserts URL stubs + schedules for scraping
   - If `enrich` configured: schedules enrichment scraping for listed fields
   - Uploads descriptions + extras to R2 (after DB transaction)
4. Returns batch statistics

### Scrape Batch

```python
async def process_scrape_batch(pool, http, limit=10) -> BatchResult:
    """Claim due postings for scraping, run scrape_one for each, write results to DB."""
```

1. Claims postings where `next_scrape_at <= now()` using `FOR UPDATE SKIP LOCKED`
2. Groups by target hostname for domain-parallel execution
3. Runs `scrape_one()` for each URL, applies fallback chain if configured
4. Updates `job_posting` with extracted content
5. Uploads descriptions + extras to R2 (outside DB transaction)
6. Records success/failure, applies exponential backoff on failure

### Concurrency Control

- `limit` parameter controls how many boards/URLs are claimed per batch
- `asyncio.TaskGroup` runs them concurrently within a batch
- `FOR UPDATE SKIP LOCKED` prevents multiple crawlers from claiming the same board
- Failed boards get exponential backoff (5 → 10 → 20 → 40 min, capped at 24h, auto-disabled at 5 consecutive failures)

## Layer 3: Scheduler

Thin, environment-specific wrapper that calls the batch processor on a schedule.

### Fly.io (default): Adaptive Poll Loop

```python
async def run_poll_loop(shutdown_event):
    """Long-running process. Backs off when idle, responds quickly to new work."""
    while not shutdown_event.is_set():
        did_work = await process_monitor_batch(...) or await process_scrape_batch(...)
        interval = 1.0 if did_work else min(interval * 2, max_interval)
        await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
```

### Single-board mode

```bash
uv run scheduler --board <board_slug>                    # Full run (monitor + scrape)
uv run scheduler --board <board_slug> --dry-run          # Test without DB writes
uv run scheduler --board <board_slug> --dry-run --verbose  # Show all extracted fields
uv run scheduler --board <board_slug> --force-rescrape   # Scrape all active jobs
```

### One-shot (CLI / testing)

```bash
uv run scheduler --once           # Process one batch and exit
uv run scheduler --monitor-only   # Only monitor (no scraping)
uv run scheduler --scrape-only    # Only scrape (no monitoring)
```

## File Structure

```
apps/crawler/src/
├── core/                        # Pure business logic (Layer 1)
│   ├── monitors/                # Monitor implementations (30+ types)
│   │   ├── __init__.py          # Registry + DiscoveredJob dataclass
│   │   ├── greenhouse.py        # Greenhouse JSON API (rich)
│   │   ├── lever.py             # Lever Postings API (rich)
│   │   ├── api_sniffer.py       # API capture — browser or HTTP mode
│   │   ├── nextdata.py          # Next.js __NEXT_DATA__ discovery
│   │   ├── rss.py               # RSS 2.0 feed (SuccessFactors, Teamtailor, generic)
│   │   ├── sitemap.py           # XML sitemap parser (URL-only)
│   │   ├── dom.py               # Playwright DOM-based discovery (URL-only)
│   │   ├── workday.py           # Workday Job Board API (streaming)
│   │   └── ...                  # ashby, bite, breezy, deel, dvinci, gem, etc.
│   ├── scrapers/                # Scraper implementations
│   │   ├── __init__.py          # Registry + JobContent dataclass
│   │   ├── jsonld.py            # JSON-LD extractor (schema.org/JobPosting)
│   │   ├── nextdata.py          # Next.js data extractor
│   │   ├── embedded.py          # Generalized embedded JSON extractor
│   │   ├── api_sniffer.py       # XHR/fetch capture or direct HTTP API
│   │   ├── dom.py               # Step-based extraction (static or Playwright)
│   │   ├── pdf.py               # PDF document scraper
│   │   └── ...                  # workday, workable, smartrecruiters, etc.
│   ├── description_store.py     # R2 upload/diff-track (descriptions + extras)
│   ├── enum_normalize.py        # employment_type + job_location_type normalizers
│   ├── location_resolve.py      # Location → GeoNames ID resolution
│   ├── salary_extract.py        # Heuristic salary parsing from HTML
│   ├── monitor.py               # monitor_one() dispatcher
│   └── scrape.py                # scrape_one() dispatcher
├── batch.py                     # Batch processor (Layer 2)
├── scheduler.py                 # Scheduler (Layer 3)
├── sync.py                      # CSV → DB sync
├── inspect.py                   # CSV validation + diagnostics
├── csvtool.py                   # CSV management library
├── db.py                        # DB connection pool
├── config.py                    # Settings (pydantic-settings)
├── shared/
│   ├── api_sniff.py             # API sniffing utilities
│   ├── browser.py               # Playwright browser launch (stealth, proxy)
│   ├── nextdata.py              # Shared field extraction (extract_field, map, list spec)
│   ├── http.py                  # HTTP client factory
│   ├── proxy.py                 # Per-domain proxy routing (PROXY_MAP)
│   ├── logging.py               # Structured logging
│   ├── constants.py             # DATA_DIR, WORKSPACE_DIR, etc.
│   ├── csv_io.py                # CSV read/write utilities
│   └── slug.py                  # Slugify utility
└── workspace/                   # Workspace CLI (ws command)
    ├── cli.py                   # Click entry point
    ├── commands/                # Command implementations
    ├── state.py                 # YAML workspace state
    ├── workflow.py              # Workflow engine + KB search
    ├── steps/                   # Step instruction markdown
    └── kb/                      # Troubleshooting entries + case studies
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
        → if enrich configured: schedule enrichment for listed fields
        → upload descriptions + extras to R2 (after DB transaction)
        → persist description_r2_hash for change detection
    → record success/failure per board

  → process_scrape_batch()
    → claim due postings (SQL: FOR UPDATE SKIP LOCKED, next_scrape_at <= now())
    → group by target hostname
    → asyncio.TaskGroup: one pipeline per hostname (serial within)
    → for each posting:
        → scrape_one(url, scraper_config, http) → JobContent
        → apply fallback chain if configured (field-level or full replacement)
        → update job_posting with extracted content
        → upload descriptions + extras to R2
    → record success/failure per posting
```
