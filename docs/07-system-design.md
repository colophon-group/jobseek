# System Design

Current design of all major subsystems across the crawler and web apps.

## Table of Contents

- [Infrastructure](#infrastructure)
- [Crawler: Monitor System](#crawler-monitor-system)
- [Crawler: Scraper System](#crawler-scraper-system)
- [Crawler: Batch Processor](#crawler-batch-processor)
- [Crawler: Scheduler](#crawler-scheduler)
- [Crawler: Redis Integration](#crawler-redis-integration)
- [Crawler: R2 Description Store](#crawler-r2-description-store)
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
| Crawler          | Fly.io                  | Long-running async Python process          |
| Database         | Neon Postgres           | Serverless driver (`@neondatabase/serverless`) everywhere |
| Redis            | Upstash                 | REST-based, serverless-compatible          |
| Email            | Resend                  | Transactional emails (verification, reset) |
| GitHub           | GitHub App (Octokit)    | Issue creation for company requests        |
| Object Storage   | Cloudflare R2            | Job descriptions, extras, version history         |
| Auth             | Better Auth (self-hosted) | Email/password + OAuth (GitHub, Google, LinkedIn) |

### Environment Variables

```
# Shared
DATABASE_URL                    # Neon Postgres connection string
UPSTASH_REDIS_REST_URL          # Upstash Redis REST endpoint
UPSTASH_REDIS_REST_TOKEN        # Upstash Redis auth token

# Web only
BETTER_AUTH_SECRET               # Session signing secret
BETTER_AUTH_URL                  # Base URL (e.g. https://jobseek.com)
GITHUB_CLIENT_ID / _SECRET       # OAuth
GOOGLE_CLIENT_ID / _SECRET       # OAuth
LINKEDIN_CLIENT_ID / _SECRET     # OAuth
GITHUB_APP_ID / _PRIVATE_KEY / _INSTALLATION_ID  # GitHub App
RESEND_API_KEY                   # Email

# Crawler only
CRAWLER_BATCH_LIMIT              # Max boards/URLs per batch (default: 200)
CRAWLER_POLL_INTERVAL            # Max idle interval in seconds (default: 15)
LOG_LEVEL                        # structlog level (default: INFO)

# Cloudflare R2 (crawler)
R2_ENDPOINT_URL                  # S3-compatible endpoint
R2_ACCESS_KEY_ID                 # R2 API token key ID
R2_SECRET_ACCESS_KEY             # R2 API token secret
R2_BUCKET                        # Bucket name (e.g. jobseek-assets)
R2_DOMAIN_URL                    # Public CDN URL (e.g. https://jobseek-assets.example.com)
```

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

All fields documented in [08 — Job Data Fields](./08-job-data-fields.md).

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

### MonitorResult

```python
@dataclass(slots=True)
class MonitorResult:
    urls: set[str]                                      # All discovered URLs
    jobs_by_url: dict[str, DiscoveredJob] | None = None # Rich data (API monitors)
    new_sitemap_url: str | None = None                  # Cached for future runs
    filtered_count: int = 0                             # URLs removed by url_filter
```

### Dispatcher

`monitor_one()` is a pure async function with no DB awareness:

```python
async def monitor_one(board_url, monitor_type, monitor_config, http, artifact_dir=None, pw=None) -> MonitorResult
```

1. Look up the registered `discover` function by `monitor_type`
2. Call `discover(board, http, pw=pw)`
3. Normalize result into `MonitorResult`
4. Apply `url_filter` (include/exclude regex from config)
5. Apply `url_transform` (regex find/replace rewrite from config)
6. Optionally save raw artifacts for debugging

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
| 10   | `personio`        | Rich*    | —           | Personio XML Feed (*HTML fallback needs scraper) |
| 10   | `pinpoint`        | Rich     | skip        | Pinpoint API                            |
| 10   | `recruitee`       | Rich     | skip        | Recruitee Careers API                   |
| 10   | `rippling`        | URL-only | rippling    | Rippling ATS API                        |
| 10   | `rss`             | Rich     | skip        | RSS 2.0 feed (SuccessFactors, Teamtailor, generic) |
| 10   | `smartrecruiters` | URL-only | smartrecruiters | SmartRecruiters API                 |
| 10   | `softgarden`      | URL-only | json-ld     | Softgarden ATS                          |
| 10   | `traffit`         | Rich     | skip        | Traffit ATS API                         |
| 10   | `workable`        | URL-only | workable    | Workable API                            |
| 10   | `workday`         | URL-only | workday     | Workday Job Board API                   |
| 20   | `nextdata`        | URL-only | —           | Next.js `__NEXT_DATA__` extraction      |
| 50   | `sitemap`         | URL-only | —           | XML sitemap parsing (auto-discovery)    |
| 80   | `api_sniffer`     | URL-only | —           | Playwright XHR/fetch capture            |
| 100  | `dom`             | URL-only | —           | Static/Playwright DOM link extraction   |

**Rich monitors** return `list[DiscoveredJob]` with full job content — no scraper needed.
**URL-only monitors** return `set[str]` — URLs are enqueued for scraping. Most have auto-configured scrapers (see `auto_scraper_type()` in `_compat.py`); monitors marked "—" require manual scraper selection.

### Auto-Detection

Each monitor can optionally implement `can_handle(url, client, pw=None) -> dict | None`. During `ws probe monitor`, all monitors are tried in cost order. A non-None return means the monitor can handle the board, with the returned dict used as initial config.

---

## Crawler: Scraper System

Scrapers extract structured job details from individual URLs. Only used when the monitor returns URL-only results.

### Registry

```
src/core/scrapers/__init__.py    # Registry, JobContent dataclass
src/core/scrapers/{type}.py      # One file per scraper type
src/core/scrape.py               # scrape_one() dispatcher
```

### JobContent

Same fields as `DiscoveredJob` plus `valid_through`. See [08 — Job Data Fields](./08-job-data-fields.md) for types, formats, and accepted values.

```python
@dataclass(slots=True)
class JobContent:
    title: str | None = None
    description: str | None = None       # HTML fragment
    locations: list[str] | None = None
    employment_type: str | None = None
    job_location_type: str | None = None  # "remote" | "hybrid" | "onsite"
    date_posted: str | None = None
    valid_through: str | None = None
    base_salary: dict | None = None       # {currency, min, max, unit}
    skills: list[str] | None = None
    responsibilities: list[str] | None = None
    qualifications: list[str] | None = None
    metadata: dict | None = None          # ATS-specific (department, team, id, ...)
```

### Dispatcher

```python
async def scrape_one(url, scraper_type, scraper_config, http, artifact_dir=None, job_id=None, pw=None) -> JobContent
```

1. Look up registered `scrape` function by type
2. Call `throttle_domain(url)` — per-domain rate limiting via Redis
3. Optionally save raw page for debugging
4. Call `scrape(url, config, http, pw=pw)`

### All Scraper Types

| Type           | Method                                             | Config Required        |
|----------------|----------------------------------------------------|------------------------|
| `json-ld`      | Parse `<script type="application/ld+json">` (JobPosting schema) | None (auto)    |
| `nextdata`     | Extract from `__NEXT_DATA__` JSON                  | `{path, fields}`       |
| `embedded`     | Parse embedded JSON (script tags, data attributes)  | `{pattern, path, fields}` |
| `dom`          | Step-based DOM extraction (static or Playwright)    | `{steps, render, ...}` |
| `api_sniffer`  | Capture XHR/fetch network requests                  | `{api_url, fields, pagination}` |

### Probe System

`ws probe scraper` tries all scraper types against sample URLs with heuristic auto-config. Each scraper can implement:
- `can_handle(htmls) -> dict | None` — detect pattern and suggest config
- `parse_html(html, config) -> JobContent` — extract without HTTP (for probing)
- `probe_pw(urls, pw) -> list[JobContent]` — Playwright-based probing (for api_sniffer)

---

## Crawler: Batch Processor

Orchestration layer that claims work from the database, groups it by rate-limit domain, and runs domain-parallel pipelines.

```
src/batch.py    # process_monitor_batch(), process_scrape_batch()
```

### Concurrency Model: Domain-Parallel Pipelines

The core problem: many boards share an ATS API host (e.g. all Greenhouse boards hit `boards-api.greenhouse.io`). Sending requests to the same host concurrently risks throttling. But boards on different hosts can be processed in parallel.

**Solution**: Group work items by their rate-limit domain, then run groups concurrently while serializing within each group.

```
Batch of 200 boards
  │
  ├── greenhouse (60 boards) ─── serial: board1 → board2 → ... → board60
  ├── lever (30 boards)      ─── serial: board1 → board2 → ... → board30
  ├── acme.com (1 board)     ─── serial: board1
  ├── bigcorp.com (1 board)  ─── serial: board1
  └── ...                        (all groups run concurrently via asyncio.TaskGroup)
```

**Rate-limit domain key** (`_throttle_key`):
- **API monitors** (greenhouse, lever, ashby, etc.): key = monitor type (all boards share one API host)
- **URL-only monitors** (sitemap, dom, etc.): key = hostname from `board_url` (each company has its own domain)

### Monitor Batch

```python
async def process_monitor_batch(pool, http, limit=200) -> BatchResult
```

1. **Claim boards**: `UPDATE ... WHERE is_enabled AND next_check_at <= now() ... FOR UPDATE SKIP LOCKED` — atomically claims boards and pushes `next_check_at` forward to prevent re-claiming during processing
2. **Group by domain**: `_throttle_key(board)` → `defaultdict[str, list]`
3. **Run pipelines**: `asyncio.TaskGroup` launches one `_monitor_pipeline()` per domain group; each pipeline processes its boards serially

**Per board** (`_process_one_board`):

1. Call `monitor_one(board_url, type, config, http)`
2. Diff discovered URLs against DB in a single SQL query:
   - **touched**: existing active jobs with matching URLs → update `last_seen_at`
   - **relisted**: previously delisted jobs reappearing → set `status = 'active'`
   - **gone**: active jobs no longer in discovered set → set `status = 'delisted'`
   - **new**: URLs not in DB at all
3. For **rich data** (API monitors): insert new jobs with full content directly
4. For **URL-only** monitors: insert URL stubs, schedule for scraping via `next_scrape_at`
5. Upload descriptions + extras to R2 (after DB transaction commits, via `asyncio.to_thread`)
6. Persist `description_r2_hash` for future change detection
7. Record success/failure on the board row (exponential backoff on failure, auto-disable after 5 consecutive failures)
8. Invalidate `cache:platform-stats` in Redis

### Scrape Batch

```python
async def process_scrape_batch(pool, http, limit=200) -> BatchResult
```

1. **Claim postings**: `UPDATE ... WHERE is_active AND next_scrape_at <= now() ... FOR UPDATE SKIP LOCKED` — claims postings due for scraping
2. **Group by hostname**: `urlparse(source_url).hostname` → `defaultdict[str, list]`
3. **Run pipelines**: `asyncio.TaskGroup` launches one `_scrape_pipeline()` per hostname; each pipeline processes its items serially

**Per posting** (`_process_one_scrape`):

1. Call `scrape_one(url, scraper_type, config, http)`
2. Update `job_posting` row with extracted content
3. Upload description + extras to R2
4. On failure: exponential backoff (interval doubles, capped at 24h)

### Board Scheduling

- **On success**: `next_check_at = now() + check_interval_minutes`, reset `consecutive_failures`
- **On failure**: `next_check_at = now() + min(5 * 2^failures, 1440) minutes`, increment `consecutive_failures`, auto-disable at 5. Retry schedule: 5 min → 10 min → 20 min → 40 min → disabled.

---

## Crawler: Scheduler

Entry point that calls the batch processor on a schedule.

```
src/scheduler.py    # run_poll_loop(), run_once()
```

### Adaptive Polling

```
while not shutdown:
    did_work = run_monitor_batch() + run_scrape_batch()
    if did_work:
        wait 1 second      # More work likely, check again quickly
    else:
        wait *= 2           # Back off exponentially (capped at CRAWLER_POLL_INTERVAL)
```

### Modes

```bash
uv run scheduler                  # Poll loop (production)
uv run scheduler --once           # Single batch (CI/CLI)
uv run scheduler --monitor-only   # Skip scraping
uv run scheduler --scrape-only    # Skip monitoring
```

Handles SIGTERM/SIGINT for graceful shutdown.

---

## Crawler: Redis Integration

All Redis operations are async via `upstash_redis.asyncio.Redis` (REST-based, serverless-compatible). Every Redis call is wrapped in `contextlib.suppress(Exception)` or try/except so the crawler degrades gracefully if Redis is unavailable.

```
src/shared/redis.py       # Singleton client (Redis.from_env())
src/shared/queue.py       # Scrape queue (list + sorted set + hash)
src/shared/dedup.py       # URL deduplication (set)
src/shared/throttle.py    # Per-domain request throttling (string)
```

### Redis Keys

| Key Pattern            | Type       | TTL      | Purpose                         |
|------------------------|------------|----------|---------------------------------|
| `throttle:{hostname}`  | String     | 10s      | Last request time per domain    |
| `cache:platform-stats` | String     | 6 hours  | Platform stats (invalidated by crawler) |
| `session:{token}`      | String     | 5 min    | Session cache (web app)         |
| `rl:auth`              | (Ratelimit)| Auto     | Auth endpoint rate limit        |
| `rl:pw-reset`          | (Ratelimit)| Auto     | Password reset rate limit       |
| `rl:company-req`       | (Ratelimit)| Auto     | Company request rate limit      |

> **Note**: The scrape queue previously used Redis lists/sets but has been migrated to Postgres-based scheduling via `next_scrape_at` + `FOR UPDATE SKIP LOCKED` on `job_posting`.

### Domain Throttling

```python
DEFAULT_DELAY = 2.0    # seconds between requests to same domain
API_DELAY = 0.5        # for known ATS APIs

KNOWN_ATS_HOSTS = {
    "boards-api.greenhouse.io", "api.lever.co", "api.ashbyhq.com",
    "api.smartrecruiters.com", "api.hireology.com", "api.rippling.com",
}
```

---

## Crawler: R2 Description Store

Job descriptions and structured extras are stored on Cloudflare R2 (S3-compatible). This offloads large text from Postgres and enables versioned history tracking.

```
src/core/description_store.py    # R2 upload/diff module
```

### R2 Layout

```
job/{posting_id}/{locale}/latest.html    — current description (HTML)
job/{posting_id}/{locale}/history.json   — version history + current extras
```

### history.json Structure

```json
{
  "current_extras": { "title": "...", "locations": [...], "metadata": {...}, ... },
  "versions": [
    { "timestamp": "2025-03-11T...", "diff": "--- new\n+++ old\n...", "extras": { "title": "old title" } },
    ...
  ]
}
```

- `current_extras`: latest snapshot of all structured fields (title, locations, metadata, date_posted, base_salary, raw_employment_type, raw_job_location_type)
- `versions`: reverse-chronological list of changes. Each entry contains:
  - `diff`: reverse unified diff (only if description HTML changed)
  - `extras`: dict of `{field: previous_value}` for changed fields (`null` = field was newly added)

### Change Detection

A `description_r2_hash` column (signed int64, truncated SHA-256) is stored on `job_posting`. Before uploading, the crawler computes the hash of `description + "\0" + json.dumps(extras)` and compares it to the stored hash. If unchanged, the R2 upload is skipped entirely.

### Upload Flow

R2 uploads happen **outside** the DB transaction to avoid holding connections during I/O:

1. DB transaction: insert/update rows, collect R2 work items
2. Commit transaction, release DB connection
3. `asyncio.to_thread(upload_posting, ...)` for each item (boto3 is synchronous)
4. Persist new `description_r2_hash` values in a separate DB call

---

## Crawler: CSV Sync

```
src/sync.py    # CSV → DB upsert
```

CSV files (`data/companies.csv`, `data/boards.csv`) are the source of truth for company and board configuration. `sync.py` upserts them into the database.

- **New rows**: Inserted with staggered `next_check_at` (random offset within 0–60 minutes to prevent thundering herd)
- **Existing rows**: Config updated, runtime fields preserved (`next_check_at`, `consecutive_failures`, etc.)
- **Removed rows**: Disabled (`is_enabled = false`), not deleted — preserves historical `job_posting` data

The `board.metadata` column stores monitor config as JSONB (merged from `monitor_config` + `scraper_type` + `scraper_config` in the CSV). The `crawler_type` column stores the monitor type.

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

### Configuration

```typescript
betterAuth({
  database: drizzleAdapter(db, { provider: "pg" }),
  emailAndPassword: {
    enabled: true,
    requireEmailVerification: true,
    revokeSessionsOnPasswordReset: true,
    sendResetPassword: async ({ user, url }, request) => { /* Resend email */ },
  },
  emailVerification: {
    sendOnSignUp: true,
    autoSignInAfterVerification: true,
    sendVerificationEmail: async ({ user, token }, request) => { /* Resend email */ },
  },
  user: {
    changeEmail: { enabled: true },
    deleteUser: { enabled: true },
  },
  socialProviders: { github, google, linkedin },
  hooks: {
    after: createAuthMiddleware(async (ctx) => {
      // Invalidate Redis session cache on sign-out, revoke, password reset
    }),
  },
  plugins: [nextCookies()],
});
```

### Auth API Route

POST requests are rate-limited (10/60s per IP). GET requests pass through without rate limiting.

```typescript
export async function POST(request: NextRequest) {
  const { success, reset } = await authLimiter.limit(getClientIp(request));
  if (!success) return new NextResponse("Too Many Requests", { status: 429, headers: { "Retry-After": ... } });
  return authPost(request);
}
```

---

## Web: Session Caching

```
src/lib/sessionCache.ts    # Redis-cached session resolution
```

Two-layer cache:

1. **React `cache()`**: Request-level dedup (prevents multiple DB queries in the same render)
2. **Redis `session:{token}`**: Cross-instance shared cache (5-minute TTL)

### Flow

```
getSession()
  → React cache() dedup
    → Extract token from cookies
      → Check Redis "session:{token}" (5 min TTL)
        → Hit: return cached session
        → Miss: call auth.api.getSession() (DB query)
          → Store in Redis
          → Return
```

### Invalidation

`invalidateSessionCache(token)` deletes the Redis key. Called automatically by the `after` hook on sign-out, session revocation, and password reset.

---

## Web: Rate Limiting

```
src/lib/rate-limit.ts    # Upstash Ratelimit instances
```

Uses `@upstash/ratelimit` with sliding window algorithm backed by Upstash Redis.

| Limiter              | Window       | Limit | Prefix          | Applied To                     |
|----------------------|--------------|-------|-----------------|--------------------------------|
| `authLimiter`        | 60 seconds   | 10    | `rl:auth`       | POST `/api/auth/[...all]`      |
| `passwordResetLimiter` | 300 seconds | 3     | `rl:pw-reset`   | Password reset requests        |
| `companyRequestLimiter` | 3600 seconds | 5    | `rl:company-req` | Company request submissions    |

All limiters key on client IP (`x-forwarded-for` header).

---

## Web: Cache System

```
src/lib/cache.ts    # Generic cache-aside utility
```

Replaces Next.js `unstable_cache` with a Redis-backed cache-aside pattern.

```typescript
async function cached<T>(key: string, fetcher: () => Promise<T>, options: { ttl: number }): Promise<T>
async function invalidate(key: string): Promise<void>
```

- Keys stored as `cache:{key}` in Redis
- Graceful degradation: Redis errors fall through to the fetcher
- Cross-instance: all Vercel serverless functions share the same cache

### Current Usage

| Key               | TTL     | Data                          | Invalidated By          |
|--------------------|---------|-------------------------------|-------------------------|
| `platform-stats`   | 6 hours | Company count + active job count | Crawler batch processor |

---

## Database Schema

Shared Neon Postgres database, managed by Drizzle ORM (web app) and raw asyncpg (crawler).

### Tables

#### `company`
Managed by CSV sync. Source of truth: `data/companies.csv`.

| Column     | Type   | Notes                    |
|------------|--------|--------------------------|
| id         | uuid   | PK, auto-generated       |
| slug       | text   | Unique                   |
| name       | text   |                          |
| website    | text   |                          |
| logo       | text   | Full primary logo URL    |
| logo_type  | text   | Full logo label (`wordmark`, `wordmark+icon`, `icon`) |
| icon       | text   | Minified square logo URL |
| created_at | timestamp |                       |
| updated_at | timestamp |                       |

#### `job_board`
Managed by CSV sync. Source of truth: `data/boards.csv`.

| Column                  | Type      | Notes                                    |
|-------------------------|-----------|------------------------------------------|
| id                      | uuid      | PK                                       |
| company_id              | uuid      | FK → company                             |
| board_slug              | text      | Unique (e.g. `stripe-careers`)           |
| crawler_type            | text      | Monitor type                             |
| board_url               | text      | Unique career page URL                   |
| check_interval_minutes  | int       | Default 60, may vary by board popularity |
| next_check_at           | timestamp | Index, set by scheduler                  |
| last_checked_at         | timestamp |                                          |
| last_success_at         | timestamp |                                          |
| consecutive_failures    | int       | Default 0, auto-disable at 5            |
| last_error              | text      |                                          |
| is_enabled              | boolean   | Default true                             |
| scrape_interval_hours   | int       | Default 24, per-board scrape frequency   |
| metadata                | jsonb     | Monitor + scraper config merged          |

#### `job_posting`

See [08 — Job Data Fields](./08-job-data-fields.md) for field types, formats, and accepted values.

**New columns** (R2 migration — dual-write period, old columns kept for compatibility):

| Column              | Type        | Notes                                              |
|---------------------|-------------|----------------------------------------------------|
| id                  | uuid        | PK                                                 |
| company_id          | uuid        | FK → company                                       |
| board_id            | uuid        | FK → job_board (nullable)                          |
| is_active           | boolean     | **New** — replaces `status` (`true` = active)      |
| locales             | text[]      | **New** — e.g. `["en", "de"]`                      |
| titles              | text[]      | **New** — parallel to locales                      |
| location_ids        | integer[]   | **New** — FK refs to `location` table (nullable)   |
| location_types      | text[]      | **New** — normalized location type tags (nullable) |
| description_r2_hash | bigint      | **New** — SHA-256 truncated to int64, change detect |
| title               | text        | *Legacy* — single display title                    |
| description         | text        | *Legacy* — HTML (being moved to R2)                |
| locations           | text[]      | One string per location                            |
| employment_type     | text        | Normalized: `full_time`, `part_time`, `contract`, `internship`, `full_or_part` |
| job_location_type   | text        | `remote` / `hybrid` / `onsite`                     |
| base_salary         | jsonb       | `{currency, min, max, unit}`                       |
| date_posted         | timestamptz |                                                    |
| language            | text        | *Legacy* — ISO 639-1 code                         |
| localizations       | jsonb       | *Legacy* — all locale versions                     |
| extras              | jsonb       | *Legacy* — structured supplementary data           |
| metadata            | jsonb       | *Legacy* — ATS-specific fields                     |
| source_url          | text        | Unique                                             |
| status              | text        | *Legacy* — `active` or `delisted`                  |
| first_seen_at       | timestamptz |                                                    |
| last_seen_at        | timestamptz |                                                    |
| next_scrape_at      | timestamptz | Postgres-based scrape scheduling                   |
| last_scraped_at     | timestamptz |                                                    |
| scrape_failures     | integer     | Exponential backoff counter                        |
| missing_count       | integer     | Consecutive monitor misses                         |
| leased_until        | timestamptz | Work-claiming lock                                 |

*Legacy* columns will be dropped after R2 migration is verified. Content served from R2: `job/{id}/{locale}/latest.html` and `job/{id}/{locale}/history.json`.

Indexes: `is_active` (partial), `location_ids` (GIN), `locations` (GIN), `next_scrape_at` (partial, where active).

#### Auth Tables (Better Auth)

- `user` (id, name, email, emailVerified, image)
- `session` (id, token, expiresAt, userId, ipAddress, userAgent)
- `account` (id, accountId, providerId, userId, tokens, password)
- `verification` (id, identifier, value, expiresAt)

#### Other Tables

- `user_preferences` — 1:1 with user (theme, locale, cookie consent, timestamps for conflict resolution)
- `location` — GeoNames-seeded location hierarchy (macro/country/region/city)
- `location_name` — Locale-specific location names (composite PK: location_id + locale)
- `location_macro_member` — Macro-region membership (e.g. EU countries)
- `subscription` — User subscription plans (free/unlimited)
- `saved_job` — User-saved job postings (unique per user + posting)
- `company_request` — User-submitted company requests (with GitHub issue tracking)

---

## Data Flow Diagrams

### Crawler Pipeline

```
data/companies.csv ──┐
data/boards.csv    ──┤  sync.py  ──→  company + job_board tables
                     └──────────┘

scheduler.py (poll loop)
  │
  ├── process_monitor_batch(limit=200)
  │     │
  │     ├── Claim due boards (FOR UPDATE SKIP LOCKED + push next_check_at)
  │     ├── Group by rate-limit domain (_throttle_key)
  │     ├── asyncio.TaskGroup: one pipeline per domain
  │     │     └── Serial within each pipeline:
  │     │           ├── Run monitor_one() → MonitorResult
  │     │           ├── Diff URLs (single SQL: new / relisted / gone / touched)
  │     │           ├── [Rich data]  → INSERT job_posting with full content
  │     │           ├── [URL-only]   → INSERT stubs → schedule scrape (next_scrape_at)
  │     │           ├── Upload descriptions + extras to R2 (after tx commit)
  │     │           ├── Persist description_r2_hash
  │     │           ├── Record success/failure on job_board
  │     │           └── Invalidate cache:platform-stats
  │     └── Return BatchResult(processed, succeeded, failed)
  │
  └── process_scrape_batch(limit=200)
        │
        ├── Claim due postings (next_scrape_at <= now(), FOR UPDATE SKIP LOCKED)
        ├── Group by target hostname
        ├── asyncio.TaskGroup: one pipeline per hostname
        │     └── Serial within each pipeline:
        │           ├── Run scrape_one() → JobContent
        │           ├── UPDATE job_posting with extracted content
        │           ├── Upload to R2 + persist hash
        │           └── Record success / apply backoff on failure
        └── Return BatchResult(processed, succeeded, failed)
```

### Web Authentication

```
User → POST /api/auth/[...all]
         │
         ├── Rate limit check (authLimiter: 10/60s per IP)
         │     └── 429 if exceeded
         │
         └── Better Auth handler
               ├── Sign up → create user + session + send verification email
               ├── Sign in → validate credentials + create session
               ├── OAuth  → redirect to provider → callback → create/link account
               └── After hook → invalidate Redis session cache on sign-out/reset
```

### Session Resolution

```
Server Component → getSession()
                     │
                     ├── React cache() dedup (per-request)
                     │
                     ├── Extract token from cookies
                     │
                     ├── Redis GET session:{token}
                     │     ├── Hit → return cached
                     │     └── Miss → auth.api.getSession() (DB)
                     │                  → SET session:{token} (5 min TTL)
                     │                  → return
                     │
                     └── Invalidation: sign-out / revoke / password reset
                           → DELETE session:{token}
```

### Company Request

```
User → requestCompany(formData)
         │
         ├── Validate input (2-200 chars)
         ├── Normalize (lowercase, trim, strip tracking params)
         │
         ├── [Exists in DB] → increment count, backfill GitHub issue if missing
         │
         └── [New] → INSERT company_request
                    → Create GitHub issue (labeled "company-request")
                    → Return issue number
```

### Stats Caching

```
getStats()
  → cached("platform-stats", fetcher, { ttl: 6h })
      → Redis GET cache:platform-stats
           ├── Hit → return
           └── Miss → COUNT(*) from company + job_posting
                     → Redis SET (6h TTL)

Crawler batch → Redis DEL cache:platform-stats (on new/delisted jobs)
```
