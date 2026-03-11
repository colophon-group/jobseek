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
│   │   ├── api_sniffer.py # API capture (httpx for public APIs, Playwright for browser-dependent)
│   │   ├── ashby.py       # Ashby Job Board API
│   │   ├── gem.py         # Gem ATS Job Board API
│   │   ├── greenhouse.py  # Greenhouse JSON API
│   │   ├── hireology.py   # Hireology Careers API
│   │   ├── lever.py       # Lever Postings API
│   │   ├── personio.py    # Personio Public XML Feed
│   │   ├── recruitee.py   # Recruitee Careers Site API
│   │   ├── rippling.py    # Rippling ATS Job Board API
│   │   ├── rss.py            # RSS 2.0 feed monitor (SuccessFactors, Teamtailor, generic)
│   │   ├── workday.py     # Workday Job Board API
│   │   ├── sitemap.py     # XML sitemap parser
│   │   ├── nextdata.py    # Next.js __NEXT_DATA__ discovery
│   │   └── dom.py         # Playwright DOM-based discovery
│   ├── scrapers/          # Scraper implementations
│   │   ├── __init__.py    # Registry + JobContent dataclass
│   │   ├── api_sniffer.py # XHR/fetch API capture for single pages
│   │   ├── jsonld.py      # JSON-LD extractor
│   │   ├── nextdata.py    # Next.js data extractor (thin wrapper for embedded)
│   │   ├── embedded.py    # Generalized embedded JSON extractor
│   │   └── dom.py         # Step-based extraction (static or Playwright)
│   ├── description_store.py # R2 upload/diff-track for descriptions + extras
│   ├── enum_normalize.py  # employment_type + job_location_type normalizers
│   ├── location_resolve.py # Location → GeoNames ID resolution
│   ├── monitor.py         # monitor_one() dispatcher
│   └── scrape.py          # scrape_one() dispatcher
├── workspace/             # Workspace CLI (ws command)
│   ├── cli.py             # Click entry point + groups
│   ├── commands/          # Command implementations
│   │   ├── lifecycle.py   # new, reject, del, submit, status, validate, resume
│   │   ├── config.py      # set, add board, del board
│   │   ├── crawl.py       # probe, select/run monitor/scraper, feedback
│   │   └── help.py        # Reference docs for monitors, scrapers, config
│   ├── state.py           # YAML workspace state (v2: named configs)
│   ├── log.py             # Action log + transcript
│   ├── git.py             # Git/GitHub CLI wrappers (retry, error wrapping)
│   ├── errors.py          # Exception hierarchy (WorkspaceError, CsvToolError, GitError)
│   ├── preflight.py       # Pre-flight checks (branch, PR state)
│   ├── filelock.py        # Advisory file locking
│   ├── output.py          # Terminal output helpers
│   ├── artifacts.py       # Debug artifact storage
│   └── url_check.py       # URL validation helpers
├── shared/
│   ├── api_sniff.py       # API sniffing utilities (data classes, scoring, pagination)
│   ├── constants.py       # DATA_DIR, WORKSPACE_DIR, SLUG_RE, URL_RE
│   ├── csv_io.py          # CSV read/write utilities
│   ├── http.py            # httpx client factory
│   ├── logging.py         # structlog config
│   └── slug.py            # slugify utility
├── batch.py               # Batch processor (R2 uploads, enum normalization)
├── scheduler.py           # Scheduler (entry point)
├── scripts/               # One-off migration and utility scripts
│   ├── migrate_descriptions_to_r2.py  # Bulk R2 upload (run before dropping columns)
│   ├── backfill_locations.py          # Backfill location_ids from GeoNames
│   └── seed_geonames.py              # Seed location tables from GeoNames data
├── sync.py                # CSV → DB sync
├── inspect.py             # CSV validation + diagnostic library
├── csvtool.py             # CSV management library
├── db.py                  # asyncpg pool
└── config.py              # pydantic-settings
```

## Commands

```bash
# Install deps
uv sync

# Workspace CLI (alias for convenience)
alias ws='uv run ws'

# Workspace lifecycle — ws new sets the active workspace; slug is omitted after that
ws new <slug> --issue <N>              # Create workspace + branch + draft PR (sets active)
ws use <slug>                          # Switch active workspace (multi-workspace only)
ws set --name "..." --website "..."
ws set --board <alias> --job-link-pattern "<regex>"  # Optional manual job-link filter
ws add board <alias> --url <board-url> [--job-link-pattern "<regex>"]
ws probe monitor -n <N>                # Probe all monitor types (N = job count from website)
ws probe scraper                       # Probe all scraper types against sample URLs
ws probe deep -n <N>                   # Playwright-based api_sniffer detection
ws probe api <url>                     # Analyze API endpoint for api_sniffer config
ws select monitor <type> [--as <name>] # Select monitor (named configs)
ws run monitor                         # Test crawl
ws select scraper <type> [--config JSON] # Select scraper
ws run scraper [--url URL ...]         # Test scrape
ws feedback [<config>] --verdict good  # Record extraction quality (mandatory)
ws select config <name>                # Re-activate a previously tested config
ws reject-config <name> --reason "..." # Mark a config as rejected
ws submit [--summary "..."] [--force]  # Validate, commit, push, post stats

# Utilities
ws validate                            # Validate CSVs
ws status                              # Show active workspace (or list all)
ws resume                              # Diagnose workspace state + suggest next action
ws use --board <alias>                 # Switch active board
ws del                                 # Remove workspace + CSV rows + close PR

# Rejection
ws reject --issue <N> --reason <key> --message "..."
ws reject --reason <key> --message "..."  # Uses active workspace's issue

# Run crawler (poll loop)
uv run scheduler

# Run one batch
uv run scheduler --once

# Sync CSVs to DB
uv run python -m src.sync

# Run tests
uv run pytest tests/
```

## Crawler Setup Agent Instruction Sources

For agents running the guided setup workflow (`ws task --issue ...`), behavior is driven by runtime instruction files:

- Step content: `src/workspace/steps/*.md`
- Workflow sequence and gates: `src/workspace/workflow.yaml`
- `ws help` command text: `src/workspace/commands/help.py`
- Troubleshooting KB used by `ws task troubleshoot`: `src/workspace/kb/*.md`

To change crawler setup agent behavior, edit those files. AGENTS/docs updates alone do not affect the runtime instruction stream.

## Decision Mindset

Use `ws` output as evidence, not as an instruction oracle.

- Prefer reasoning from observations (what was found), method (how it was found), and interpretation (what it likely means).
- Treat auto-detected monitor/scraper suggestions as hypotheses that require verification.
- Prefer directly referenced board evidence over unreferenced slug guesses.
- When signals conflict, explain the conflict and why one signal is stronger.

See [docs/agents.md](../../docs/agents.md) for the full mindset reference.

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

## Scraper Evaluation Guidelines

See [docs/08-job-data-fields.md](../../docs/08-job-data-fields.md) for the complete field reference (types, formats, accepted values, per-ATS source mapping, and `fields` mapping syntax).

When evaluating scraper probe results and extraction output:

- Read "Next:" suggestions as one interpretation of current evidence, not as a required action.
- Check evidence provenance: static HTML, rendered DOM, embedded JSON, API capture can disagree.
- Verify content quality from samples, not only N/N counts.
- For DOM extraction, verify step order in `flat.json` (forward cursor behavior can hide misses).
- Prefer extracting complete upstream data over post-processing partial/garbled text.
- Validate field formats (`locations`, HTML `description`, salary structure, location type values).
- If evidence is ambiguous, capture one more validating signal before choosing a final config.

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
