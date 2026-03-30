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
