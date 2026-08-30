# Typesense Deployment State

Current production deployment as of July 2026. The earlier docs in this directory (00-05) describe the migration plan and benchmarks; this document describes what was actually deployed.

## Infrastructure

### Typesense Machine

- **Hetzner CX22**: 4 GB RAM, 2 vCPU, dedicated IPv4
- **OS**: Ubuntu (Docker host)
- **Container**: Typesense 27.1 pinned by manifest digest in the host installer,
  `--network host`, data at
  `/mnt/typesense-data`; the only command argument is the path to a read-only
  config file
- **Port**: 8108
- **Firewall**: SSH from anywhere, port 8108 from private network only (10.0.0.0/16)
- **Backups**: daily application-consistent Snapshot API backup to an encrypted,
  home-isolated Storage Box repository; see
  [`19-data-backup-recovery.md`](19-data-backup-recovery.md)

### Private Network (Hetzner 10.0.0.0/16)

All machines communicate over Hetzner's private network. Actual IPs are in `apps/crawler/.env.local`.

| Role | Description |
|------|-------------|
| Typesense box | Typesense 27.1 + Cloudflare tunnel |
| Postgres box | Local Postgres (source of truth) |
| Crawler box | Workers, exporter, drain, Redis, Alloy |

Crawler connects to Typesense over the private network (`http://<TYPESENSE_PRIVATE_IP>:8108`), no TLS needed.

### Cloudflare Tunnel

The Vercel-hosted web app has no stable IPs, so it cannot be firewalled into the private network. Instead, a Cloudflare tunnel exposes Typesense to the web app.

- **Hostname**: `typesense.colophon-group.org`
- **Routes to**: `localhost:8108` on the Typesense machine
- **Daemon**: `cloudflared` running as the dedicated unprivileged
  `cloudflared` user, auto-starts on reboot. The root-only token source is
  delivered through systemd `LoadCredential`; neither the unit nor process
  arguments contain the token.
- **Cache bypass rule**: configured in Cloudflare dashboard -- without it, Cloudflare may cache GET search responses and return stale results (Typesense does not set `Cache-Control` headers by default)
- **Rate-limit rule** (zone `colophon-group.org`, phase `http_ratelimit`): per-IP, 200 requests / 10 s on `(http.host eq "typesense.colophon-group.org")`, action `block` for 10 s. Required because the search key is exposed to browsers (see "Web App Integration") and the origin is a single 4 GB / 2 vCPU box.
- **CORS**: Typesense container emits `Access-Control-Allow-Origin: *` directly -- no Cloudflare Transform Rule needed. Verified via `curl -X OPTIONS -H 'Origin: https://jseek.co' https://typesense.colophon-group.org/health`.
- **Latency overhead**: ~10-30 ms per request (acceptable -- Typesense queries take <10 ms)

## API Keys

The server bootstrap key and five generated keys have separate owners and
lifecycles. Values are stored only where their consumer needs them: protected
GitHub environment secrets for CI/CD, root-only host files, protected Vercel
environment variables for the web app, and ignored developer
`apps/crawler/.env.local` files when local access is required. They are never
part of a Git branch.

| Environment Variable | Scope | Used By | Connection Path |
|---------------------|-------|---------|-----------------|
| `TYPESENSE_BOOTSTRAP_KEY` | Server bootstrap/full access; not a generated application key | Starts Typesense only; root-owned host deployment | Root-only `/etc/jobseek-typesense/typesense-server.ini` |
| `TYPESENSE_OPERATIONS_KEY` | Generated key: `collections:*`, `documents:*`, `aliases:*` on all collections, plus `metrics.json:list` | Exporter, sync, backfill, setup, reconciliation, health metrics | Private network (crawler -> Typesense) |
| `TYPESENSE_BACKUP_KEY` | Generated, revocable wildcard key | Root-owned Typesense Snapshot API backup service only | Loopback on Typesense host |
| `TYPESENSE_SEARCH_KEY` | `documents:search` + `documents:get` on all collections | Web app server-side search (server actions) | Cloudflare tunnel |
| `TYPESENSE_BROWSER_PARENT_KEY` | `documents:search` only on `job_posting`, `company`, `location`, `occupation`, `seniority`, and `technology` | Web app `/api/typesense-key` route handler -- mints scoped keys for direct browser->Typesense calls | Cloudflare tunnel (browser, scoped key) |
| `TYPESENSE_WRITE_KEY` | `documents:create/upsert/delete/update` on `watchlist` collection only | Web app watchlist mutations | Cloudflare tunnel |

`TYPESENSE_BROWSER_PARENT_KEY` is a separate parent because Typesense rejects scoped keys derived from a parent that has any actions other than `documents:search` (the server returns `Forbidden - a valid x-typesense-api-key header must be sent.` when used with a multi-action parent).
The six named collections are the complete direct-browser read set. In
particular, the browser watchlist path searches `job_posting`; it does not read
the `watchlist` collection. Do not grant the parent wildcard collection access.

Browser scoped keys embed a server-enforced `expires_at` Unix timestamp ten
minutes in the future. The search-only scope is identical for every visitor,
so `/api/typesense-key` deliberately does not read session state; reintroducing
an auth lookup would make a shared response vary per viewer and pull the auth,
database, and Redis dependency graph into this hot Function. The endpoint
returns the signed boundary in milliseconds. Vercel's CDN caches the shared
response for 510 seconds with a hard `max-age` freshness boundary, leaving 90
seconds before key expiry, while the browser refreshes 30 seconds early. A
valid response is also kept in
`localStorage` until that refresh boundary so reloads and new tabs do not mint
another key. The scoped child is short-lived and the parent key never leaves
the server.

### Browser parent-key rotation

Rotate `TYPESENSE_BROWSER_PARENT_KEY` after deploying an expiry-policy change
and on the normal credential-rotation schedule:

1. Create a generated parent with the sole action `documents:search`, exactly
   the six collections above, and an expiry later than any scoped child expiry.
2. Replace the protected Vercel `TYPESENSE_BROWSER_PARENT_KEY` value and deploy
   the web app.
3. Fetch `/api/typesense-key`, decode its scoped payload to confirm
   `expires_at`, and perform a browser-path search with the new child key.
4. Delete the old parent in Typesense. Confirm a child captured from the old
   parent now receives HTTP 401; revoking a parent invalidates all children
   derived from it. Browser search helpers clear a persisted child on that 401,
   so the affected operation can use its server fallback and the next browser
   operation fetches a child derived from the replacement parent.

Never delete the old parent before the new web deployment is serving keys.

Typesense 27.1 does not accept a generated key restricted to
`operations:snapshot` or `operations:*` for `POST /operations/snapshot`; a
live negative authorization test returned 401 for both. The backup consumer
therefore has a generated wildcard key as a documented version-specific
exception. It remains revocable and is confined to the root-owned backup
environment; it is never shared with crawler or web workloads. Re-test a
snapshot-only scope when Typesense is upgraded.

The repository-owned host deployment is
`.github/workflows/deploy-typesense-host.yml`. Pushes validate its shell,
systemd, conformance, and a real Typesense 27.1 config-file smoke test, but do
not mutate production. An operator explicitly dispatches `typesense`,
`cloudflared`, or `all`. The installer requires recent successful Typesense
backup evidence before a Typesense handoff, refuses to overlap an active
backup, pulls the image before downtime, skips conformant services, and
restores prior credential files and services when a health gate fails. See
[`16-hetzner-maintenance.md`](16-hetzner-maintenance.md#typesense-host-credentials)
for rotation and verification.

## Collections

7 collections, all using versioned names with aliases for zero-downtime reindexing:

| Collection | Alias Target | Doc Count (approx) | Purpose |
|------------|-------------|-------------------|---------|
| `job_posting` | `job_posting_v1` | ~1M | Main search, faceted filtering |
| `location` | `location_v1` | ~10K | Location typeahead |
| `occupation` | `occupation_v1` | ~400 | Occupation typeahead (per-locale docs) |
| `seniority` | `seniority_v1` | ~40 | Seniority typeahead (per-locale docs) |
| `technology` | `technology_v1` | ~500 | Technology typeahead |
| `company` | `company_v1` | ~1K | Company typeahead + browse |
| `watchlist` | `watchlist_v1` | varies | Public watchlist search |

### Key Design Choices

- **Ancestor IDs**: Typesense `job_posting` documents store `location_ids` and `occupation_ids` as **ancestor-expanded arrays** (leaf ID + all parent/grandparent IDs + macro region IDs). This enables hierarchy-free filtering -- searching for "Germany" matches all cities in Germany without recursive joins.

  **Design rule: Postgres stores leaf IDs only; the exporter expands to ancestors at indexing time.** Do NOT expand ancestors in the crawler processing pipeline (`_resolve_locations_sync`, `_resolve_locations`). Postgres `location_ids` and `location_types` must remain parallel arrays of the same length. Ancestor expansion adds extra IDs without matching type entries, breaking this database invariant.

  **Where ancestor expansion happens (exporter only):**
  - `exporter.py` → `TaxonomyMaps.location_ancestors`: walks `location.parent_id` chain + `location_macro_member` (macro regions like EU, DACH). Populates `location_ids` on Typesense documents.
  - `exporter.py` → `TaxonomyMaps.occupation_ancestors`: walks `occupation.parent_id` chain. Populates `occupation_ids` on Typesense documents.
  - The backfill script (`typesense-backfill-local.py`) must use the same logic.

  **Invariant**: `buildFilterString()` in the web app filters on `location_ids` and `occupation_ids` (plural array fields). If only leaf IDs reach Typesense, hierarchy filtering silently breaks (filtering by "Germany" won't match "Berlin"). If ancestors are written to Postgres instead, the `location_ids`/`location_types` length constraint breaks local writes.
- **Direct location IDs**: `job_posting.location_direct_ids` preserves the
  unexpanded source IDs alongside `location_ids`. Company location summaries
  facet the direct field so a city is not counted again as its country or macro;
  search filters continue to use the ancestor-expanded field.
- **Taxonomy read contract**: taxonomy documents carry the identity and
  hierarchy metadata required by web readers. Location documents include the
  indexed `slug`, `parent_id`, `ancestor_ids` (self + geographic ancestors +
  macros), localized display names/aliases, and `member_country_ids` on macro
  rows. Occupation documents include indexed `slug`, `parent_id`, `domain_id`,
  and `domain_slug`; seniority slugs are indexed. This metadata is a producer
  contract, not an optional display optimization: the web app does not query
  the Supabase crawler mirror to reconstruct it.
- **Sentinel values**: `experience_min_years = -1` for NULL (and legacy `experience_min = -1` during the integer-field compatibility window). `locales = ["_none"]` for jobs with no detected language.
- **Experience precision**: Postgres stores `experience_min` / `experience_max` as decimal years (`NUMERIC(3,1)`), so sub-year requirements such as "6 months" index as `0.5`. Typesense filters use `experience_min_years` / `experience_max_years` float fields, while legacy integer `experience_min` / `experience_max` fields remain for backfill compatibility.
- **Denormalized names**: Taxonomy names (location, occupation, seniority, technology) are stored directly on each job posting document for search and faceting without joins.
- **Versioned aliases**: `job_posting` is an alias pointing to `job_posting_v1`. To reindex with a new schema: create `_v2`, backfill, swap alias, drop `_v1`.

### Schema Definition

Collection schemas are the single source of truth in `apps/crawler/src/typesense_schema.py` (`COLLECTIONS`). Two callers:

- `scripts/typesense-setup.py` -- operator-facing wrapper for dev workflows.
- `crawler setup-typesense` CLI subcommand -- exposed inside the crawler image so `deploy.sh` can patch the live cluster on every deploy.

Both are idempotent. On every run, the setup logic:

1. Creates any missing collection + alias (initial setup).
2. PATCHes existing collections to add fields that appear in `COLLECTIONS` but not on the live cluster -- via `client.collections[name].update({"fields": [...]})` against Typesense's alter API. The implicit `id` field is filtered from the diff: Typesense never returns it from `retrieve()['fields']`, so a naive name-based diff would always flag it missing, and PATCH on `id` is rejected with 400 `Field \`id\` cannot be altered`.
3. Repairs `index` drift with Typesense's documented drop-and-re-add field pair. Stored values remain in documents. Existing fields are altered one per PATCH because the operation is synchronous, blocks writes, and may scan the full collection.
4. Never removes stored fields or auto-repairs other field-shape drift.

```bash
cd apps/crawler && uv run python ../../scripts/typesense-setup.py         # Idempotent: create + patch
cd apps/crawler && uv run python ../../scripts/typesense-setup.py --force  # Drop + recreate (data loss)
uv run --no-sync crawler setup-typesense                                   # Same, from inside the image
```

The deploy script (`apps/crawler/deploy.sh`) stops workers, exporter, drain, and
browser before running Alembic, `crawler setup-typesense`, and `crawler sync`.
Keeping the whole schema/runtime transition in one quiescence window prevents
an old local-Postgres writer or exporter from crossing the commit-safe CDC
trigger cutover, keeps schema patching ahead of `sync` upserts, and avoids a
Redis reseed race with live workers. Typesense itself remains online and serves
reads throughout an in-place schema alter. Setup uses a one-hour request
timeout, a two-hour total alter deadline, and logs allocated/active/resident
memory before and after the schema series; the SSH deploy permits three hours.
The deploy workflow also smoke-runs `setup-typesense` twice against an ephemeral
Typesense container before SSHing to prod (the second run exercises the patch
path on existing collections), so a schema regression fails CI rather than
aborting the deploy mid-stream. BuildKit's crawler and browser manifest
digests, rather than their version tags, are passed into production Compose.
The deploy rejects missing, malformed, or mutable image references, keeps a
verified crawler-confirmed snapshots of `/home/deploy/.env` and the active
Compose contract, verifies
each running container against the candidate digest manifest, and only then
publishes the atomic success marker. Version and `latest` tags remain discovery
and compatibility aliases; the workflow promotes `latest` only after the
digest-addressed SSH deployment succeeds.

### Job-posting stored-only compatibility fields

The posting schema keeps these values in each document but deliberately omits
their in-memory search indexes:

| Field | Retained purpose | Indexed replacement |
|------|------|---------|
| `occupation_id` | leaf occupation in exporter/reconciliation payloads | `occupation_ids` for hierarchy-aware filters/facets |
| `occupation_name` | exporter/reconciliation compatibility | taxonomy collection / `occupation_ids` |
| `last_seen_at` | CDC compatibility and direct-retrieval diagnostics | none; no search path consumes it |

These are response-compatible changes: direct document retrieval and normal
search hits still include the stored values. Filtering, faceting, grouping,
sorting, or adding the fields to `query_by` is intentionally unsupported. See
[the 2026-08-26 footprint investigation](typesense-footprint-investigation-2026-08-26.md)
for consumer tracing, benchmark evidence, and the fixed-capacity rollout.

### Company Collection (extended for company detail page)

The `company` collection doubles as the source for the company detail page (see [Read paths](#read-paths-summary)) and therefore carries fields beyond what typeahead/browse needs:

| Field | Type | Purpose |
|------|------|---------|
| `id`, `name`, `slug`, `icon` | scalar | shared with typeahead |
| `logo`, `website`, `employee_count_range`, `founded_year` | scalar | detail page facts |
| `description` | string (en) | fallback when no per-locale variant |
| `description_de`, `description_fr`, `description_it` | string | per-locale variants from `company_description`; reader falls back to `description` |
| `industry_id`, `industry_name` | scalar | en industry name from `industry.name` |
| `industry_name_de`, `industry_name_fr`, `industry_name_it` | indexed string | per-locale display names from `industry_name`; queried together with English by industry browse/search and therefore cannot be `index: false` |
| `active_posting_count`, `year_posting_count` | int32 | counts (refreshed by `refresh-typesense`) |

## Indexing Pipeline

### Job Postings (CDC via exporter.py)

The production exporter is Typesense-only. `crawler export` opens no
relational-mirror connection and owns only the `typesense:job_posting` keyset
cursor, regardless of an ambient developer `DATABASE_URL`.

On each tick:

1. Capture the database clock and the oldest current posting-writer
   transaction start in one non-blocking query, using the earlier value as the
   commit-safe cutoff.
2. SELECT changed postings strictly before the cutoff and after the Typesense
   cursor.
3. Denormalize + expand ancestor IDs and upsert to Typesense.
4. Require an acknowledgement list with exactly one object containing an
   explicit boolean `success` per submitted document before advancing the
   Typesense cursor. A well-formed rejected document follows the logged
   poison-document policy; an empty, truncated, overlong, or malformed
   acknowledgement pins the batch and enters bounded retry backoff.

The Typesense document builder (`_build_typesense_docs`) expands `location_ids` and `occupation_ids` with all ancestor IDs using pre-loaded hierarchy maps (`TaxonomyMaps.location_ancestors`, `occupation_ancestors`). This means even legacy Postgres rows with leaf-only IDs produce correct hierarchy-filterable Typesense documents.

No Supabase cursor is loaded, written, or considered when selecting rows.
The exporter/operator fence is held across the mutable-row read, downstream
upserts, and cursor save so an operator repair cannot race past the cursor. See
[`03-crawler-architecture.md`](03-crawler-architecture.md#commit-safe-posting-cdc)
for the trigger contract, writer-floor delay alert, and deployment ordering.

**Feature flag**: Typesense writes only happen when
`TYPESENSE_OPERATIONS_KEY` is set (non-empty). Environments without Typesense
cannot run the production exporter. The env var must be passed to containers
in `docker-compose.yml` (`x-common-env`).

**Denormalization**: The exporter's `TaxonomyMaps` reads all lookup data from
**local Postgres** (the source of truth). Company info, location names,
occupation names, seniority names, and technology names are all loaded from
local; there is no Supabase fallback. All ancestor chain computation
(locations + macro regions, occupations) uses local Postgres data exclusively.

### Taxonomy Collections (via sync.py)

After the authoritative local Postgres transaction commits, `sync.py` reads
local data to populate the location, occupation, seniority, technology, and
company collections. It does not read the transitional Supabase mirror for
these documents. Includes:

- `active_posting_count` and `has_active_postings` for each taxonomy entry
- localized taxonomy display names and aliases, stable slugs, parent/domain
  hierarchy, and macro-region membership used by the web taxonomy provider
- Taxonomy rename detection: if a name changes in CSV, affected job posting documents in Typesense are updated with the new denormalized name

`crawler sync` does not open `DATABASE_URL`, and the production CLI no longer
exposes a mirror selector. Watchlist reconciliation remains a separate
web-owned read through `WEB_DATABASE_URL`.

### Count Refresh + Watchlist Reconciliation

```bash
uv run crawler refresh-typesense
```

- Refreshes `active_posting_count` / `has_active_postings` on all retained
  taxonomy and company documents. Retained document IDs and localized
  occupation/seniority variants come from the bounded local Postgres
  authorities; counts come from exhaustive Typesense facets. IDs absent from a
  valid facet are explicitly reset to zero/false.
- Reconciles the `watchlist` collection against the web-owned database selected
  by `WEB_DATABASE_URL` (upserts missing, deletes stale). Company membership
  comes from `watchlist_company`; `active_job_count` comes from one exhaustive
  `company_id` facet over the canonical web-visible posting filter
  (`is_active:true && has_content:!=false`) and is summed per watchlist in
  Python. A company absent from a valid facet contributes zero, while
  `company_count` remains the number of membership rows. Only explicit
  sync/count-refresh jobs receive the web credential; long-running crawler
  services receive no web-owned database URL.
- Validates exact per-document Typesense import acknowledgements before
  continuing. A rejected, malformed, or truncated acknowledgement aborts the
  command, records a failed cron run, and blocks dependent watchlist pruning.

**When it runs in production** (two paths, both version-controlled):

1. **Every deploy / CSV merge — inline.** `crawler sync` calls `refresh_typesense_counts()` as its last step (`apps/crawler/src/sync.py`), so every run of `.github/workflows/deploy-crawler-browser.yml` and `.github/workflows/sync-data.yml` does a refresh.
2. **Every 4 hours — out-of-band.** `.github/workflows/crawler-scheduled-maintenance.yml` SSHes to the crawler host and runs `crawler refresh-typesense` as a `docker run --rm` one-shot. Keeps counts fresh between deploys.

### Full Re-index (Backfill)

```bash
uv run crawler backfill-typesense    # Production: reads from local Postgres only
```

Production backfills are dispatched manually through
`.github/workflows/crawler-scheduled-maintenance.yml`. The workflow holds the
same concurrency lock for the entire re-index and refuses to start while an
older Typesense maintenance container is still running. Crawler deploys also
refuse to overlap these containers because their inline sync refreshes
Typesense counts. Each idempotent bulk upsert is retried with bounded
exponential backoff. If Typesense still rejects or times out a batch, the
command fails without advancing the scan or CDC cursor past that batch;
operators must fix the downstream failure and rerun the backfill before
running `refresh-typesense`.

The production dispatch requires the exact 40-character live crawler revision.
Before mutation it verifies that `/home/deploy/.env` records that revision and
an immutable image digest, that exactly one exporter uses the same image, and that
the exporter has no relational-mirror credential. One fail-closed container
then runs the following chain while the host mutation lock remains held:

```bash
uv run --no-sync crawler backfill-typesense && \
uv run --no-sync crawler reconcile --repair --full --fresh-cycle --target typesense && \
uv run --no-sync crawler verify-typesense-taxonomies
```

The fresh reconciliation restarts durable progress at bucket 0, repairs and
verifies all 256 posting buckets, and rejects an incomplete summary. The final
gate reads one repeatable-read local PostgreSQL snapshot and compares every
document and every static consumer-facing field in `location`, `occupation`,
`seniority`, `technology`, and company-industry data. It also validates the
live schema, including location hierarchy fields and indexed localized
industry names. Evidence contains exact counts and projection hashes plus a
bounded, ID-hashed mismatch list; it never emits row values. Posting-derived
taxonomy/company counts are excluded from this static gate and remain the
responsibility of `refresh-typesense`.

`location_direct_ids` is populated by the exporter for new changes, but adding
the field does not rewrite older posting documents. Roll out this read contract
in order: deploy/run `crawler setup-typesense`, run `crawler sync` to rewrite
taxonomy documents, complete `crawler backfill-typesense`, and only then deploy
the web reader. Verify the backfill before enabling the reader; there is no
Supabase crawler-mirror fallback for missing contract fields.

For local development/testing only:
```bash
cd apps/crawler && uv run python ../../scripts/typesense-backfill-local.py [--limit N]
```

### Posting Reconciliation

Posting parity is not inferred from collection counts or a random sample. The
deploy-independent crawler-host timer runs the deterministic reconciler
documented in
[`03-crawler-architecture.md`](03-crawler-architecture.md#cross-store-reconciliation).
Each posting document contains an optional indexed
`reconciliation_bucket` equal to the UUID high byte. The field is optional
only for the additive rollout; the exporter, backfill, and reconciler write it
on every upsert.

Normal runs export one bounded bucket at a time and compare exact IDs,
`is_active`, and a defined set of user-visible fields. Payload comparison uses
canonical in-process fingerprints of the actual exported fields; it does not
trust a checksum stored beside the document. Unordered arrays/maps are
canonicalized deterministically, while positional location/technology order is
retained so mispaired fields cannot compare equal. Only exact unique detected
counts plus aggregate payload mismatch counts enter durable state or telemetry.
Missing/mismatched documents are rebuilt with the ordinary
denormalization path and verified again before the cursor advances, while
Typesense-only documents are deleted. During the first complete repair cycle,
all authoritative local documents are upserted before a streamed unbucketed
cleanup; cleanup fails closed if any unbucketed ID still exists locally. No
Typesense restart, collection rebuild, or search downtime is required. The
design rationale and exact bounded contract are in
[`03-crawler-architecture.md`](03-crawler-architecture.md#payload-comparison-design).

## Web App Integration

- `TypesenseSearchProvider` implements the `SearchProvider` interface, replacing `PostgresSearchProvider` (one-shot cutover)
- All search, typeahead, browse-all modals, and watchlist search go through Typesense
- **Posting detail**: `getPostingDetail` retrieves the posting plus company/location/seniority documents from Typesense and builds the R2 description URL. Original salary amount, currency, and period are denormalized onto the posting document for this reader.
- **Saved jobs**: `saved_job` owns an immutable posting/company snapshot. The snapshot is populated from Typesense for new saves and backfilled by migration for existing saves, so application history survives later removal of the Supabase `job_posting` mirror. List/detail readers refresh current `is_active` in one bounded Typesense query and retain the snapshot value for missing hits or outages.
- **Company- and watchlist-facing reads**: company autocomplete, watchlist company search, public watchlist discovery, watchlist posting lists/counts, the progress-page company/posting counters, and `getCompanyBySlug` read Typesense. They do not fall back to the Supabase crawler mirror. Authenticated watchlist metadata, company membership, and mutations remain in the web database
- **Location/taxonomy reads**: filter-chip slug resolution, descendant
  expansion, browse-all location/occupation/seniority/technology metadata,
  industry suggestions, and company location summaries read the taxonomy,
  company, and posting collections. They do not read the Supabase crawler
  mirror.
- **Graceful degradation**: optional company/location/taxonomy browse surfaces
  return their empty shape only when Typesense is unavailable, and the catch is
  outside the cache boundary so an outage-shaped empty is not stored. Exact
  slug resolvers and unexpected errors (including rate limits and schema/query
  errors) propagate. Company detail degrades to not found and logs an actual
  outage. Public watchlist discovery and posting lists return empty results and
  posting counts return zero during a confirmed Typesense outage. No live
  fallback returns crawler data from Supabase.
- **Caching**: no Redis cache on main search (Typesense is fast enough). Cached for unfiltered homepage (60s) and popular watchlists (120s). `getCompanyBySlug` is wrapped with a Redis cache (`ttl: 600`, key `company-slug:{slug}:{locale}`) that skips storing nulls so brand-new slugs aren't poisoned
- **Server-side client**: `typesense-js` in the web app, connecting to `typesense.colophon-group.org` (Cloudflare tunnel) with the search/read key

### Direct browser → Typesense (feature-flagged)

The web app can bypass the Vercel server-action proxy and call Typesense directly from the browser for read-heavy surfaces. Gated by `NEXT_PUBLIC_TYPESENSE_DIRECT=1`. Each surface has a server-action fallback for when the browser path errors.

**Surfaces wired direct-browser:**

| Surface | Runner export | Mirrors server action |
|---------|---------------|----------------------|
| `/explore` search loop (filter chip changes, load-more) | `runSearchJobs`, `runListTopCompanies` | `searchJobs`, `listTopCompanies` |
| Shared header search-bar typeahead (per debounced query) | `runSearchBarTypeahead` | `suggestSearchBarTypeahead` (one action for company + four taxonomy caches) |
| Location-only pill / modal typeahead | `runSuggestLocations` | `suggestLocations` |
| Company detail postings list | `runGetCompanyPostings` | `getCompanyPostings` (calls `loadPostingsWithCounts`) |
| Public watchlist postings (≤100 companies) | `runGetWatchlistPostings` | `getWatchlistPostings` (≤100 path; >100 falls back) |

**Out of scope for direct path:**

- `getPostingDetail` (Typesense document reads + R2 URL construction — needs the server search/read key)
- `getCurrencyRates` (DB read, not Typesense)
- Salary/experience histograms (`getSalaryHistogram`/`getExperienceHistogram`, kept on server actions for the 3600 s cache)
- `getCompanyBySlug` (server-rendered and cached; unavailable Typesense degrades to not found)
- `getSimilarCompanies` (filtered path requires Postgres slug→id resolution)
- Browse-all modals (`getGlobalLocationsGrouped`, `getAllOccupationsGrouped`,
  etc. -- server-side Typesense taxonomy snapshots and facets)
- Watchlist postings for >100 companies (uses batched-merge logic that's only worth maintaining server-side)

**Infrastructure:**

- **Scoped key endpoint** (`GET /api/typesense-key`): mints a Typesense scoped search key (HMAC-SHA256 + base64) from `TYPESENSE_BROWSER_PARENT_KEY`. The embed is `{ use_cache: true, expires_at: <Unix seconds> }`. `limit_hits` is intentionally **not** embedded because Typesense counts raw hits (not grouped rows) and would block normal anon traffic on `group_by company_id` with `group_limit 10`.
- **TTL**: 10 min for every visitor because the search-only scope has no user-specific permissions. Browser memory plus `localStorage` reuse the key across reloads/tabs and refresh 30 s before expiry. The endpoint sets a Vercel-only 510 s `max-age` CDN TTL, leaving a 90 s validity margin on the oldest fresh cache hit. Do not use `s-maxage` here: Vercel may serve that response stale once while asynchronously revalidating, but the scoped key has a hard expiry.
- **Browser provider**: `apps/web/src/lib/search/typesense-browser.ts` (postings/companies), `typesense-browser-typeahead.ts` (taxonomy suggest), `typesense-browser-watchlist.ts`. All thin -- no `typesense-js` runtime dependency in the browser bundle.
- **Company posting batch**: one `multi_search` carries the ordered posting
  page, active count, and one-year flow count. Both the browser provider and
  server fallback reject missing or errored result slots as one failed batch;
  a server transport retry replays the whole batch so visible postings and
  counts always come from the same attempt.
- **Anon truncation**: enforced as a soft client-side cap (`ANON_MAX_COMPANIES`, `ANON_MAX_POSTINGS`, `ANON_MAX_WATCHLIST_POSTINGS`) matching the current server-action behaviour. Real abuse protection is the Cloudflare per-IP rate-limit on the tunnel hostname.
- **Fallback**: every runner falls back to the corresponding server action when the browser path errors, returns degraded, or hits a code-explicit fallback case (e.g. watchlist >100 companies).
- **Search-bar application-data request budget**: direct mode batches candidate
  collections, non-English fallbacks, and posting-count boost facets into at
  most three sequential `multi_search` requests. The strict ceiling is four
  application-initiated data requests per debounced query: one scoped-key fetch
  plus three searches on a successful cold worst case, or a stopped direct plan
  plus one batched server-action fallback. Direct-disabled mode uses one action.
  A warm key removes the key fetch; warm candidates remove the candidate and
  locale-fallback searches (a filter-aware warm hit can still need one boost
  search). Best-effort boost failure retains unboosted order and never retries.
  This is deliberately not described as an absolute browser-wire count: cold
  Next.js dynamic-import chunks and browser-generated cross-origin CORS
  `OPTIONS` preflights are cache/transport requests outside the runner's data
  budget. Query generations prevent older completions from updating the UI.

## Read paths summary

Three data tiers, three read paths:

| Tier | Role | Reads |
|------|------|-------|
| Local Postgres (Hetzner) | Source of truth for `job_posting`, taxonomies, companies | Crawler workers, exporter, `refresh-typesense` retained document IDs and watchlist taxonomy-ID resolution |
| Web-owned Postgres | **Only home** for user-facing tables (`user`, `session`, `watchlist`, `watchlist_company`, `saved_job`, ...) | Auth, watchlist mutations, saved-job snapshots, and watchlist company-pair lookups |
| Typesense | In-memory search + denormalized read layer | Job search and posting detail, all typeaheads and taxonomy resolvers, browse-all modals, watchlist search/discovery/posting lists/counts, company autocomplete/detail/location/industry reads, public site stats, similar-company strip |

Posting-count aggregations are read from exhaustive Typesense facets so the
published values match the indexed jobs users can actually see and scheduled
maintenance does not rescan the multi-million-row local table. Notable paths:

- **Watchlist active-posting counts** (`refresh-typesense`): pulls
  `(watchlist_id, company_id)` pairs from the web-owned database configured by
  `WEB_DATABASE_URL`, reads one exhaustive Typesense `company_id` facet with
  `is_active:true && has_content:!=false`, and sums the UUID-string-keyed counts
  per watchlist in Python. Shared companies contribute independently to each
  watchlist; a company absent from the facet contributes zero. The membership
  count is computed separately and is unaffected by posting visibility.
- **Public Discover `anyCompany` counts**: the `watchlist` Typesense doc carries a sanitized `filters_json` payload with public filters plus resolved taxonomy IDs. Discover cards use that payload to run an exact live `job_posting` count for `anyCompany` watchlists without hydrating `watchlist.filters` from Postgres. Company-scoped public cards keep using the denormalized `active_job_count` field.
- **Per-company taxonomy counts** (`refresh_typesense_counts`): reads exhaustive
  `job_posting` facets from Typesense so counts match web-visible filters, then
  updates every retained local Postgres taxonomy/company document. A retained
  ID missing from a valid facet receives an explicit zero; malformed or
  unavailable facet responses abort the refresh.

Most web pages do not aggregate `job_posting` directly -- they read precomputed counts from the Typesense doc fields above. Public Discover is the exception for `anyCompany` watchlists: it computes a live, exact Typesense count from the indexed filter payload because the company join is intentionally empty.

## Monitoring (Grafana/Prometheus)

Metrics exposed by the exporter and scraped by Alloy:

| Metric | Description |
|--------|-------------|
| `typesense_export_docs_total` | Total documents upserted to Typesense |
| `typesense_export_lag` | Cursor lag (seconds behind latest Postgres change) |
| `typesense_export_duration_seconds` | Time per Typesense batch upsert |
| `typesense_healthy` | 0 or 1, from `/health` endpoint |
| `typesense_memory_bytes` | Typesense process memory from `/stats.json` |
| `jobseek_typesense_open_file_descriptors` / `jobseek_typesense_nofile_{soft,hard}_limit` | Live descriptor use and managed process limits from the Typesense host |
| `jobseek_typesense_threadpool_queue_depth` | Maximum queue depth reported during the bounded five-minute log window |
| `jobseek_typesense_slow_request_max_milliseconds` | Slowest request reported during the bounded five-minute log window |
| `jobseek_typesense_recent_log_events{event="..."}` | Five-minute counts for descriptor exhaustion, leaderlessness, snapshot failure, slow requests, and thread-pool exhaustion |
| `jobseek_cross_store_reconciliation_last_unresolved{target="typesense"}` | Unresolved drift from the last target outcome, read from durable PostgreSQL state |
| `jobseek_cross_store_reconciliation_last_payload_mismatch{target="typesense"}` | Same-ID payload mismatches detected in the last complete verified cycle |
| `jobseek_cross_store_reconciliation_last_success_unixtime{target="typesense"}` | Last complete verified Typesense cycle |
| `jobseek_cross_store_reconciliation_progress_partition{target="typesense"}` | Durable next UUID partition |
| `jobseek_cross_store_reconciliation_bootstrap_complete{target="typesense"}` | Whether legacy unbucketed cleanup was verified |

## Credentials Reference

Production addresses, API keys, and connection strings live only in protected
deployment/host environment files. Developer `.env.local` files are ignored
and must never be committed. Never print or hardcode their values. Key
environment variables:

| Variable | Description |
|----------|-------------|
| `TYPESENSE_HOST` | Typesense private IP (for crawler) |
| `TYPESENSE_PORT` | 8108 |
| `TYPESENSE_PROTOCOL` | `http` (private network, no TLS) |
| `TYPESENSE_OPERATIONS_KEY` | Generated crawler key scoped to collection, document, alias, and read-only metrics operations |
| `TYPESENSE_BOOTSTRAP_KEY` | Root-only server bootstrap key; protected deployment secret, never a crawler/web variable |
| `TYPESENSE_BACKUP_KEY` | Root-only generated backup key; protected deployment secret |
| `TYPESENSE_SEARCH_KEY` | Search/read key for web server-side Typesense calls (via tunnel uses `https`) |
| `TYPESENSE_BROWSER_PARENT_KEY` | `documents:search`-only parent for scoped browser keys, limited to `job_posting`, `company`, `location`, `occupation`, `seniority`, and `technology` |
| `TYPESENSE_WRITE_KEY` | Watchlist write key (web app) |
