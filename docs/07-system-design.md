# System Design

Current design of all major subsystems across the crawler and web apps.

## Table of Contents

- [Infrastructure](#infrastructure)
- [Crawler: Redis Queue System](#crawler-redis-queue-system)
- [Crawler: Monitor System](#crawler-monitor-system)
- [Crawler: Scraper System](#crawler-scraper-system)
- [Crawler: Worker Pipeline](#crawler-worker-pipeline)
- [Crawler: R2 Description Store](#crawler-r2-description-store)
- [Crawler: Exporter CDC](#crawler-exporter-cdc)
- [Crawler: Per-Domain Proxy Layer](#crawler-per-domain-proxy-layer)
- [Crawler: CSV Sync](#crawler-csv-sync)
- [Web: Authentication](#web-authentication)
- [Web: Session Caching](#web-session-caching)
- [Web: Rate Limiting](#web-rate-limiting)
- [Web: Cache System](#web-cache-system)
- [Database Schema](#database-schema)
- [Data Flow Diagrams](#data-flow-diagrams)

---

## Infrastructure

| Component        | Service                 | Notes                                      |
|------------------|-------------------------|--------------------------------------------|
| Web app          | Vercel (Next.js 15)     | Serverless, edge-compatible                |
| Crawler workers  | Hetzner CPX31 (116.203.192.19) | 8 vCPU, 16GB RAM; 3 HTTP workers, 1 browser worker, exporter, drain, Redis, Alloy |
| Local Postgres   | Hetzner Dedicated (178.104.102.63) | Postgres 16, 20GB XFS volume; crawler source of truth |
| Supabase         | Managed Postgres        | Frontend read DB, populated by exporter CDC |
| Redis            | Local (Hetzner)         | Tiered ready queues, domain throttling, task config |
| Object Storage   | Cloudflare R2           | Job description HTML storage               |
| Observability    | Grafana Cloud           | Metrics (Prometheus) + logs (Loki) via Alloy |
| Email            | Resend                  | Transactional emails (verification, reset) |
| Auth             | Better Auth (self-hosted) | Email/password + OAuth (GitHub, Google, LinkedIn) |

### Environment Variables

```
# Shared
DATABASE_URL                    # Supabase Postgres connection string

# Crawler
LOCAL_DATABASE_URL              # Local Postgres (crawler's authoritative DB)
REDIS_URL                       # Local Redis (redis://localhost:6379/0)
R2_ENDPOINT_URL                 # S3-compatible endpoint
R2_ACCESS_KEY_ID                # R2 API token key ID
R2_SECRET_ACCESS_KEY            # R2 API token secret
R2_BUCKET                       # Bucket name (e.g. jobseek-assets)
R2_DOMAIN_URL                   # Public CDN URL
LOG_LEVEL                       # structlog level (default: INFO)
PROXY_MAP                       # JSON dict mapping hostnames to proxy URLs (optional)
WORKER_ID_PREFIX                # Container identity prefix (e.g. hetzner)
METRICS_PORT                    # Prometheus metrics port (9091-9094)

# Enrichment
ENRICH_PROVIDER                 # openai, anthropic, or gemini
ENRICH_MODEL                    # Model ID
ENRICH_API_KEY                  # Provider API key

# Web only
BETTER_AUTH_SECRET               # Session signing secret
BETTER_AUTH_URL                  # Base URL
GITHUB_CLIENT_ID / _SECRET       # OAuth
GOOGLE_CLIENT_ID / _SECRET       # OAuth
LINKEDIN_CLIENT_ID / _SECRET     # OAuth
GITHUB_APP_ID / _PRIVATE_KEY / _INSTALLATION_ID  # GitHub App
RESEND_API_KEY                   # Email
UPSTASH_REDIS_REST_URL           # Upstash Redis (web-only: sessions, rate limiting)
UPSTASH_REDIS_REST_TOKEN         # Upstash Redis auth token
```

---

## Crawler: Redis Queue System

All work distribution uses local Redis with Lua scripts for atomic operations. The crawler uses `redis.asyncio` (standard Redis protocol, not REST/Upstash).

```
src/redis_queue.py       # Lua-backed claim/enqueue/reschedule
src/lua/                 # claim_work.lua, enqueue_task.lua, reschedule_task.lua
```

### Tiered Ready Queues

6 sorted sets organized by transport and priority:

```
ready:simple:0     ZSET  score = next_check_at  (tier 0: first-time work)
ready:simple:1     ZSET  score = next_check_at  (tier 1: monitors)
ready:simple:2     ZSET  score = next_check_at  (tier 2: scrapes)
ready:browser:0    ZSET  score = next_check_at  (tier 0: first-time browser)
ready:browser:1    ZSET  score = next_check_at  (tier 1: browser monitors)
ready:browser:2    ZSET  score = next_check_at  (tier 2: browser scrapes)
```

Workers claim via `claim_work.lua` which atomically pops the highest-priority due item. Tasks are enqueued via `enqueue_task.lua` and rescheduled after processing via `reschedule_task.lua`.

### Domain Rate Limiting

```
ratelimit:{domain}    STRING  TTL-based cooldown per domain
```

Prevents concurrent requests to shared ATS APIs (e.g. all Greenhouse boards share `boards-api.greenhouse.io`).

### Redis Keys

| Key Pattern            | Type       | Purpose                         |
|------------------------|------------|----------------------------------|
| `ready:simple:0/1/2`  | Sorted Set | HTTP worker ready queues        |
| `ready:browser:0/1/2` | Sorted Set | Browser worker ready queues     |
| `ratelimit:{domain}`  | String     | Per-domain request cooldown     |
| `cache:platform-stats` | String    | Platform stats (6h TTL, invalidated by workers) |

---

## Crawler: Monitor System

Monitors discover which jobs exist on a career board. They return either full structured data (API monitors) or just URLs (site-scraping monitors).

### Registry

```
src/core/monitors/__init__.py    # Registry, DiscoveredJob dataclass
src/core/monitors/{type}.py      # One file per monitor type
src/core/monitor.py              # monitor_one() dispatcher
```

Each monitor registers itself with `register(name, discover, cost, can_handle)`. The registry is sorted by cost (cheaper monitors tried first during auto-detection).

### DiscoveredJob

All fields documented in [08 -- Job Data Fields](./08-job-data-fields.md).

```python
@dataclass(slots=True)
class DiscoveredJob:
    url: str
    title: str | None = None
    description: str | None = None       # HTML fragment
    locations: list[str] | None = None
    employment_type: str | None = None
    job_location_type: str | None = None
    date_posted: str | None = None
    base_salary: dict | None = None      # {currency, min, max, unit}
    skills: list[str] | None = None
    responsibilities: list[str] | None = None
    qualifications: list[str] | None = None
    metadata: dict | None = None         # ATS-specific (department, team, id, ...)
```

### Dispatcher

`monitor_one()` is a pure async function with no DB awareness:

```python
async def monitor_one(board_url, monitor_type, monitor_config, http, artifact_dir=None, pw=None) -> MonitorResult
```

### All Monitor Types

| Cost | Type              | Return   | Auto-scraper | Method                                  |
|------|-------------------|----------|-------------|-----------------------------------------|
| 9    | `join`            | URL-only | nextdata    | JOIN (join.com) Next.js data            |
| 10   | `amazon`          | Rich     | skip        | Amazon Jobs                             |
| 10   | `ashby`           | Rich     | skip        | Ashby Job Board API                     |
| 10   | `bite`            | URL-only | bite        | b-ite.com ATS API                       |
| 10   | `breezy`          | URL-only | json-ld     | Breezy HR listing endpoint              |
| 10   | `dvinci`          | Rich     | skip        | d.vinci ATS API                         |
| 10   | `gem`             | Rich     | skip        | Gem ATS API                             |
| 10   | `greenhouse`      | Rich     | skip        | Greenhouse JSON API                     |
| 10   | `hireology`       | Rich     | skip        | Hireology Careers API                   |
| 10   | `lever`           | Rich     | skip        | Lever Postings API                      |
| 10   | `personio`        | Rich*    | --          | Personio XML Feed (*HTML fallback needs scraper) |
| 10   | `pinpoint`        | Rich     | skip        | Pinpoint API                            |
| 10   | `recruitee`       | Rich     | skip        | Recruitee Careers API                   |
| 10   | `rippling`        | URL-only | rippling    | Rippling ATS API                        |
| 10   | `rss`             | Rich     | skip        | RSS 2.0 feed (SuccessFactors, Teamtailor, generic) |
| 10   | `smartrecruiters` | URL-only | smartrecruiters | SmartRecruiters API                 |
| 10   | `softgarden`      | URL-only | json-ld     | Softgarden ATS                          |
| 10   | `traffit`         | Rich     | skip        | Traffit ATS API                         |
| 10   | `workable`        | URL-only | workable    | Workable API                            |
| 10   | `workday`         | URL-only | workday     | Workday Job Board API                   |
| 20   | `nextdata`        | URL-only | --          | Next.js `__NEXT_DATA__` extraction      |
| 50   | `sitemap`         | URL-only | --          | XML sitemap parsing (auto-discovery)    |
| 80   | `api_sniffer`     | URL-only | --          | Playwright XHR/fetch capture            |
| 100  | `dom`             | URL-only | --          | Static/Playwright DOM link extraction   |

---

## Crawler: Scraper System

Scrapers extract structured job details from individual URLs. Only used when the monitor returns URL-only results.

### Registry

```
src/core/scrapers/__init__.py    # Registry, JobContent dataclass
src/core/scrapers/{type}.py      # One file per scraper type
src/core/scrape.py               # scrape_one() dispatcher
```

### All Scraper Types

| Type           | Method                                             | Config Required        |
|----------------|----------------------------------------------------|------------------------|
| `json-ld`      | Parse `<script type="application/ld+json">` (JobPosting schema) | None (auto)    |
| `nextdata`     | Extract from `__NEXT_DATA__` JSON                  | `{path, fields}`       |
| `embedded`     | Parse embedded JSON (script tags, data attributes)  | `{pattern, path, fields}` |
| `dom`          | Step-based DOM extraction (static or Playwright)    | `{steps, render, ...}` |
| `api_sniffer`  | Capture XHR/fetch network requests                  | `{api_url, fields, pagination}` |

---

## Crawler: Worker Pipeline

Workers run a fixed internal pipeline claiming from Redis queues.

```
src/workers/pipeline.py     # Discovery coroutines, claim from Redis, dispatch
```

### HTTP Worker (`crawler run`)

Claims from `ready:simple:*` queues. Processes both monitors and scrapes. 3 replicas on the current Hetzner deployment, each allocated 1 CPU and 1GB memory.

### Browser Worker (`crawler run-browser`)

Claims from `ready:browser:*` queues. Same pipeline pattern but with Chromium available. 1 replica with 3 CPUs and 4GB memory.

### Processing Flow

All monitors use the streaming path (`_process_one_board_streaming` in `processing/board.py`):

1. Monitor discovers jobs (yields batches for large datasets)
2. Diff against local Postgres in a single SQL query (new/touched/relisted/gone)
3. Rich data: insert full `job_posting` rows directly
4. URL-only: insert URL stubs, enqueue scrapes to Redis
5. Upload descriptions to `descriptions` table (R2 drain picks them up)
6. Record success/failure, reschedule board in Redis

---

## Crawler: R2 Description Store

Job descriptions are stored on Cloudflare R2 (S3-compatible). Only `latest.html` per locale -- no version history.

```
src/core/description_store.py    # R2 put/get
src/workers/r2_drain.py          # Producer-consumer drain pipeline
```

### R2 Layout

```
job/{posting_id}/{locale}/latest.html    -- current description (HTML)
```

### Change Detection

A `description_r2_hash` column (signed int64, truncated SHA-256) on `job_posting` enables skip-on-unchanged. The hash is computed from description content before upload.

### Upload Flow (R2 Drain)

The `descriptions` table in local Postgres serves as the upload queue:

1. Workers write HTML to `descriptions` with `r2_uploaded = false`
2. Drain producer claims rows atomically (`UPDATE ... SET r2_uploaded = NULL ... RETURNING`)
3. Drain consumers PUT `latest.html` to R2
4. On success: mark `r2_uploaded = true`, update `description_r2_hash` on `job_posting`
5. On failure: revert to `r2_uploaded = false` for retry

---

## Crawler: Exporter CDC

```
src/exporter.py    # CDC: local Postgres -> Supabase
```

The exporter is the only component that writes to Supabase. It queries local Postgres for rows with `updated_at > last_export_ts` and batch COPYs them to Supabase.

### Export Loop

- Polls every 1-2 seconds
- Batch size: 2000 rows per tick
- Throughput: ~2100 rows/sec sustained
- Latency: ~1.5s average (change to visible on Supabase)

### What Gets Exported

- `job_posting`: all display columns (titles, locales, locations, employment type, salary, enrichment, etc.)
- Board status is NOT exported (Supabase `job_board` is populated by `sync.py` only)

### Reconciliation

Daily reconciliation compares local Postgres against Supabase and re-exports any discrepancies by touching `updated_at` on local Postgres (picked up by CDC on next cycle).

CLI: `crawler reconcile [--full]`

---

## Crawler: Per-Domain Proxy Layer

Optional proxy routing for domains that block direct requests.

```
src/shared/proxy.py    # proxy_for_url(), build_httpx_mounts(), build_playwright_proxy()
```

Set `PROXY_MAP` as a JSON dict mapping hostnames to proxy URLs:

```bash
PROXY_MAP='{"apply.workable.com": "http://user:pass@gate.smartproxy.com:7777"}'
```

- **httpx**: Transport mounts per domain, wired automatically in `create_http_client()`
- **Playwright**: `open_page()` accepts `target_url` kwarg, launches browser with proxy when configured
- Per-domain, not per-board -- avoids CSV schema changes
- Orthogonal to throttling -- rate limits remain in effect regardless of proxy

---

## Crawler: CSV Sync

```
src/sync.py    # CSV -> DB upsert
```

CSV files are the source of truth. `sync.py` writes to THREE targets in one pass:

1. **Local Postgres**: full board config (all columns)
2. **Supabase**: company display data + minimal board reference
3. **Redis**: board config and initial schedule in ready queues

- **New rows**: Inserted with staggered `next_check_at` (random offset to prevent thundering herd)
- **Existing rows**: Config updated, runtime fields preserved
- **Removed rows**: Disabled (`is_enabled = false`), not deleted

---

## Web: Authentication

```
src/lib/auth.ts              # Server-side Better Auth config
src/lib/auth-client.ts       # Client-side auth client
app/api/auth/[...all]/route.ts  # Catch-all API route
```

### Providers

- **Email/password**: Enabled with email verification required
- **GitHub OAuth**: Standard OAuth flow
- **Google OAuth**: Standard OAuth flow
- **LinkedIn OAuth**: Standard OAuth flow

POST requests rate-limited (10/60s per IP). GET requests pass through.

---

## Web: Session Caching

```
src/lib/sessionCache.ts    # Redis-cached session resolution
```

Two-layer cache:

1. **React `cache()`**: Request-level dedup
2. **Upstash Redis `session:{token}`**: Cross-instance cache (5-minute TTL)

Invalidation: `invalidateSessionCache(token)` deletes the Redis key on sign-out, session revocation, and password reset.

---

## Web: Rate Limiting

```
src/lib/rate-limit.ts    # Upstash Ratelimit instances
```

Uses `@upstash/ratelimit` with sliding window algorithm backed by Upstash Redis.

| Limiter              | Window       | Limit | Applied To                     |
|----------------------|--------------|-------|--------------------------------|
| `authLimiter`        | 60 seconds   | 10    | POST `/api/auth/[...all]`      |
| `passwordResetLimiter` | 300 seconds | 3     | Password reset requests        |
| `companyRequestLimiter` | 3600 seconds | 5    | Company request submissions    |

---

## Web: Cache System

```
src/lib/cache.ts    # Generic cache-aside utility
```

Redis-backed cache-aside pattern replacing `unstable_cache`.

| Key               | TTL     | Data                          | Invalidated By          |
|--------------------|---------|-------------------------------|-------------------------|
| `platform-stats`   | 6 hours | Company count + active job count | Crawler workers |

---

## Database Schema

### Two Databases

- **Local Postgres** (Hetzner): Full schema with all crawler columns. Managed by Alembic migrations.
- **Supabase**: Display subset -- no scheduling, config, or lease columns. Managed by Drizzle ORM.

### Key Tables

#### `company`
Managed by CSV sync. Source of truth: `data/companies.csv`.

| Column     | Type   | Notes                    |
|------------|--------|--------------------------|
| id         | uuid   | PK                       |
| slug       | text   | Unique                   |
| name       | text   |                          |
| website    | text   |                          |
| logo       | text   | Full primary logo URL    |
| icon       | text   | Minified square logo URL |
| logo_type  | text   | `wordmark`, `wordmark+icon`, `icon` |

#### `job_board`
Managed by CSV sync. Source of truth: `data/boards.csv`.

Local Postgres has full schema (scheduling, config, state). Supabase has display subset only (id, company_id, board_slug, board_url, crawler_type, metadata, board_status, is_enabled).

#### `job_posting`
See [08 -- Job Data Fields](./08-job-data-fields.md) for field types and formats.

Key columns: `id`, `company_id`, `board_id`, `source_url` (unique), `is_active`, `titles` (text[]), `locales` (text[]), `location_ids` (int[]), `location_types` (text[]), `employment_type`, `salary_*` columns, `description_r2_hash`, `enrichment` (jsonb), `first_seen_at`, `last_seen_at`, `updated_at`.

Local Postgres additionally has: `missing_count`, scrape scheduling columns, and the `descriptions` table for R2 upload queue.

#### Auth Tables (Better Auth)
- `user`, `session`, `account`, `verification`

#### Other Tables
- `user_preferences`, `location`, `location_name`, `location_macro_member`, `subscription`, `saved_job`, `company_request`

---

## Data Flow Diagrams

### Crawler Pipeline

```
data/companies.csv --+
data/boards.csv    --+  sync.py  -->  Local Postgres + Supabase + Redis queues
                     +----------+

Workers claim from Redis tiered queues
  |
  +-- Monitor path:
  |     +-- claim_work.lua pops from ready:simple:1 or ready:browser:1
  |     +-- monitor_one() -> MonitorResult
  |     +-- Diff URLs (SQL: new / relisted / gone / touched)
  |     +-- [Rich data]  -> INSERT job_posting with full content
  |     +-- [URL-only]   -> INSERT stubs, enqueue scrapes to Redis
  |     +-- Write descriptions to descriptions table
  |     +-- reschedule_task.lua -> re-enqueue board
  |
  +-- Scrape path:
  |     +-- claim_work.lua pops from ready:simple:2 or ready:browser:2
  |     +-- scrape_one() -> JobContent
  |     +-- UPDATE job_posting (conditional updated_at)
  |     +-- Write description to descriptions table
  |     +-- reschedule_task.lua -> re-enqueue scrape
  |
  +-- R2 Drain:
  |     +-- Poll descriptions WHERE NOT r2_uploaded
  |     +-- PUT latest.html to R2
  |     +-- Mark r2_uploaded = true
  |
  +-- Exporter CDC:
        +-- SELECT WHERE updated_at > cursor
        +-- Batch COPY to Supabase
```

### Company Request

```
User -> requestCompany(formData)
         |
         +-- Validate input
         +-- [Exists in DB] -> increment count, backfill GitHub issue if missing
         +-- [New] -> INSERT company_request
                    -> Create GitHub issue (labeled "company-request")
                    -> Return issue number
```
