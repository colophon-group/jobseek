# AGENTS.md — Crawler

Crawler-specific instructions. See the root [AGENTS.md](../../AGENTS.md) for project-wide context.

## Architecture

Redis-orchestrated workers writing to local Postgres, with CDC export to Supabase + Typesense:

1. **Single Job** (`src/core/`) — pure async functions, no DB awareness
2. **Workers** (`src/workers/pipeline.py`) — claim from Redis tiered queues, process, write to local Postgres
3. **Exporter** (`src/exporter.py`) — CDC: local Postgres -> Supabase batch COPY + Typesense upserts (two independent cursors, concurrent writes)
4. **R2 Drain** (`src/workers/r2_drain.py`) — poll descriptions table, PUT to R2
5. **Typesense Sync** (`src/sync.py`) — taxonomy + company collections populated after CSV sync; rename detection updates denormalized names on postings

See [docs/03-crawler-architecture.md](../../docs/03-crawler-architecture.md) for full details.
See [docs/11-typesense.md](../../docs/11-typesense.md) for Typesense deployment details.

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
├── exporter.py            # CDC: local Postgres -> Supabase + Typesense (two-cursor)
├── typesense_client.py    # Shared Typesense client (lazy init, None when unconfigured)
├── sync.py                # CSV -> local Postgres + Supabase + Redis + Typesense taxonomies
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
│   ├── proxy.py           # Provider-agnostic proxy layer (see Proxy-routed transport)
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
uv run crawler export                  # CDC exporter loop (Supabase + Typesense)
uv run crawler drain                   # R2 description uploader
uv run crawler sync                    # CSV -> local Postgres + Supabase + Redis + Typesense
uv run crawler reconcile               # Compare local vs Supabase, fix discrepancies (also runs daily in-process inside the exporter container)
uv run crawler backfill-typesense      # Full re-index of job_posting to Typesense (manual; workflow_dispatch in .github/workflows/crawler-scheduled-maintenance.yml)
uv run crawler refresh-typesense       # Refresh Typesense counts + reconcile watchlists (every 4h via .github/workflows/crawler-scheduled-maintenance.yml, plus inline at every deploy/CSV sync)
uv run crawler notify-indexnow         # Push changed company URLs to IndexNow (RETIRED in #2821 — kept for revival; no scheduler invokes it)
uv run crawler retry-stalled-scrapes   # Reset next_scrape_at for transient-3-strike-stalled postings (#2738; see docs/03-crawler-architecture.md "Delisting model" section 5)
uv run crawler retry-stalled-scrapes --dry-run  # Report the count without writing
uv run crawler reprocess-experience --dry-run   # Report active postings whose stored descriptions would update experience_min/max (#3289)
uv run crawler reprocess-experience             # Apply the #3289 experience_min/max correction locally; exporter propagates changes
uv run crawler reprocess-occupations --dry-run  # Report occupation_id changes after taxonomy splits (#3360)
uv run crawler reprocess-occupations --live     # Apply the #3360 occupation_id correction locally; exporter propagates changes
uv run crawler board <slug>            # Process single board (debug)
uv run crawler board <slug> --dry-run  # Test without DB writes
uv run crawler board <slug> --dry-run --verbose  # Show all extracted fields
uv run crawler board <slug> --pcsx-full-crawl  # Force full PCSX crawl for
                                                # eightfold boards, bypassing
                                                # the incremental watermark.
                                                # Used for manual backfills of
                                                # very large boards (Starbucks).

# Run tests
uv run pytest tests/
```

## Proxy-routed transport

Some hosts (e.g. `apply.starbucks.com`, `citi.eightfold.ai`) block
Hetzner datacenter IPs with AWS WAF captcha pages. The crawler routes
httpx requests and Playwright launches for those boards through an
external HTTP proxy. Implementation: `src/shared/proxy.py` — a
`ProxyProvider` Protocol with a single `StaticProxyProvider` impl
covering Webshare / Decodo / any `http://user:pass@host:port` service.

A board opts in by setting `"proxy": true` inside `monitor_config`
and/or `scraper_config` JSON in `data/boards.csv` — same place as
`render`, `skip_ssl`, `rescrape_policy`. The two flags are independent;
typically both are set for a WAF-blocked host.

```csv
starbucks,starbucks-eightfold,https://starbucks.eightfold.ai/careers,eightfold,"{""url_filter"": ""/careers/job/"", ""proxy"": true}",eightfold,"{""enrich"": [""description""], ""proxy"": true}"
```

Active provider is chosen by env:

```bash
PROXY_PROVIDER=webshare         # none | webshare | decodo
WEBSHARE_PROXY_URL=http://user:pass@192.53.69.78:6716
DECODO_PROXY_URL=http://user:pass@isp.decodo.com:10001
```

`PROXY_PROVIDER=none` (and missing/empty URLs) are fail-safe — boards
with `proxy: true` fall back to direct egress, which means captcha on
WAF'd hosts but nothing crashes. The provider logs
`proxy.provider.missing_url` at ERROR when a selected provider has an
empty URL — first thing to check when a `proxy: true` board starts
returning captcha.

### Billing model — per IP, flat monthly (NOT per request)

**Current providers (Webshare, Decodo) are billed per static IP, flat
monthly. Per-request volume does not affect the bill.** One leased IP
costs the same at 10 req/day and 10 000 req/day. Cost scales with IPs
leased, not traffic volume.

This is the opposite of the prior Lightpanda CDP transport (removed in
PR #2181), which was billed by browser-hours of session clock time, so
request volume directly mattered. Older mentions of "cost" and
"bandwidth isn't free" in commit messages and docs are leftovers from
that era — they **do not** apply to the Webshare/Decodo setup.

A single IP covers the current WAF-blocked set; add IPs (and a
rotating/failover provider impl) only when an origin bans the IP or a
provider hits concurrency caps.

Adding a new provider or a new WAF-blocked host is covered in the
commit that introduced this section (PR #2181) — the PR body has the
step-by-step and rollback runbook.

### Disabling re-scrapes on paid-proxy boards

> This is **not** a per-request cost saver — see the billing note
> above. The proxy provider does not bill per request.

Set `monitor_config.rescrape_policy = "never"` in `data/boards.csv`
for WAF-blocked boards whose content rarely changes. The reasons are:

1. **Concurrency budget.** Webshare static IPs allow a limited number
   of concurrent connections per IP. Each needless re-scrape holds a
   connection slot that another board could use. A board with
   thousands of postings (Starbucks ~21k, Uber ~1k) will saturate the
   slot budget at the 24h refresh cadence without contributing new
   information.
2. **Origin good-neighborliness.** The proxy exit IP hitting the
   origin at high volume for stale data is what gets the IP blocked
   by the origin's WAF, forcing us to lease a new one.
3. **Future-proofing.** If we ever swap to a bandwidth-metered
   provider (e.g. Decodo's rotating/BW plans, which are NOT what we
   run today), this flag is already in the right place.

Mechanics: `_RECORD_SCRAPE_SUCCESS` sets `next_scrape_at = NULL` after
each successful scrape when the flag is set. The first scrape still
runs (so descriptions are filled when a posting is first discovered),
and relisted jobs still re-scrape once (because
`_enqueue_scrapes_for_relisted` directly sets `next_scrape_at =
now()`); only the periodic refresh tail is suppressed.

```csv
starbucks,starbucks-eightfold,https://starbucks.eightfold.ai/careers,eightfold,"{""url_filter"":""/careers/job/"",""rescrape_policy"":""never""}",json-ld,"{""enrich"":[""description""]}"
```

`inspect.validate_csvs()` rejects unknown values (only `"never"` is
supported today).

## Eightfold hybrid monitor (sitemap + PCSX incremental)

The `eightfold` monitor runs in a hybrid mode for PCSX-enabled tenants: it
fetches the sitemap for the canonical URL set (gone detection works
unchanged) **and** paginates the Eightfold PCSX API (`/api/pcsx/search`)
incrementally via a high-water mark on `postedTs`. See `ws help monitor
eightfold` for the full reference.

### Watermark state

Stored as `job_board.metadata.pcsx_watermark` (runtime-written — preserved
across `crawler sync` by `_UPSERT_BOARD_LOCAL`'s JSONB merge):

```json
{
  "max_ts": 1775606400,
  "last_full_at": "2026-04-08T23:00:00+00:00",
  "last_incremental_at": "2026-04-09T09:00:00+00:00",
  "interval_days": 7,
  "enabled": true,
  "auto_full_crawl": true,
  "extra": {"host": "careers.kering.com", "domain": "kering"}
}
```

- `max_ts` — drives incremental stop (paginate until all items on a page
  have `postedTs <= max_ts`, then 3 safety pages for boundary jitter)
- `last_full_at` / `interval_days` — weekly forced full crawl for drift
  correction (content changes on existing jobs via json-ld enrichment)
- `enabled` — cached result of the `/api/pcsx/search` probe. `false` for
  the 7 tenants that return `"PCSX is not enabled for this user."`
- `auto_full_crawl` — if `false`, skip the automatic full crawl on first
  run. Used for boards too large to crawl inside the scheduled worker pool

### Manual backfill for very large boards

Starbucks (~21k jobs) has `monitor_config.pcsx_watermark.auto_full_crawl:
false` in `data/boards.csv` so scheduled runs don't start a 30-60 minute
full crawl. Operator runs the backfill manually from the Hetzner box:

```bash
ssh -i ~/.ssh/hetzner_deploy root@<WORKER_IP>
docker exec crawler-slim uv run crawler board starbucks-eightfold --pcsx-full-crawl
```

After success, the watermark is populated and subsequent scheduled runs
do fast incremental top-ups (~30-60 seconds). The 7 PCSX-disabled boards
(bayer, american-express, hsbc, stmicroelectronics, symetra, vale, zebra)
stay on the sitemap-only path automatically — their probes fail and
`enabled=false` gets cached.

### CSV config for PCSX-enabled eightfold boards

Each PCSX-enabled eightfold board needs `scraper_config: {"enrich":
["description"]}` in `data/boards.csv` so the pipeline runs a one-shot
json-ld scrape per new job to fill descriptions (PCSX doesn't return
them). 15 boards migrated: citigroup, dexcom, eaton, hasbro, kering,
lam-research, mercado-libre, micron, microsoft, northrop-grumman, ptc,
qualcomm, starbucks, tailored-brands, vodafone.

### Rollback paths

1. Per-board kill switch — `metadata.pcsx_watermark.enabled = false` via SQL
2. Disable auto-full-crawl — `metadata.pcsx_watermark.auto_full_crawl = false`
3. CSV revert — remove `scraper_config: {enrich: [description]}` and sync
4. Full git revert — safe; the `board.py` partial-rich fix is a strict
   superset of pre-refactor behaviour

## Crawler Setup Agent Instruction Sources

For agents running the guided setup workflow (`ws task --issue ...`), behavior is driven by runtime instruction files:

- Step content: `src/workspace/steps/*.md`
- Workflow sequence and gates: `src/workspace/workflow.yaml`
- `ws help` command text: `src/workspace/commands/help.py`
- Troubleshooting KB used by `ws task troubleshoot`: `src/workspace/kb/*.md`

To change crawler setup agent behavior, edit those files. AGENTS/docs updates alone do not affect the runtime instruction stream.

Codex is the preferred new automation surface. Use repo skills from
`.agents/skills` when present, the Hetzner local Codex runner for recurring
scheduled routines, and `codex exec --json` for traceable noninteractive
fallback.
Claude-compatible prompts may remain as alternate paths, but do not describe
GitHub Actions automation execution as ChatGPT subscription billed and do not
re-add GitHub Actions that run automations now owned by the Hetzner runner.
GitHub Actions may still deploy the Hetzner runner host surface.

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

See [docs/16-hetzner-maintenance.md](../../docs/16-hetzner-maintenance.md)
for disk triage, Docker image garbage collection, Redis disk-full recovery,
and resize procedures.

### SSH Access

```bash
ssh -i ~/.ssh/hetzner_deploy root@<WORKER_IP>      # Worker machine (Redis, workers, exporter, drain, alloy)
ssh -i ~/.ssh/hetzner_deploy root@<POSTGRES_IP>     # Postgres machine
ssh -i ~/.ssh/hetzner_deploy root@<TYPESENSE_IP>    # Typesense machine
```

IPs are in `.env.local` (`HETZNER_HOST` for worker, `LOCAL_DATABASE_URL` contains the Postgres IP, `TYPESENSE_HOST` for Typesense).

### Private Network Layout

All machines communicate via Hetzner private network (10.0.0.0/16). See `.env.local` for actual IPs.

| Machine | Role |
|---------|------|
| Crawler box | Workers, exporter, drain, Redis, Alloy |
| Postgres box | Local Postgres (source of truth) |
| Typesense box | Typesense 27.1, Cloudflare tunnel (`cloudflared`) |

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

### Disk and Docker GC

All Hetzner hosts should run the `jobseek-docker-gc.timer` systemd timer.
It prunes stale Docker builder cache and unused images, and on the crawler
host it keeps the active plus recent rollback crawler/browser release images
while removing older unused version tags. Do not prune Docker volumes.

```bash
systemctl is-active jobseek-docker-gc.timer
systemctl list-timers --all jobseek-docker-gc.timer --no-pager
journalctl -u jobseek-docker-gc.service -n 80 --no-pager
df -h /
docker system df
```

If Redis reports `MISCONF` after a disk-full event, free disk first, then
verify Redis persistence and writes:

```bash
docker exec deploy-redis-1 redis-cli INFO persistence | tr -d '\r' \
  | grep -E '^(rdb_bgsave_in_progress|rdb_last_bgsave_status|aof_enabled):'
docker exec deploy-redis-1 redis-cli SET disk_probe ok EX 60
```

### Current Container Layout (metrics ports)

| Container | Image | Metrics Port | CPU | Memory |
|-----------|-------|-------------|-----|--------|
| worker-1 | crawler-slim | 9095 | 1 | 1GB |
| worker-2 | crawler-slim | 9096 | 1 | 1GB |
| worker-3 | crawler-slim | 9097 | 1 | 1GB |
| browser-1 | crawler-full | 9098 | 3 | 6GB |
| exporter | crawler-slim | 9093 | — | — |
| drain | crawler-slim | 9094 | — | — |
| alloy | grafana/alloy | 12346 | 0.25 | 256MB |
| redis | redis:7-alpine | — | — | 1.5GB (1GB maxmemory) |

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

### Alert Rules

Alert rules are managed as Prometheus YAML at `apps/crawler/alerts.yaml`
and pushed to Grafana Cloud Mimir. The current set covers the failure
modes from the 2026-04-25 dark-window incident (#2696):
`NoMetricsFromCrawler`, `DiskNearFull`, `RedisMemoryPressure`,
`ExporterStale`, `TaskFailureRateHigh`; plus the 2026-04-26 false-delisting
incident (#2722–#2726): `DelistingRateSpike` (page; fleet gone-rate
>3× rolling 7d median) and `GoneDetectionGuardsFiring` (email; resilience
guards #2723/#2724 actively suppressing mass delistings — investigate
the underlying monitor truncation). Severity is encoded as a label
(`severity=page` vs `severity=email`); routing is configured in Grafana
Cloud Alerting separately.

```bash
# Preferred: push via mimirtool
mimirtool rules load apps/crawler/alerts.yaml \
  --address="$MIMIR_URL" \
  --id="$MIMIR_TENANT" \
  --key="$MIMIR_KEY"

# Fallback: import via the Grafana Cloud Alerting UI's
# "Import from Prometheus YAML" flow.
```

The `RedisMemoryPressure` alert depends on the `redis_exporter` block
in `apps/crawler/alloy.river`. Removing or moving it will silently
disable the alert, since `redis_memory_max_bytes` will stop being
ingested.

### Typesense Operations

Typesense 27.1 runs as a Docker container on a dedicated Hetzner CX22 (4 GB RAM, 2 vCPU). Data stored at `/mnt/typesense-data`. The container runs with `--network host`.

```bash
# SSH to Typesense machine
ssh -i ~/.ssh/hetzner_deploy root@<TYPESENSE_IP>

# Check health
curl -s http://localhost:8108/health -H "X-TYPESENSE-API-KEY: <ADMIN_KEY>"

# View stats
curl -s http://localhost:8108/stats.json -H "X-TYPESENSE-API-KEY: <ADMIN_KEY>"

# View container
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.MemUsage}}"
docker logs typesense 2>&1 | tail -20

# Restart Typesense
docker rm -f typesense && docker run -d --name typesense --restart unless-stopped \
  --network host -v /mnt/typesense-data:/data \
  typesense/typesense:27.1 --data-dir /data --api-key=<ADMIN_KEY>
```

**Cloudflare tunnel**: `cloudflared` runs as a systemd service, routing `typesense.colophon-group.org` to `localhost:8108`. Auto-starts on reboot.

```bash
# Check tunnel status
systemctl status cloudflared

# Restart tunnel
systemctl restart cloudflared
```

**Collection management** (from `apps/crawler/` on any machine with connectivity):

```bash
# Create/recreate collections + aliases
uv run python ../../scripts/typesense-setup.py [--force]

# Full re-index
uv run crawler backfill-typesense

# Refresh counts + reconcile watchlists
uv run crawler refresh-typesense
```

**Grafana metrics**: `typesense_export_docs_total`, `typesense_export_lag`, `typesense_export_duration_seconds`, `typesense_healthy` (0/1), `typesense_memory_bytes`, `typesense_reconciliation_discrepancies`.

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
