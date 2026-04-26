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

### 2. Scrape authority (fallback) — `_RECORD_SCRAPE_FAILURE` budget tombstone

Some upstream platforms violate the natural model. Avature's US Deloitte tenant is the documented case (issue #2708): the `apply.deloitte.com` SearchJobs SPA continues to list JobDetail URLs that the application then refuses to serve at the per-posting endpoint, returning 403 with a real Avature error page body. The monitor sees "URL still listed → not gone"; the scraper sees "403 → not fetchable". Without a fallback, the posting stays `is_active = true` forever — a dead link in the web app — because the monitor's signal never trips the delist threshold.

The fallback rule: when the per-posting scraper exhausts its existing 3-failure retry budget (≈90 minutes of doubling backoff: 0/30/60 min), `_RECORD_SCRAPE_FAILURE` *also* flips `is_active = false`, in addition to setting `next_scrape_at = NULL`. RFC-defined "this resource is gone" responses (HTTP 404 / 410) short-circuit the budget via the SQL's `permanent_gone` parameter and tombstone on the first failure. **Both branches are universal — no host allowlist.** The budget catches every variant of the same pattern (Avature 403, Workday cookieless 403, archived-then-403 on any platform). A host allowlist would be guaranteed-stale within a quarter; the budget is fix-it-once.

### 3. Recovery — relisted path

Either authority's tombstone is reversible. The monitor's discovery insert (`queries/monitor.py:163` — the `relisted` branch) flips `is_active = true` and resets `missing_count = 0` when a previously-tombstoned URL reappears in a fresh monitor cycle. So a transient 3-failure cluster on a still-live URL self-heals on the next monitor pass; only URLs that the monitor *also* doesn't re-list stay tombstoned.

### Why dual authority

Either authority alone is wrong:

- **Monitor-only**: leaks dead links forever when the upstream listing lies (the Avature case).
- **Scrape-only**: too aggressive — a transient 3-failure cluster on a live URL would tombstone it without the monitor's "I just saw it again" recovery signal.

Combining them gives bounded delisting latency in both cases — at most one full monitor cycle for the natural model, at most ≈90 min of scrape budget for the inconsistent-upstream case — with relisted-path recovery as the safety net.

The asymmetry is deliberate: the monitor cycle is the *fast* path (minutes) when the listing is honest; the scrape budget is the *slow but eventual* path (~90 min) when it isn't. We trust the monitor first because per-cycle URL sets are cheaper than 3 per-URL retries; the budget exists because we can't trust every monitor to be authoritative about every URL.

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
