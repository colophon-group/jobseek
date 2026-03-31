# AGENTS.md — Crawler

Crawler-specific instructions. See the root [AGENTS.md](../../AGENTS.md) for project-wide context.

## Architecture

Redis-orchestrated workers writing to local Postgres, with CDC export to Supabase:

1. **Single Job** (`src/core/`) — pure async functions, no DB awareness
2. **Workers** (`src/workers/pipeline.py`) — claim from Redis tiered queues, process, write to local Postgres
3. **Exporter** (`src/exporter.py`) — CDC: local Postgres -> Supabase batch COPY
4. **R2 Drain** (`src/workers/r2_drain.py`) — poll descriptions table, PUT to R2

See [docs/03-crawler-architecture.md](../../docs/03-crawler-architecture.md) for full details.

## Key Files

```
src/
├── core/
│   ├── monitors/          # Monitor implementations (35+ types)
│   │   ├── __init__.py    # Registry + DiscoveredJob dataclass
│   │   ├── accenture.py   # Accenture Career API (dedicated, auto-partitioned)
│   │   ├── api_sniffer.py # API capture (httpx for public APIs, Playwright for browser-dependent)
│   │   ├── ashby.py       # Ashby Job Board API
│   │   ├── gem.py         # Gem ATS Job Board API
│   │   ├── greenhouse.py  # Greenhouse JSON API
│   │   ├── hireology.py   # Hireology Careers API
│   │   ├── lever.py       # Lever Postings API
│   │   ├── personio.py    # Personio Public XML Feed
│   │   ├── recruitee.py   # Recruitee Careers Site API
│   │   ├── rippling.py    # Rippling ATS Job Board API
│   │   ├── rss.py         # RSS 2.0 feed monitor (SuccessFactors, Teamtailor, generic)
│   │   ├── workday.py     # Workday Job Board API
│   │   ├── sitemap.py     # XML sitemap parser
│   │   ├── nextdata.py    # Next.js __NEXT_DATA__ discovery
│   │   ├── dom.py         # Playwright DOM-based discovery
│   │   └── inline.py      # Single-page inline job extraction (rich)
│   ├── scrapers/          # Scraper implementations
│   │   ├── __init__.py    # Registry + JobContent dataclass
│   │   ├── api_sniffer.py # XHR/fetch API capture for single pages
│   │   ├── jsonld.py      # JSON-LD extractor
│   │   ├── nextdata.py    # Next.js data extractor (thin wrapper for embedded)
│   │   ├── embedded.py    # Generalized embedded JSON extractor
│   │   └── dom.py         # Step-based extraction (static or Playwright)
│   ├── description_store.py # R2 put/get
│   ├── enum_normalize.py  # employment_type + job_location_type normalizers
│   ├── location_resolve.py # Location -> GeoNames ID resolution
│   ├── salary_extract.py  # Heuristic salary parsing from HTML
│   ├── monitor.py         # monitor_one, monitor_one_stream dispatchers
│   └── scrape.py          # scrape_one dispatcher
├── workers/
│   ├── pipeline.py        # Discovery coroutines, claim from Redis, dispatch
│   └── r2_drain.py        # Producer-consumer: descriptions -> R2
├── processing/
│   ├── board.py           # Streaming monitor processing, timestamp gone detection
│   ├── scrape.py          # Single-job scraping, fallback chain
│   ├── cpu.py             # CPU-bound processing (salary, location, tech matching)
│   └── r2_stage.py        # Stage descriptions for R2 upload
├── queries/
│   ├── monitor.py         # SQL: DIFF_BATCH, MARK_GONE_BY_TIMESTAMP, record success/fail
│   ├── scrape.py          # SQL: UPDATE_JOB_CONTENT (conditional updated_at), RECORD_SCRAPE_*
│   └── lookups.py         # Cached lookup table loaders (locations, technologies, etc.)
├── redis_queue.py         # Lua-backed claim/enqueue/reschedule
├── lua/                   # claim_work.lua, enqueue_task.lua, reschedule_task.lua
├── exporter.py            # CDC: local Postgres -> Supabase (job_posting only)
├── sync.py                # CSV -> local Postgres + Supabase + Redis
├── bootstrap.py           # One-time: Supabase -> local Postgres copy
├── cli.py                 # Entry point: crawler run/run-browser/export/drain/sync/board
├── config.py              # Settings (pydantic-settings)
├── db.py                  # asyncpg pools (local Postgres + Supabase)
├── metrics.py             # Prometheus metrics
├── migrations/            # Alembic migrations for local Postgres
├── workspace/             # Workspace CLI (ws command)
│   ├── cli.py             # Click entry point + groups
│   ├── commands/          # Command implementations
│   │   ├── lifecycle.py   # new, reject, del, submit, status, validate, resume
│   │   ├── config.py      # set, add board, del board
│   │   ├── crawl.py       # probe, select/run monitor/scraper, feedback, compare-boards
│   │   ├── task.py        # Workflow: task, troubleshoot, learn, casestudy
│   │   └── help.py        # Reference docs for monitors, scrapers, config
│   ├── state.py           # YAML workspace state (v2: named configs)
│   ├── log.py             # Action log + transcript
│   ├── git.py             # Git/GitHub CLI wrappers (retry, error wrapping)
│   ├── errors.py          # Exception hierarchy
│   ├── preflight.py       # Pre-flight checks (branch, PR state)
│   ├── filelock.py        # Advisory file locking
│   ├── output.py          # Terminal output helpers
│   ├── artifacts.py       # Debug artifact storage
│   └── url_check.py       # URL validation helpers
├── shared/
│   ├── api_sniff.py       # API sniffing utilities (data classes, scoring, pagination)
│   ├── browser.py         # Playwright browser launch (stealth, proxy support)
│   ├── constants.py       # DATA_DIR, WORKSPACE_DIR, SLUG_RE, URL_RE
│   ├── csv_io.py          # CSV read/write utilities
│   ├── http.py            # httpx client factory
│   ├── nextdata.py        # Shared field extraction (extract_field, map, list spec, each+wrap)
│   ├── proxy.py           # Per-domain proxy routing (PROXY_MAP env var)
│   ├── logging.py         # structlog config
│   └── slug.py            # slugify utility
├── inspect.py             # CSV validation + diagnostic library
└── csvtool.py             # CSV management library
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
ws run monitor [--config <name>]        # Test crawl (--config for parallel testing)
ws select scraper <type> [--config JSON] # Select scraper
ws run scraper [--url URL ...] [--config <name>]  # Test scrape
ws feedback [<config>] --verdict good  # Record extraction quality (mandatory)
ws select config <name>                # Re-activate a previously tested config
ws reject-config <name> --reason "..." # Mark a config as rejected
ws compare-boards                      # Detect mirror/subset/overlapping boards
ws submit [--summary "..."] [--force]  # Validate, commit, push, post stats

# Search + discovery
ws search "<query>"                    # Search existing companies by name/slug/website
ws logos                               # Show discovered logo candidates + current selection

# Parallel mode
ws await-board [--exclude ALIAS ...]   # Block until new board appears (for parallel pipeline)
ws boards-done                         # Signal board discovery complete (unblocks await-board)
ws task back --to <step> --reason "..."  # Backtrack to earlier step on new evidence

# Reconfiguration
ws new <slug> --reconfig [--start-at <step>]  # Reconfigure existing company

# Utilities
ws validate                            # Validate CSVs
ws status                              # Show active workspace + discovery status
ws resume                              # Diagnose workspace state + suggest next action
ws use --board <alias>                 # Switch active board
ws del                                 # Remove workspace + CSV rows + close PR

# Rejection
ws reject --issue <N> --reason <key> --message "..."
ws reject --reason <key> --message "..."  # Uses active workspace's issue

# Run crawler workers
uv run crawler run                     # HTTP worker (claims from simple queues)
uv run crawler run-browser             # Browser worker (claims from browser queues)
uv run crawler export                  # CDC exporter loop
uv run crawler drain                   # R2 description uploader
uv run crawler sync                    # CSV -> local Postgres + Supabase + Redis
uv run crawler reconcile               # Compare local vs Supabase, fix discrepancies
uv run crawler board <slug>            # Process single board (debug)
uv run crawler board <slug> --dry-run  # Test without DB writes
uv run crawler board <slug> --dry-run --verbose  # Show all extracted fields

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
   - `rich=True` if the monitor returns full job data (scraper step is skipped)
   - `stream=<async_generator>` for large datasets that yield batches
5. Import in `src/core/monitors/__init__.py`

## Adding a New Scraper Type

1. Create `src/core/scrapers/<name>.py`
2. Implement `async def scrape(url: str, config: dict, http: httpx.AsyncClient) -> JobContent`
3. Register at module bottom: `register("<name>", scrape)`
   - `can_handle=<func>` for auto-detection in `ws probe scraper`
   - `parse_html=<func>` for fast HTML-only extraction (avoids HTTP call in fallback chain)
   - `needs_browser=True` if the scraper requires Playwright
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

## Local Mode (`WS_LOCAL=1`)

Set `WS_LOCAL=1` to run `ws` commands without git/GitHub side effects (no
branches, PRs, pushes, or issue comments). Useful for:

- **Bulk operations** — processing many companies without creating PRs for each
- **Logo discovery** — `ws new <slug> --issue 1 && ws set <slug> --website <url>`
  triggers async logo/enrichment discovery. Check results with `ws logos <slug>`.
- **Testing configs** — iterate on monitor/scraper configs without committing
- **Debugging** — inspect ws behavior locally

Local mode skips: worktree creation, git commit/push, PR creation/update,
issue comments, branch cleanup. Everything else works normally (CSV writes,
logo discovery, monitor probes, scraper tests, validation).

```bash
# Example: bulk logo discovery
export WS_LOCAL=1
for slug in starbucks hsbc bayer; do
  ws new "$slug" --issue 1
  ws set "$slug" --website "https://www.${slug}.com"
done
sleep 15  # wait for async discovery
for slug in starbucks hsbc bayer; do
  ws logos "$slug"
  ws set "$slug" --logo-candidate 1 --icon-candidate 2 --logo-type wordmark --no-discover
done
```

**Note:** `ws new` in local mode still writes stub CSV rows. Clean up with
`git checkout -- data/` if you only needed the discovery artifacts.
`ws del` in local mode also removes CSV rows — use with caution on
companies that already exist.

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

## Hetzner Operations

All crawler services run on Hetzner. Machine IPs, credentials, and API keys are in `apps/crawler/.env.local` — never hardcode them.

### SSH Access

```bash
ssh -i ~/.ssh/hetzner_deploy root@<WORKER_IP>    # Worker machine (Redis, workers, exporter, drain, alloy)
ssh -i ~/.ssh/hetzner_deploy root@<POSTGRES_IP>   # Postgres machine
```

IPs are in `.env.local` (`HETZNER_HOST` for worker, `LOCAL_DATABASE_URL` contains the Postgres IP).

### Container Management

All containers run with `--network host` and `--restart unless-stopped`:

```bash
# List running containers
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.CPUPerc}}\t{{.MemUsage}}"

# View logs
docker logs <name> 2>&1 | tail -20
docker logs <name> 2>&1 | grep "error" | tail -10

# Restart a service
docker rm -f <name> && docker run -d --name <name> --restart unless-stopped \
  --env-file /home/deploy/.env --network host --memory=1g --cpus=1.0 \
  -e METRICS_PORT=<port> -e DISCOVERY_CONCURRENCY=30 -e MONITOR_CONCURRENCY=10 \
  crawler-slim:latest uv run --no-sync crawler run

# Build images after code changes
rsync -az --delete --exclude='.venv' --exclude='__pycache__' --exclude='.env*' --exclude='*.pyc' \
  -e "ssh -i ~/.ssh/hetzner_deploy" apps/crawler/ root@<WORKER_IP>:/home/deploy/crawler-src/
ssh ... 'cd /home/deploy/crawler-src && docker build --target slim -t crawler-slim:latest .'
ssh ... 'cd /home/deploy/crawler-src && docker build --target full -t crawler-full:latest .'
```

### Current Container Layout (metrics ports)

| Container | Image | Metrics Port | CPU | Memory |
|-----------|-------|-------------|-----|--------|
| worker-1 | crawler-slim | 9095 | 1 | 1GB |
| worker-2 | crawler-slim | 9096 | 1 | 1GB |
| worker-3 | crawler-slim | 9097 | 1 | 1GB |
| browser-1 | crawler-full | 9098 | 3 | 4GB |
| exporter | crawler-slim | 9093 | — | — |
| drain | crawler-slim | 9094 | — | — |
| alloy | grafana/alloy | 12346 | 0.25 | 256MB |
| redis | redis:7-alpine | — | — | 320MB |

### Querying Metrics

```bash
# Prometheus metrics from any container
curl -s http://localhost:<port>/metrics | grep "crawler_"

# Redis queue state
redis-cli ZCARD ready:simple:0   # first-time domains
redis-cli ZCARD ready:simple:1   # monitor domains
redis-cli ZCARD ready:simple:2   # scrape domains

# Local Postgres (via docker exec on Postgres machine)
ssh ... root@<POSTGRES_IP> "docker exec -i postgres psql -U crawler -d crawler -c '<SQL>'"
```

### Grafana Dashboard

Dashboard is managed as JSON at `apps/crawler/grafana-dashboard.json` and pushed via the Grafana HTTP API.

```bash
# Push dashboard update
curl -s -X POST "https://colophongroup.grafana.net/api/dashboards/db" \
  -H "Authorization: Bearer <GRAFANA_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"dashboard": <json>, "overwrite": true}'
```

`GRAFANA_API_KEY` is in `.env.local`. The dashboard UID is `jobseek-crawler-pipeline`.

### Alloy (Metrics + Logs Collector)

Alloy scrapes Prometheus metrics from all containers and ships to Grafana Cloud. Config is written to `/tmp/alloy-full.river` on the worker machine. When adding/removing containers, update the static scrape targets and restart alloy.

Credentials for Grafana Cloud Prometheus and Loki are in `.env.local` (`GRAFANA_*` vars). Note: Prometheus and Loki have **different user IDs** (Prometheus = `GRAFANA_USER_ID`, Loki has its own instance ID).

### Deploying Code Changes

```bash
# 1. Rsync from local to Hetzner
rsync -az --delete --exclude='.venv' --exclude='__pycache__' --exclude='.env*' \
  -e "ssh -i ~/.ssh/hetzner_deploy" apps/crawler/ root@<WORKER_IP>:/home/deploy/crawler-src/

# 2. Build image(s)
ssh ... 'cd /home/deploy/crawler-src && docker build --target slim -t crawler-slim:latest .'

# 3. Restart affected containers (worker, exporter, drain, etc.)
ssh ... 'docker rm -f worker-1 && docker run -d --name worker-1 ...'

# 4. Re-sync if CSV data changed
ssh ... 'docker run --rm --env-file /home/deploy/.env --network host \
  crawler-slim:latest uv run --no-sync crawler sync'
```

## Code Conventions

- `from __future__ import annotations` in every module
- Async everywhere — `asyncpg`, `httpx.AsyncClient`
- Structured logging: `log = structlog.get_logger()`, then `log.info("event.name", key=value)`
- SQL in raw strings (no ORM), using `$1` positional params for asyncpg
- Concurrency: `asyncio.TaskGroup` for parallel work, Redis Lua scripts for atomic claiming
- Error handling: exponential backoff on failures (interval doubles, capped at 24h, auto-disable at 5 consecutive failures)
