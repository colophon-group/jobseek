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
- [Crawler: Proxy-Routed Transport](#crawler-proxy-routed-transport)
- [Crawler: CSV Sync](#crawler-csv-sync)
- [Web: Authentication](#web-authentication)
- [Web: Session Caching](#web-session-caching)
- [Web: Rate Limiting](#web-rate-limiting)
- [Web: Public API Metrics](#web-public-api-metrics)
- [Web: Cache System](#web-cache-system)
- [Database Schema](#database-schema)
- [Data Flow Diagrams](#data-flow-diagrams)

---

## Infrastructure

| Component        | Service                 | Notes                                      |
|------------------|-------------------------|--------------------------------------------|
| Web app          | Vercel (Next.js 15)     | Serverless, edge-compatible                |
| Crawler workers  | Hetzner CPX31 (116.203.192.19) | 8 vCPU, 16GB RAM; 3 HTTP workers, 1 browser worker, exporter, drain, Redis, Alloy; native ATS monitors run without upstream scraper dependencies |
| Local Postgres   | Hetzner Dedicated (178.104.102.63) | Postgres 16, 20GB XFS volume; crawler source of truth |
| Web-owned Postgres | Managed Postgres      | Retained user/auth/watchlist data only      |
| Redis            | Local (Hetzner)         | Tiered ready queues, domain throttling, task config |
| Object Storage   | Cloudflare R2           | Job description HTML storage               |
| Observability    | Grafana Cloud           | Metrics (Prometheus) + logs (Loki) via Alloy |
| Email            | Resend                  | Transactional emails (verification, reset) |
| Auth             | Better Auth (self-hosted) | Email/password + OAuth (GitHub, Google, LinkedIn) |

### Environment Variables

```
# Crawler
LOCAL_DATABASE_URL              # Local Postgres (crawler's authoritative DB)
WEB_DATABASE_URL                # Optional web-owned watchlist boundary for explicit sync/refresh jobs
REDIS_URL                       # Local Redis (redis://localhost:6379/0)
R2_ENDPOINT_URL                 # S3-compatible endpoint
R2_ACCESS_KEY_ID                # R2 API token key ID
R2_SECRET_ACCESS_KEY            # R2 API token secret
R2_BUCKET                       # Bucket name (e.g. jobseek-assets)
R2_DOMAIN_URL                   # Public CDN URL
LOG_LEVEL                       # structlog level (default: INFO)
PROXY_PROVIDER                  # webshare | none (default: none)
WEBSHARE_PROXY_URLS             # JSON per-proxy p.webshare.io backbone URL pool
WEBSHARE_PROXY_URL              # Migration-only direct/static fallback
WEBSHARE_PROXY_CANARY_SLOT      # Local same-egress diagnostic pin; not deployed
WEBSHARE_API_KEY                # Local operator audit/config only; never deployed
WEBSHARE_EXPECTED_CLIENT_IPS    # JSON production-egress allowlist for local audit
WORKER_ID_PREFIX                # Container identity prefix (e.g. hetzner)
METRICS_PORT                    # Prometheus metrics port (9091-9094)

# Enrichment
ENRICH_PROVIDER                 # empty disables; prefer openai for new smokes; anthropic/gemini supported
ENRICH_MODEL                    # Provider model ID
ENRICH_API_KEY                  # Provider API key

# Web only
BETTER_AUTH_SECRET               # Session signing secret
BETTER_AUTH_URL                  # Base URL
GITHUB_CLIENT_ID / _SECRET       # OAuth
GOOGLE_CLIENT_ID / _SECRET       # OAuth
LINKEDIN_CLIENT_ID / _SECRET     # OAuth
GITHUB_APP_ID / _PRIVATE_KEY / _INSTALLATION_ID  # GitHub App
RESEND_API_KEY                   # Email
UPSTASH_REDIS_REST_URL           # Upstash Redis (web-only: sessions, rate limiting, API metrics)
UPSTASH_REDIS_REST_TOKEN         # Upstash Redis auth token
API_METRICS_HMAC_SECRET          # HMAC key for daily, non-reversible API network-client estimates
HOSTED_MCP_API_PROVENANCE_TOKEN  # Private marker authenticating hosted-MCP -> REST attribution
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

A recurring mixed-work domain has one tier-1 score for its earliest monitor
and one tier-2 score for its earliest scrape. First-time work suppresses those
recurring representations and stays exclusively in tier 0 until drained.

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
| 8    | `eightfold`       | URL-only | eightfold   | Eightfold AI sitemap + position API fallback |
| 9    | `join`            | URL-only | nextdata    | JOIN (join.com) Next.js data            |
| 9    | `phenom`          | URL-only | json-ld     | Phenom People sitemap-based discovery   |
| 10   | `accenture`       | Rich     | skip        | Accenture career API                    |
| 10   | `adp`             | Rich     | adp         | ADP Workforce Now listing API + detail enrichment |
| 10   | `almacareer`      | Rich     | skip        | AlmaCareer / Capybara GraphQL API       |
| 10   | `amazon`          | Rich     | skip        | Amazon Jobs                             |
| 10   | `ashby`           | Rich     | skip        | Ashby Job Board API                     |
| 10   | `avature`         | URL-only | dom         | Avature static listings + map data      |
| 10   | `jobvite`         | URL-only | json-ld     | Jobvite static career-site listings     |
| 10   | `pageup`          | Rich     | dom         | PageUp static listings + description enrichment |
| 45   | `papa_johns`      | URL-only | json-ld     | Papa Johns branded listings with count-checked pagination |
| 10   | `bamboohr`        | Rich     | api_sniffer | BambooHR public careers API + detail enrichment |
| 10   | `beehire`         | Rich     | skip        | Beehire public campaign API              |
| 10   | `beisen`          | Rich/hybrid | skip/dom | Beisen modern public API + legacy listings |
| 10   | `brassring`       | Rich     | skip        | BrassRing TGnewUI browser-session search API |
| 10   | `cnstaff`         | Rich     | skip        | CNStaff paginated public career-board JSON |
| 60   | `candidatus`      | URL-only | dom         | Candidatus WinDev browser-resolved detail postbacks |
| 10   | `paycom`          | Rich     | paycom      | Paycom bootstrap + preview API + detail enrichment |
| 10   | `jazzhr`          | URL-only | jazzhr      | ApplyToJob static listing + JSON-LD/DOM detail parsing |
| 10   | `job51`           | Rich     | skip        | 51job employer microsite listing and detail CoAPI |
| 10   | `jobbank104`      | URL-only | json-ld     | 104 Job Bank company-page links through optional proxy transport |
| 10   | `jobdiva`         | URL-only | api_sniffer | JobDiva token bootstrap + native range pagination |
| 10   | `jobstreet`       | Rich     | jobstreet   | JobStreet employer search summaries + GraphQL detail enrichment |
| 10   | `seek`            | URL-only | seek        | SEEK AU/NZ advertiser search + GraphQL detail enrichment |
| 10   | `icims`           | URL-only | json-ld     | iCIMS static listings + bounded pagination |
| 10   | `infoniqa`        | URL-only | --          | Infoniqa employer-bound session POST pagination |
| 10   | `infor`           | Rich     | infor       | Infor CandidateSelfService Landmark APIs + detail enrichment |
| 10   | `intervieweb`     | URL-only | json-ld     | Intervieweb HTML + CSRF-protected POST pagination |
| 10   | `gupy`            | URL-only | json-ld     | Gupy single-page NextData inventory |
| 10   | `cornerstone`     | Rich     | skip        | Cornerstone bootstrap + regional paginated search API |
| 10   | `curately`        | Rich     | skip        | Curately tenant-scoped public contractor search API |
| 10   | `cvwarehouse`     | Rich     | skip        | CVWarehouse localized hosted-board inventory |
| 10   | `darwinbox`       | Rich     | skip        | Darwinbox browser-session public jobs API |
| 10   | `dayforce`        | Rich     | skip        | Dayforce browser-context public search BFF |
| 10   | `herp`            | URL-only | json-ld     | HERP Hire single static requisition listing |
| 10   | `hrmos`           | URL-only | json-ld     | HRMOS static listings with bounded pagination |
| 10   | `bite`            | URL-only | bite        | b-ite.com ATS API                       |
| 10   | `breezy`          | URL-only | json-ld     | Breezy HR listing endpoint              |
| 10   | `comeet`          | Rich     | skip        | Comeet hosted data and Careers API      |
| 10   | `computrabajo`    | URL-only | json-ld     | Computrabajo employer pages with explicit totals and bounded pagination |
| 10   | `deel`            | Rich     | skip        | Deel ATS API                            |
| 10   | `dvinci`          | Rich     | skip        | d.vinci ATS API                         |
| 10   | `earcu`           | Rich     | skip        | eArcu live-vacancy XML feed             |
| 10   | `gem`             | Rich     | skip        | Gem ATS API                             |
| 10   | `inploi`          | Rich     | json-ld     | Inploi public search API + description enrichment |
| 10   | `greenhouse`      | Rich     | skip        | Greenhouse JSON API                     |
| 10   | `headhunter`      | Rich     | headhunter  | Proxy-routed HeadHunter employer API + detail enrichment |
| 10   | `hibob`           | Rich     | skip        | HiBob public career-site API            |
| 10   | `hirehive`        | Rich     | skip        | HireHive public Jobs API                |
| 10   | `hireology`       | Rich     | skip        | Hireology Careers API                   |
| 10   | `turbohire`       | Rich     | skip        | TurboHire public career API             |
| 10   | `jarvi`           | Rich     | skip        | Jarvi public careers API                |
| 10   | `jobylon`         | Rich     | skip        | Jobylon iframe embed data               |
| 10   | `johdi`           | URL-only | johdi       | Johdi Suite embedded widget API         |
| 10   | `jobs_ch`         | URL-only | json-ld     | jobs.ch employer profile search API     |
| 10   | `keka`            | Rich     | skip        | Keka public career-portal jobs API      |
| 10   | `lever`           | Rich     | skip        | Lever Postings API                      |
| 10   | `linkedin`        | Rich     | linkedin    | LinkedIn guest-job summaries + detail enrich |
| 10   | `manatal`         | Rich     | skip        | Manatal public Careers Page API         |
| 10   | `mokahr`          | Rich     | skip        | Mokahr encrypted listing API            |
| 10   | `paylocity`       | Rich     | paylocity   | Paylocity embedded summaries + detail enrich |
| 10   | `personio`        | Conditional* | --     | Personio XML feed; HTML fallback needs scraper |
| 10   | `pinpoint`        | Rich     | skip        | Pinpoint API                            |
| 10   | `practicematch`   | URL-only | json-ld     | Proxy-routed employer form pagination  |
| 10   | `prospective`     | Rich     | skip        | CareerCenter POST pagination with durable application identity |
| 10   | `recruitee`       | Rich     | skip        | Recruitee Careers API                   |
| 10   | `recruiterbox`    | URL-only | json-ld     | Recruiterbox / Trakstar Hire static listings |
| 10   | `taleo`           | URL-only | json-ld     | Taleo Business Edition total/cursor listings |
| 10   | `rippling`        | URL-only | rippling    | Rippling ATS API                        |
| 10   | `rss`             | Rich     | skip        | RSS 2.0 feed (SuccessFactors, Teamtailor, generic) |
| 10   | `seamlesshiring`  | Rich     | skip        | SeamlessHiring public candidate API     |
| 10   | `smartrecruiters` | URL-only | smartrecruiters | SmartRecruiters API                 |
| 10   | `softgarden`      | URL-only | json-ld     | Softgarden ATS                          |
| 10   | `traffit`         | Rich     | skip        | Traffit ATS API                         |
| 10   | `typify`          | Rich     | json-ld     | Typify partitioned vacancy API + description enrichment |
| 10   | `ukg`             | Rich     | embedded    | UKG Pro search API + detail enrichment  |
| 10   | `unifr`           | Rich/URL-only | skip/pdf | University of Fribourg FR/DE and faculty inventories |
| 10   | `unisante`        | Rich     | skip        | Bounded Unisanté official listing/detail validation |
| 10   | `welcometothejungle` | Rich  | skip        | Welcome to the Jungle public jobs APIs  |
| 10   | `workable`        | URL-only | workable    | Workable API                            |
| 10   | `workday`         | URL-only | workday     | Workday Job Board API                   |
| 10   | `ycombinator`     | URL-only | json-ld     | YCombinator Jobs fallback pages         |
| 15   | `notion`          | URL-only | --          | Notion internal API enumeration         |
| 15   | `oracle_hcm`      | Rich     | oracle_hcm  | Oracle HCM REST API + description enrich |
| 15   | `recruiter_co_kr` | Rich     | skip        | Recruiter.co.kr API                     |
| 15   | `umantis`         | URL-only | --          | Umantis HTML listings                   |
| 20   | `nextdata`        | Conditional* | skip/-- | Embedded JSON / Next.js data extraction |
| 45   | `talemetry`       | URL-only | json-ld     | Talemetry/Jobvite result-range pagination |
| 45   | `talentbrew`      | URL-only | json-ld     | TalentBrew/Radancy search results       |
| 50   | `sitemap`         | URL-only | --          | XML sitemap parsing (auto-discovery)    |
| 60   | `inline`          | Rich     | skip        | Inline single-page job extraction       |
| 60   | `kipt`            | Rich     | skip        | Active KIPT PDF bulletin splitting      |
| 80   | `api_sniffer`     | Conditional* | skip/-- | Direct API or Playwright XHR/fetch capture |
| 80   | `njoyn`           | URL-only | --          | Njoyn XWeb session-bound form pagination |
| 100  | `dom`             | URL-only | --          | Static/Playwright DOM link extraction   |

*Conditional monitors return rich data only when their runtime source/config
provides full fields. Without that condition, they behave like URL-only
monitors and need an explicit or auto-resolved scraper.

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
| `adp`          | Fetch ADP detail JSON and DOCX description attachments | None               |
| `api_sniffer`  | Direct API replay or XHR/fetch capture on job pages | `{api_url, fields, pagination}` |
| `bite`         | Fetch BITE detail JSON                             | None                   |
| `dom`          | Step-based DOM extraction (static or Playwright)   | `{steps, scope?, render, ...}` |
| `eightfold`    | JSON-LD extraction with Eightfold position API fallback | None              |
| `embedded`     | Parse embedded JSON/RSC data from page source      | `{pattern/script_id/source, path, fields}` |
| `headhunter`   | Fetch proxy-routed HeadHunter vacancy detail JSON  | None                   |
| `jobstreet`    | Fetch JobStreet vacancy detail GraphQL data        | None                   |
| `johdi`        | Fetch Johdi Suite public offer-detail JSON         | `{company_key, flow, locale}` |
| `json-ld`      | Parse `<script type="application/ld+json">` (JobPosting schema) | None (auto)    |
| `mokahr`       | Fetch and decrypt Mokahr detail API records        | None                   |
| `nextdata`     | Extract from `__NEXT_DATA__` JSON                  | `{path, fields}`       |
| `notion`       | Convert Notion API blocks to structured content    | `property_map` optional |
| `onlyfy`       | Fetch Onlyfy/Prescreen server-rendered candidate pages | `language` optional |
| `infor`        | Fetch session-bound Infor CandidateSelfService detail responses | None |
| `oracle_hcm`   | Fetch Oracle HCM detail REST responses             | `{host, site}`         |
| `paycom`       | Bootstrap Paycom and fetch regional detail API      | None                   |
| `paycor`       | Parse Paycor/Newton server-rendered detail fields   | None                   |
| `jazzhr`       | Parse JSON-LD, then JazzHR DOM fallback in-memory    | None                   |
| `paylocity`    | Parse Paylocity server-rendered detail pages       | None                   |
| `linkedin`     | Fetch LinkedIn public guest-job detail fragments   | None                   |
| `pdf`          | Download PDF files and extract text content        | Title extraction optional |
| `phuketall`    | Parse PhuketAll employer job pages                 | None                   |
| `rippling`     | Fetch Rippling detail API records                  | None                   |
| `seek`         | Fetch SEEK AU/NZ vacancy detail GraphQL data        | None                   |
| `skip`         | No-scrape marker for rich monitor output           | None                   |
| `smartrecruiters` | Fetch SmartRecruiters detail API records        | None                   |
| `taleo`       | Parse Taleo Enterprise embedded detail payload      | None                   |
| `veryeast`    | Parse complete, bounded VeryEast employer job pages | None                   |
| `workable`     | Fetch Workable detail API records                  | None                   |
| `workday`      | Fetch Workday detail API records                   | None                   |

---

## Crawler: Worker Pipeline

Workers run a fixed internal pipeline claiming from Redis queues.

```
src/workers/pipeline.py     # Discovery coroutines, claim from Redis, dispatch
```

### HTTP Worker (`crawler run`)

Claims from `ready:simple:*` queues. Processes both monitors and scrapes. 3 replicas on the current Hetzner deployment, each allocated 1 CPU and 1GB memory.

### Browser Worker (`crawler run-browser`)

Claims from `ready:browser:*` queues. Same pipeline pattern but with Chromium available. 1 replica with 3 CPUs and 6GB memory. Each discovery coroutine reuses one Playwright driver between claims and recycles it after six hours to bound long-lived driver and renderer memory growth.

### Processing Flow

All monitors use the streaming path (`_process_one_board_streaming` in `processing/board.py`):

1. Monitor discovers jobs (yields batches for large datasets)
2. Diff against local Postgres in a single SQL query (new/touched/relisted,
   including canonical recovery from foreign-board liveness evidence, then
   gone)
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
src/exporter.py    # CDC: local Postgres -> Typesense
```

The exporter queries local Postgres with a commit-safe `(updated_at, id)`
cursor and publishes posting documents to Typesense. The production command
never opens the former relational mirror. The separately scheduled reconciler
performs bounded, fenced, Typesense-only repairs from a locked local snapshot.

### Export Loop

- Polls every 1-2 seconds
- Batch size: 2000 rows per tick
- Throughput: ~2100 rows/sec sustained
- Latency: ~1.5s average (change to visible downstream)

### What Gets Exported

- `job_posting`: all display columns (titles, locales, locations, employment type, salary, enrichment, etc.)
- Board status is not exported; `job_board` registry rows come from `sync.py`.

### Reconciliation

An hourly Hetzner systemd timer resumes deterministic 1/256 UUID partitions
from durable local PostgreSQL state and repairs Typesense to the exact local
document set. The cursor advances only after direct repair is verified. This
survives exporter recreation and does not mutate local posting timestamps.

CLI: `crawler reconcile [--repair] [--full] [--max-partitions N] [--target typesense]`

See [Cross-store reconciliation](03-crawler-architecture.md#cross-store-reconciliation)
for locking, scheduling, alerting, and recovery contracts.

---

## Crawler: Proxy-Routed Transport

Optional proxy routing for boards whose origins block Hetzner datacenter
IPs (AWS WAF captchas, Cloudflare challenges, etc.).

```
src/shared/proxy.py    # Webshare round-robin, quarantine, and recovery
src/shared/http.py     # Per-request rotating httpx transport
```

Implementation and config notes live in the canonical doc:
[apps/crawler/AGENTS.md § Proxy-routed transport](../apps/crawler/AGENTS.md#proxy-routed-transport).

Quick summary:

- Per-board opt-in via `"proxy": true` in `monitor_config` and/or
  `scraper_config` JSON in `data/boards.csv` (the two flags are
  independent; typically both are set for a WAF-blocked host).
- `webshare` is the sole provider; `PROXY_PROVIDER=none` is the explicit
  direct-egress switch. A selected provider without endpoints fails closed.
- Plain HTTP rotates per completed top-level request, keeping redirects on the
  same slot. A browser launch is selected against its planned target origin
  and keeps one slot for document and subresources to preserve anti-bot egress
  affinity.
- Global proxy faults and origin-specific blocks enter bounded exponential
  quarantine, then generation-owned one-at-a-time half-open recovery; stale
  concurrent responses cannot reopen a newer circuit.
- The current plan is bandwidth-metered. `WEBSHARE_PROXY_URLS` uses per-proxy
  backbone credentials that remain valid across the 30-day direct-IP refresh.
- `rescrape_policy: "never"` in `monitor_config` disables the 24h
  refresh tail for WAF-blocked boards whose content rarely changes —
  conserving bandwidth, origin pressure, and connection slots.
- `crawler proxy-configure-webshare` backs up and atomically updates a local
  env file; `crawler proxy-audit` checks plan, pool, usage, and client-source
  anomalies without emitting credentials or IPs. Historical source evidence
  is explicitly inconclusive when Webshare's six-day activity retention or a
  plan-upgrade boundary clips the requested window. The account API key is
  never forwarded to runtime containers.

---

## Crawler: CSV Sync

```
src/sync.py    # CSV -> DB upsert
```

CSV files are the source of truth. `sync.py` writes to three targets in one pass:

1. **Local Postgres**: full board config (all columns), companies, and taxonomies
2. **Redis**: board config and initial schedule in ready queues
3. **Typesense**: taxonomy collections, the `company` collection (incl. per-locale description and industry name variants used by the company detail page), and the `watchlist` collection. `crawler setup-typesense` runs on each deploy and patches the live schema in place before sync upserts populate new fields -- see [docs/11-typesense.md](./11-typesense.md#schema-definition)

- **New rows**: Inserted with staggered `next_check_at` (random offset to prevent thundering herd)
- **Existing rows**: Config updated, runtime fields preserved
- **Removed rows**: Disabled (`is_enabled = false`), not deleted

### Read paths (which database serves which page)

See [docs/11-typesense.md#read-paths-summary](./11-typesense.md#read-paths-summary) for the full breakdown. Short version:

- **Job search, typeaheads, browse-all, watchlist search, company detail, similar-company strip** → Typesense
- **Auth, watchlist mutations, and watchlist company-pair lookups** → web-owned Postgres
- **All `job_posting` aggregations** (active counts per company, per taxonomy, per watchlist) → Local Postgres, then upserted into Typesense doc fields. Web pages never aggregate `job_posting` directly

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
| `apiLimiter`         | 60 seconds   | 30    | Origin executions of public `/api/v1/*` GETs |

The public REST limiter is the defense-in-depth origin layer. Successful
deterministic responses cache only on Vercel for 5 minutes or 1 hour, so CDN
hits do not execute it. A production `jseek.co` WAF group matches
`GET /api/v1/*` before CDN lookup and shares the project's existing fixed
60/minute IP budget with public-read Server Actions. Hobby permits only one
rate-limit rule per project. Errors and origin 429s are `no-store`; successful
shared responses omit caller-specific `X-RateLimit-*` headers.

---

## Web: Public API Metrics

```
src/lib/public-api-metrics-contract.ts  # Bounded dimensions and v1 Redis key contract
src/lib/public-api-metrics.ts           # Fail-open aggregate writer
script/report-api-traffic.ts            # Operator 7/30/90-day report
```

The public REST and MCP surfaces write privacy-bounded daily aggregates to
the existing Upstash Redis database after the response has been produced.
These counters measure **origin executions only**: Vercel CDN hits that never
reach a Function and requests stopped by the WAF are absent. Hosted MCP
consumer attribution is likewise origin-only; a cached REST response is
anonymous and records no consumer event. These counters complement, but do
not replace, Vercel Firewall edge totals.

Each request increments one canonical low-cardinality hash field containing
only the enumerated interface, route, consumer class, status class, latency
bucket, rate-limit flag, and whether an HLL write was issued. Query strings,
bodies, tool arguments, raw errors, user agents, referrers, geography, and raw
IP addresses are never persisted.

External requests with a valid platform-authoritative IP also update a daily
HyperLogLog. Its member is
`HMAC-SHA256(API_METRICS_HMAC_SECRET, UTC-day + NUL + IP)`, so neither the raw
IP nor a reusable plain hash reaches Redis. Hosted-MCP downstream REST calls
and unknown/invalid IPs do not enter an HLL. HLL results estimate daily
network clients—not people, accounts, organizations, or cross-day uniques.
The report therefore sums them only as **network-client-days**.

Writes are fail-open and pipelined. A successful counts-only write issues two
logical Redis commands (`HINCRBY`, `EXPIREAT`); an eligible external request
issues four by adding `PFADD` and a second `EXPIREAT`. Every key expires at the
fixed UTC day start plus 90 days. Redis or configuration failures emit one
sanitized `api_metrics.unavailable` runtime event and never change the API
response. Because a failed Redis write cannot reliably record its own failure,
the Redis report exposes `telemetry_write_failures: null`; runtime logs remain
the only source for that event.

The report's `logical_metric_write_commands` is calculated from retained count
fields and their bounded `network_client` dimension. It is not provider
billing or a measured Upstash command total. A failed pipeline can leave no
count, and an Upstash pipeline is not atomic, so a partial pipeline can differ
from the logical two/four-command path represented by the retained field.
Provider plan limits, utilization, and storage remain `null` until separately
measured and recorded by operations.

### Production secret provisioning and rotation

`API_METRICS_HMAC_SECRET` and `HOSTED_MCP_API_PROVENANCE_TOKEN` are separate
secrets with independent purposes, owners, and rotation schedules. Never reuse
one value for the other.

| Secret | Purpose and owner | Conservative degradation | Rotation rule |
|--------|-------------------|--------------------------|---------------|
| `API_METRICS_HMAC_SECRET` | Telemetry/privacy owner; derives daily non-reversible HLL members | Counts continue, HLL writes are skipped, and `api_metrics.unavailable` is emitted; raw IPs are never stored | Rotate independently at a UTC day boundary so one day's HLL is not split across keys derived from two secrets |
| `HOSTED_MCP_API_PROVENANCE_TOKEN` | Web/MCP operations owner; proves that a REST call came from the hosted MCP bridge | Missing, empty, or mismatched values classify the REST call as `external`; the marker grants no authorization or rate-limit bypass | Rotate independently but deploy the hosted MCP sender and REST verifier with the same new value |

Provision both as protected Vercel **Production** environment secrets before
deploying the instrumentation. Use distinct values per environment, keep them
out of committed env files and logs, and verify only variable presence—not
values—during release checks. The `turbo.json` environment allowlist merely
permits named variables to reach tasks; it does not create, encrypt, rotate, or
provision either secret.

Deployment record: on 2026-08-28, both variables were provisioned for Vercel
Production as **Sensitive** values. Their values were not copied into this
repository, retained in investigation notes, or printed during verification.

After deployment, run these bounded probes:

1. Send one direct REST request and one hosted MCP tool call that reaches REST.
2. Confirm runtime logs contain the expected bounded `public_api.request`
   events, including `external` for the direct call and `hosted_mcp` for the
   downstream MCP call, with no `api_metrics.unavailable` event.
3. Run `traffic:api` with `--through` set to the current UTC date. Confirm the
   day is marked partial, both consumer classes increment, and only external
   traffic contributes to the REST network-client HLL estimate.
4. Inspect the daily counts and HLL keys in the Upstash console: expiry must be
   the UTC day start plus 90 days, and fields/members must not expose an IP,
   query, argument, token, or secret.

```bash
pnpm --dir apps/web traffic:api --since 7d
pnpm --dir apps/web traffic:api --since 30d --json
pnpm --dir apps/web traffic:api --since 90d --through 2026-08-27 --env-file .env.local
```

The default `--through` is the last completed UTC day. Missing, corrupt, and
explicitly included partial UTC days are reported rather than silently
treated as zero.

### Web Upstash key-family registry

| Key Pattern | Type | Retention | Purpose |
|-------------|------|-----------|---------|
| `session:{token}` | String | 5 minutes | Cross-instance authenticated-session cache |
| `rl:*` | Upstash Ratelimit keys | Limiter-defined | Auth, public API, and public-read rate limits |
| `cache:*` | String | Call-site-defined | Web cache-aside values |
| `metrics:public-api:v1:counts:YYYY-MM-DD` | Hash | Fixed UTC day start + 90 days | One bounded aggregate field per REST/MCP origin execution |
| `metrics:public-api:v1:clients:YYYY-MM-DD:rest:external` | HyperLogLog | Fixed UTC day start + 90 days | Daily external REST network-client estimate |
| `metrics:public-api:v1:clients:YYYY-MM-DD:mcp:external` | HyperLogLog | Fixed UTC day start + 90 days | Daily external MCP network-client estimate |

### Upstash baseline and approved-ceiling record

The 2026-08-28 operations probe established the Redis-side point-in-time
baseline below. Redis's `total_commands_processed` is a lifetime counter since
the server started; it is **not** month-to-date usage and provides no billing-
period boundary. The signed-in provider dashboard was unavailable, so the
plan, current billing-period commands/requests, monthly allowance, and
configured read regions could not be verified.

| Field | Operations-supplied value | Evidence/notes |
|-------|---------------------------|----------------|
| Measurement date | 2026-08-28 | Redis-side point-in-time operations probe; no billing-period window available |
| Database key count (`DBSIZE`) | 19 | Redis-reported |
| Used memory | 924 B | Redis-reported `used_memory` |
| Configured maximum memory | 3 GB | Redis-reported `maxmemory` |
| Lifetime commands processed | 1,591,460 | Redis-reported `total_commands_processed`; lifetime only, not MTD |
| Replicas | None reported through available `INFO` | Does not establish configured provider read regions |
| Provider plan | Unavailable | Signed-in provider dashboard unavailable |
| Current billing-period commands/requests | Unavailable | Cannot be derived from the lifetime Redis counter |
| Monthly command/request allowance | Unavailable | Signed-in provider dashboard unavailable |
| Configured read regions | Unavailable | Signed-in provider dashboard unavailable; Redis `INFO` is insufficient |
| Approved incremental metric ceiling | Provisionally 25,000 logical commands/month | 22,440 worst-case monthly logical commands at the measured traffic rate plus approximately 11% headroom |
| Approval condition | Current MTD usage + 25,000 must remain below the provider limit | Must be verified from provider-owned billing-period data before treating the ceiling as available headroom |
| 24-hour post-deploy command/storage measurement | Pending | Compare provider usage with the Redis logical report once dashboard access is available |
| 7-day post-deploy command/storage measurement | Pending | Confirm command headroom or disable metric writes |

At 19 keys and 924 B used, incremental aggregate storage is negligible against
the Redis-reported 3 GB maximum; the provider command allowance is the binding
unknown. Metric writes must remain fail-open regardless of the eventual plan
or ceiling. If measured provider usage threatens the provisional ceiling,
disable or reduce metric writes; never impair API/MCP responses or weaken the
existing API rate limiter to preserve telemetry.

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
- **Web-owned Postgres**: user/auth/watchlist data plus temporarily retained
  rollback support tables. It is not a crawler posting mirror target.

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

Local Postgres has the authoritative full schema (scheduling, config, state).
The retained web-owned board subset is rollback support and is no longer
updated by normal crawler sync.

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
data/boards.csv    --+  sync.py  -->  Local Postgres transaction --> Redis + Typesense

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
        +-- Batch upsert to Typesense
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
