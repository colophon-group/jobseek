# Crawler Architecture

The crawler uses Redis-orchestrated workers writing to local Postgres, with CDC export to Supabase.

## Infrastructure

```
                                +-----------+
                                | Supabase  |
                                | (user DB) |
                                +-----^-----+
                                      | batch COPY (write-only)
      +-------------------------------+-------------------------------+
      |  Hetzner (116.203.192.19)     |                               |
      |                    +----------+-----------+                    |
      |  +---------+      |     Exporter         |   +------------+  |
      |  |  Redis  |<-----|  (CDC on updated_at)  |-->| Local      |  |
      |  | (queues)|      +----------------------+   | Postgres   |  |
      |  +---------+             ^                    | (crawler   |  |
      |       |                  |                    |  DB)       |  |
      |       |                  | read/write          +-----^-----+  |
      |  +----v-----------------+---+                       |         |
      |  |  Worker instances (x3)   |  write directly       |         |
      |  |  claim from Redis queues |<----------------------+         |
      |  +-------------------------+                                  |
      |  +--------------------------+                                 |
      |  |  Browser instance (x1)   |  (same pipeline, Chromium)     |
      |  +--------------------------+                                 |
      |  +--------------+                                             |
      |  |  R2 Drain    |-- poll descriptions WHERE NOT r2_uploaded   |
      |  +--------------+     --> PUT to R2                           |
      |  +--------------+                                             |
      |  |  Alloy       |-- metrics (Prometheus) + logs (Loki)        |
      |  +--------------+     --> Grafana Cloud                       |
      +---------------------------------------------------------------+

      Dedicated Postgres machine (178.104.102.63):
        Postgres 16, 20GB XFS volume, source of truth

      CLI: crawler {run|run-browser|export|drain|sync|reconcile|board}
```

## Redis Tiered Ready Queues

Work distribution uses 6 tiered ready queues, managed by 3 Lua scripts for atomic operations:

```
ready:simple:0    -- Tier 0: first-time work (never crawled)
ready:simple:1    -- Tier 1: monitors (re-checks)
ready:simple:2    -- Tier 2: scrapes (re-scrapes)

ready:browser:0   -- Tier 0: first-time browser work
ready:browser:1   -- Tier 1: browser monitors
ready:browser:2   -- Tier 2: browser scrapes
```

**Lua scripts** (`src/lua/`):
- `claim_work.lua` -- atomically pop from the highest-priority queue with a due item
- `enqueue_task.lua` -- add a task to the correct tier with a scheduled time
- `reschedule_task.lua` -- re-enqueue after processing with the next check time

**Per-domain rate limiting** via `ratelimit:{domain}` keys prevents hammering shared ATS APIs (e.g. all Greenhouse boards share `boards-api.greenhouse.io`).

## Worker Pipeline

All workers use the same internal pattern. HTTP workers claim from `ready:simple:*` queues, browser workers from `ready:browser:*`.

### Monitor Processing (streaming path)

All monitors now use `_process_one_board_streaming()`:

1. Claim board task from Redis
2. Run `monitor_one()` -- returns discovered jobs
3. Stream results through processing in batches
4. Diff against local Postgres (`DIFF_BATCH` SQL)
5. Insert new jobs / update touched / mark gone (timestamp-based gone detection)
6. Enqueue scrapes to Redis for URL-only monitors
7. Stage descriptions for R2 upload
8. Record success/failure, reschedule board in Redis

**Monitor semaphore** caps concurrent monitors to bound memory usage.

**Timestamp-based gone detection** replaces the old URL accumulation approach -- jobs are marked gone based on `last_seen_at` timestamps rather than building up a complete URL set.

**Conditional `updated_at`** -- the `updated_at` column only bumps when content actually changes, preventing unnecessary CDC exports.

### Scrape Processing

1. Claim scrape task from Redis
2. Load board config (scraper type, config)
3. Run `scrape_one()` with fallback chain if configured
4. CPU processing: salary extraction, location resolution, technology matching
5. Update `job_posting` on local Postgres (`UPDATE_JOB_CONTENT` with conditional `updated_at`)
6. Stage description for R2 upload
7. Record success/failure, reschedule in Redis

## Delisting model — when is a posting "gone"?

`job_posting.is_active = true` is the primary user-visible signal: the web app, search, and watchlists all filter on it. Tombstoning correctly is therefore a correctness invariant — a posting that's been removed upstream must flip to `is_active = false` within bounded time, and a posting that's still live must not.

The crawler has **two authorities** for that decision, and they answer the question from different angles:

### 1. Monitor authority (primary) — `_MARK_GONE_BY_TIMESTAMP`

For every board cycle, the monitor produces the current per-board URL set (sitemap parse, API page, rendered DOM). A posting that was previously discovered but does not appear in the latest cycle bumps `missing_count`; once the count crosses the per-monitor threshold (1 for authoritative monitors like APIs / sitemaps; 2 for fragile DOM-based monitors that occasionally render partials), `_MARK_GONE_BY_TIMESTAMP` flips `is_active = false`.

This path implements the natural model: *"the listing is the source of truth — if the upstream listing stops mentioning the URL, the posting is gone."* It works whenever monitor and listing agree.

### 2. Scrape authority (fallback) — three failure classes

Some upstream platforms violate the natural model. Avature's US Deloitte tenant is the documented case (issue #2708): the `apply.deloitte.com` SearchJobs SPA continues to list JobDetail URLs that the application then refuses to serve at the per-posting endpoint, returning 403 with a real Avature error page body. The monitor sees "URL still listed → not gone"; the scraper sees "403 → not fetchable". Without a fallback, the posting stays `is_active = true` forever — a dead link in the web app — because the monitor's signal never trips the delist threshold.

The scrape side classifies every failure into one of three buckets, decided by `_is_permanent_gone` and `_is_budget_eligible_failure` in `processing/scrape.py`:

- **`permanent_gone`** (HTTP 404 / 410) — `_RECORD_SCRAPE_FAILURE` with `permanent_gone=True`: tombstone IMMEDIATELY on the first failure. RFC-defined "this resource is gone" semantics. Universal across every host.
- **`budget_eligible`** (HTTP 4xx other than 401, 403, 429) — `_RECORD_SCRAPE_FAILURE` with `permanent_gone=False`: counts toward the 3-failure tombstone budget (≈90 min wall-clock with the 30/60-min doubling backoff). Catches platforms that use 400 / 422 / 405 for an archived posting.
- **`transient`** (HTTP 5xx, 401, 403, 429, network timeouts, connect errors, and successful HTTP fetch with empty extraction) — `_RECORD_SCRAPE_TRANSIENT`: backs off using the same doubling-then-stop math, but **never** flips `is_active`. The monitor authority remains the only delisting decision-maker for these failures.

The transient bucket exists as a deliberate safety choice. The first iteration of #2708's fix made every 3rd consecutive failure tombstone, regardless of failure shape. Cold-read critics surfaced two false-positive risks: a 2-hour upstream 5xx incident would mass-tombstone live postings cohort-wide, and a regex break in an extraction config would tombstone every posting on the affected board. The transient bucket protects against both.

The cost of the safety: 403 from Avature/similar archived-posting platforms is in the transient bucket and is **not** auto-tombstoned by the scrape side. Those URLs stay orphaned until the monitor authority delists them (Avature's SearchJobs SPA eventually drops archived URLs from the listing) or until an operator runs a one-shot cleanup. We accept this trade-off rather than tombstoning live postings during transient WAF challenges, where Cloudflare / Datadome / Akamai also return 403.

A host allowlist for the Avature 403 pattern was rejected (closed PR #2720) as overfitting: it rots fast as new platforms appear with the same quirk. The classification is universal — no host names anywhere in the decision logic.

### 3. Recovery — relisted path

Either authority's tombstone is reversible. The monitor's discovery query (`queries/monitor.py` — the `relisted` CTE) flips `is_active = true`, resets `missing_count = 0`, AND resets `scrape_failures = 0` when a previously-tombstoned URL reappears in a fresh monitor cycle. So a transient 3-failure cluster on a still-live URL self-heals on the next monitor pass; only URLs that the monitor *also* doesn't re-list stay tombstoned.

The `scrape_failures` reset is load-bearing: without it, a relisted posting comes back with `scrape_failures = 3`, and the very next failed scrape would re-tombstone it via the budget condition — a flap loop on chronically slow upstreams.

### 4. Known recovery gap — cross-tenant URLs

When the same source URL is owned by board A but also discovered as a `foreign_touched` URL under board B (cross-tenant duplication — ByteDance/TikTok share a careers host; Glencore reaches GCAA's Workday tenant), only `last_seen_at` is refreshed on the foreign-board match (`queries/monitor.py` — `foreign_touched` CTE). `is_active` is never flipped back. A scrape-tombstoned posting on board A whose URL the monitor only finds via board B will stay tombstoned even though the URL is still listed somewhere. This is a pre-existing recovery gap made more visible by the scrape-side authority — flagged as a known limitation rather than a regression.

### 5. Known recovery gap — transient 3-strike on permanently-listed URLs

The transient class backs off via `next_scrape_at = NULL` after 3 consecutive failures, mirroring the budget path. The worker self-heal (`_process_scrape_work`) honours `next_scrape_at = NULL` and stops re-firing. Recovery is only via the monitor's `relisted` CTE — which fires only when a URL re-appears after dropping out of the listing.

For a posting that stays continuously listed (the upstream listing keeps citing it) but happens to hit 3 transient failures in a row (e.g. a 90-minute upstream 5xx incident hits a posting whose backoff schedule lined up with the outage window), the URL stays in the listing throughout, the `relisted` branch never fires, and the posting is permanently un-rescrapable until either a `crawler sync` re-imports the row or an operator runs `crawler retry-stalled-scrapes` (`apps/crawler/src/retry_stalled.py`, added in #2738). `crawler backfill-locations` covers a different scope — it targets postings missing `location_ids` regardless of `scrape_failures`, and the predicate `description_r2_hash IS NOT NULL` excludes 3-strike postings that never had a successful scrape; use the dedicated CLI for transient-3-strike recovery.

The data already in Postgres (last successful scrape) stays visible to web users, and `is_active` is preserved — so the failure mode is "stale content for this posting" rather than "dead link".

```bash
# Default: target postings stuck > 7 days
uv run crawler retry-stalled-scrapes

# Custom age cutoff
uv run crawler retry-stalled-scrapes --max-age-days 14

# Dry run — report the count without writing
uv run crawler retry-stalled-scrapes --dry-run
```

The query targets `is_active = true AND next_scrape_at IS NULL AND scrape_failures >= 3 AND last_scraped_at < now() - <N>d` — transient-3-strike specifically. Postings on `rescrape_policy = "never"` boards (Starbucks, Uber, paid-proxy boards) also have `next_scrape_at IS NULL` after a successful scrape, but their `scrape_failures = 0`, so they're not affected.

### Why dual authority

Either authority alone is wrong:

- **Monitor-only**: leaks dead links forever when the upstream listing lies (the Avature case).
- **Scrape-only**: too aggressive — a transient 3-failure cluster on a live URL would tombstone it without the monitor's "I just saw it again" recovery signal.

Combining them gives bounded delisting latency in both cases — at most one full monitor cycle for the natural model, at most ≈90 min of scrape budget for the inconsistent-upstream case — with relisted-path recovery as the safety net.

The asymmetry is deliberate: the monitor cycle is the *fast* path (minutes) when the listing is honest; the scrape budget is the *slow but eventual* path (~90 min) when it isn't. We trust the monitor first because per-cycle URL sets are cheaper than 3 per-URL retries; the budget exists because we can't trust every monitor to be authoritative about every URL.

## TDM-Reservation respect

Every fetch helper inspects upstream responses for the W3C [TDM Reservation Protocol](https://www.w3.org/TR/tdmrep/) opt-out signal before treating a body as crawlable content. Two channels are checked, in canonical-precedence order: the `tdm-reservation` HTTP response header (canonical, integer `1` = opt-out), and — when the header is absent — an `<meta name="tdm-reservation" content="1">` tag in the HTML body excerpt. Header `0` (explicit opt-in) takes precedence over any body meta. Non-integer values (`"true"`, `"yes"`, multi-value lists) are treated as absent, per the spec's "implementation-defined" clause for non-conformant values; we choose lenient over strict because false positives cost real boards while the spec gives us no obligation either way.

Implementation lives in `apps/crawler/src/shared/tdm.py` (#2842). The helper `check_response(resp, body_excerpt=...)` is invoked by every shared and per-monitor fetch retry helper after a 200 status check: `fetch_with_retry` (http_retry.py), `_fetch_via_page` (dom-browser path with a Playwright-shaped `check_browser_response`), the per-monitor `_post_page_with_retry` / `_get_page_with_retry` in workday/lever/smartrecruiters/hireology/umantis, the api-sniffer `http_fetch_with_retry`, the PCSX `_fetch_page`, and the shared `make_http_fetcher` wrapper used by api_sniff and accenture. A positive signal raises `TDMReservedError`, which is **not** retried (publisher policy is not transient) and is caught at the monitor wrapper in `_process_one_board_streaming`. The wrapper logs `batch.monitor.tdm_reserved` (with `url`, `source` ∈ {`header`, `meta`}, `tdm_policy_url` for the optional companion `tdm-policy` header), increments `crawler_monitor_skipped_tdm_total{board_id, source}`, and returns success-shaped — no `_RECORD_FAILURE` ramp, no `_MARK_GONE_BY_TIMESTAMP` tombstoning, no `consecutive_failures` bump. The board is treated as a clean skip so that an opted-out publisher doesn't cascade into the 5-strike disable path.

Empirically (#2842 blast-radius probe, 2026-05-09): 0 of 4709 active boards / 0 of 881 distinct origins emit the signal today, including all major ATS (greenhouse, ashby, lever, workday, smartrecruiters). The check exists ahead of any emergence — a single `boards.greenhouse.io` TDM declaration would skip thousands of boards at once, and we'd rather honor it on day one than after the next deploy. No feature flag: enforce-direct.

## R2 Drain

Producer-consumer pipeline polling `descriptions WHERE NOT r2_uploaded`:

1. Producer claims rows atomically (`UPDATE ... SET r2_uploaded = NULL ... RETURNING`)
2. Consumers PUT `latest.html` to Cloudflare R2
3. On success: mark `r2_uploaded = true`, update `description_r2_hash`
4. On failure: revert to `false` for retry

No Redis stream needed -- the `descriptions` table in local Postgres serves as the durable queue.

## Exporter (CDC)

Single process exporting changed rows from local Postgres to Supabase:

```
Every 1-2 seconds:
1. SELECT * FROM job_posting WHERE updated_at >= $last_export_ts LIMIT 2000
2. Batch COPY changed rows to Supabase via temp table + ON CONFLICT upsert
3. Update last_export_ts (persisted in exporter_state table)

Daily:
4. Reconciliation: compare local vs Supabase, re-export discrepancies
```

The exporter is the **only component that writes to Supabase**. Workers never touch Supabase directly.

## Data Flow

```
CSV -> sync.py -> Local Postgres + Redis queues + Supabase (company display data)
                       |
Workers (pipeline.py) claim from Redis -> process board/scrape -> write to Local Postgres
                       | (new/changed jobs)
                       -> enqueue scrapes to Redis
                       |
R2 Drain -> poll descriptions WHERE NOT r2_uploaded -> PUT to R2
                       |
Exporter CDC -> SELECT WHERE updated_at > cursor -> batch COPY to Supabase
```

## File Structure

```
apps/crawler/src/
├── workers/
│   ├── pipeline.py          # Discovery coroutines, claim from Redis, dispatch
│   └── r2_drain.py          # Producer-consumer: descriptions -> R2
├── processing/
│   ├── board.py             # Streaming monitor processing, timestamp gone detection
│   ├── scrape.py            # Single-job scraping, fallback chain
│   ├── cpu.py               # CPU-bound processing (salary, location, etc.)
│   └── r2_stage.py          # Stage descriptions for R2 upload
├── queries/
│   ├── monitor.py           # SQL: DIFF_BATCH, MARK_GONE_BY_TIMESTAMP, record success/fail
│   ├── scrape.py            # SQL: UPDATE_JOB_CONTENT (conditional updated_at), RECORD_SCRAPE_*
│   └── lookups.py           # Cached lookup table loaders
├── redis_queue.py           # Lua-backed claim/enqueue/reschedule
├── lua/                     # claim_work.lua, enqueue_task.lua, reschedule_task.lua
├── exporter.py              # CDC: local Postgres -> Supabase (job_posting only, no boards)
├── sync.py                  # CSV -> Supabase + local Postgres + Redis
├── bootstrap.py             # One-time: Supabase -> local Postgres copy
├── cli.py                   # Entry point: crawler run/run-browser/export/drain/sync/board
├── config.py                # Settings: discovery_concurrency, monitor_concurrency, etc.
├── metrics.py               # Prometheus metrics
├── core/
│   ├── monitors/            # Monitor implementations (35+ types)
│   ├── scrapers/            # Scraper implementations
│   ├── description_store.py # R2 put/get
│   ├── monitor.py           # monitor_one, monitor_one_stream dispatchers
│   └── scrape.py            # scrape_one dispatcher
├── shared/
│   ├── browser.py           # Playwright launch (open_page context manager)
│   ├── http.py              # httpx client factory
│   └── ...
└── migrations/              # Alembic for local Postgres
```

## CLI Commands

```bash
crawler run              # HTTP worker (claims from simple queues)
crawler run-browser      # Browser worker (claims from browser queues)
crawler export           # CDC exporter loop
crawler drain            # R2 description uploader
crawler sync             # CSV -> DB + Redis
crawler reconcile        # Compare local vs Supabase, fix discrepancies
crawler board <slug>     # Process single board (debug)
```

## Deployment

```bash
# docker-compose.yml defines: redis, worker (x3), browser, exporter, drain, alloy
# deploy.sh: writes .env, pulls images, docker compose up, alembic migrate, crawler sync
# CI: .github/workflows/deploy-crawler-browser.yml builds slim + full images
```

Docker images:
- `crawler-slim` (~200MB): Python + httpx only (workers, exporter, drain)
- `crawler-full` (~600MB): Python + httpx + Playwright + Chromium (browser worker)

## Performance

| Component | Throughput | Memory |
|-----------|-----------|--------|
| HTTP worker | ~161 items/min per CPU | 1GB |
| Browser worker | ~13 items/min per CPU | 4GB (scales with cores) |
| Exporter | ~2000 rows/tick, <1s at steady state | 60MB |
| R2 drain | ~55 uploads/sec | 256MB |

## Failure Modes

| Failure | Impact | Recovery |
|---------|--------|----------|
| Worker crash | Task not completed | Redis schedule stale; sync.py re-bootstraps |
| Redis dies | Queued work lost | sync.py rebuilds from CSV; no data loss |
| Local Postgres down | Workers idle | Resume when Postgres recovers; data on persistent volume |
| Supabase down | Exporter can't flush | Changed rows accumulate in local Postgres; catches up on recovery |
| Exporter crash | CDC paused | Resumes from last_export_ts on restart |
| R2 drain failure | Unuploaded descriptions | Rows stay r2_uploaded = false; retried automatically |

Redis is **disposable infrastructure** -- state is rebuildable from CSV via sync.py. Local Postgres is the **authoritative store** -- protected by volume snapshots.
