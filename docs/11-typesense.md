# Typesense Deployment State

Current production deployment as of April 2026. The earlier docs in this directory (00-05) describe the migration plan and benchmarks; this document describes what was actually deployed.

## Infrastructure

### Typesense Machine

- **Hetzner CX22**: 4 GB RAM, 2 vCPU, dedicated IPv4
- **OS**: Ubuntu (Docker host)
- **Container**: `typesense/typesense:27.1`, `--network host`, data at `/mnt/typesense-data`
- **Port**: 8108
- **Firewall**: SSH from anywhere, port 8108 from private network only (10.0.0.0/16)

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
- **Daemon**: `cloudflared` running as a systemd service, auto-starts on reboot
- **Cache bypass rule**: configured in Cloudflare dashboard -- without it, Cloudflare may cache GET search responses and return stale results (Typesense does not set `Cache-Control` headers by default)
- **Latency overhead**: ~10-30 ms per request (acceptable -- Typesense queries take <10 ms)

## API Keys

Three scoped keys. Stored in: `apps/crawler/.env.local` (main branch), GitHub secrets (CI), Vercel env vars (web app).

| Environment Variable | Scope | Used By | Connection Path |
|---------------------|-------|---------|-----------------|
| `TYPESENSE_ADMIN_KEY` | Full access | Exporter, sync, backfill, setup scripts | Private network (crawler -> Typesense) |
| `TYPESENSE_SEARCH_KEY` | `documents:search` on all collections | Web app search | Cloudflare tunnel |
| `TYPESENSE_WRITE_KEY` | `documents:create/upsert/delete/update` on `watchlist` collection only | Web app watchlist mutations | Cloudflare tunnel |

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

  **Design rule: Postgres stores leaf IDs only; the exporter expands to ancestors at indexing time.** Do NOT expand ancestors in the crawler processing pipeline (`_resolve_locations_sync`, `_resolve_locations`). Postgres `location_ids` and `location_types` must remain parallel arrays of the same length (Supabase enforces `chk_location_arrays_length`). Ancestor expansion adds extra IDs without matching type entries, breaking this constraint.

  **Where ancestor expansion happens (exporter only):**
  - `exporter.py` → `TaxonomyMaps.location_ancestors`: walks `location.parent_id` chain + `location_macro_member` (macro regions like EU, DACH). Populates `location_ids` on Typesense documents.
  - `exporter.py` → `TaxonomyMaps.occupation_ancestors`: walks `occupation.parent_id` chain. Populates `occupation_ids` on Typesense documents.
  - The backfill script (`typesense-backfill-local.py`) must use the same logic.

  **Invariant**: `buildFilterString()` in the web app filters on `location_ids` and `occupation_ids` (plural array fields). If only leaf IDs reach Typesense, hierarchy filtering silently breaks (filtering by "Germany" won't match "Berlin"). If ancestors are written to Postgres instead, the `location_ids`/`location_types` length constraint breaks and the Supabase exporter stalls.
- **Sentinel values**: `experience_min = -1` for NULL (Typesense excludes missing optional fields from range queries). `locales = ["_none"]` for jobs with no detected language.
- **Denormalized names**: Taxonomy names (location, occupation, seniority, technology) are stored directly on each job posting document for search and faceting without joins.
- **Versioned aliases**: `job_posting` is an alias pointing to `job_posting_v1`. To reindex with a new schema: create `_v2`, backfill, swap alias, drop `_v1`.

### Schema Definition

Collection schemas are the single source of truth in `apps/crawler/src/typesense_schema.py` (`COLLECTIONS`). Two callers:

- `scripts/typesense-setup.py` -- operator-facing wrapper for dev workflows.
- `crawler setup-typesense` CLI subcommand -- exposed inside the crawler image so `deploy.sh` can patch the live cluster on every deploy.

Both are idempotent. On every run, the setup logic:

1. Creates any missing collection + alias (initial setup).
2. PATCHes existing collections to add fields that appear in `COLLECTIONS` but not on the live cluster -- via `client.collections[name].update({"fields": [...]})` against Typesense's alter API.
3. Never removes fields automatically (manual operator step).

```bash
cd apps/crawler && uv run python ../../scripts/typesense-setup.py         # Idempotent: create + patch
cd apps/crawler && uv run python ../../scripts/typesense-setup.py --force  # Drop + recreate (data loss)
uv run --no-sync crawler setup-typesense                                   # Same, from inside the image
```

The deploy script (`apps/crawler/deploy.sh`) runs `crawler setup-typesense` between Alembic migrations and `crawler sync`, so a PR that adds new fields ships safely: schema is patched first, then `sync` upserts populate the new fields.

### Company Collection (extended for company detail page)

The `company` collection doubles as the source for the company detail page (see [Read paths](#read-paths-summary)) and therefore carries fields beyond what typeahead/browse needs:

| Field | Type | Purpose |
|------|------|---------|
| `id`, `name`, `slug`, `icon` | scalar | shared with typeahead |
| `logo`, `website`, `employee_count_range`, `founded_year` | scalar | detail page facts |
| `description` | string (en) | fallback when no per-locale variant |
| `description_de`, `description_fr`, `description_it` | string | per-locale variants from `company_description`; reader falls back to `description` |
| `industry_id`, `industry_name` | scalar | en industry name from `industry.name` |
| `industry_name_de`, `industry_name_fr`, `industry_name_it` | string | per-locale display names from `industry_name`; same fallback rule |
| `active_posting_count`, `year_posting_count` | int32 | counts (refreshed by `refresh-typesense`) |

## Indexing Pipeline

### Job Postings (CDC via exporter.py)

The exporter uses a **two-cursor design**: Supabase and Typesense each have their own keyset cursor (`(updated_at, id)` tuple). On each tick:

1. SELECT changed postings after `MIN(supabase_cursor, typesense_cursor)`
2. Concurrently (`asyncio.gather`):
   - Upsert to Supabase, advance Supabase cursor on success
   - Denormalize + expand ancestor IDs + upsert to Typesense, advance Typesense cursor on success

The Typesense document builder (`_build_typesense_docs`) expands `location_ids` and `occupation_ids` with all ancestor IDs using pre-loaded hierarchy maps (`TaxonomyMaps.location_ancestors`, `occupation_ancestors`). This means even legacy Postgres rows with leaf-only IDs produce correct hierarchy-filterable Typesense documents.

If one target fails, only its cursor stalls. The other continues unaffected.

**Feature flag**: Typesense writes only happen when `TYPESENSE_ADMIN_KEY` is set (non-empty). Environments without Typesense are unaffected. The env var must be passed to containers in `docker-compose.yml` (`x-common-env`).

**Denormalization**: The exporter's `TaxonomyMaps` reads all lookup data from **local Postgres** (the source of truth). Company info, location names, occupation names, seniority names, and technology names are all loaded from local. A Supabase fallback exists for company_info only (for pre-migration compatibility). All ancestor chain computation (locations + macro regions, occupations) uses local Postgres data exclusively.

### Taxonomy Collections (via sync.py)

After CSV sync, `sync.py` populates taxonomy collections (location, occupation, seniority, technology, company) in Typesense. Includes:

- `active_posting_count` and `has_active_postings` for each taxonomy entry
- Taxonomy rename detection: if a name changes in CSV, affected job posting documents in Typesense are updated with the new denormalized name

### Count Refresh + Watchlist Reconciliation

```bash
uv run crawler refresh-typesense
```

- Refreshes `active_posting_count` / `has_active_postings` on all taxonomy and company collections
- Reconciles the `watchlist` collection against Supabase (upserts missing, deletes stale)
- Should be run periodically (cron or after sync)

### Full Re-index (Backfill)

```bash
uv run crawler backfill-typesense    # Production: reads from local Postgres + Supabase
```

For local development/testing only:
```bash
cd apps/crawler && uv run python ../../scripts/typesense-backfill-local.py [--limit N]
```

### Reconciliation

Daily reconciliation (run by the exporter loop):

1. Compare document counts: Postgres vs Typesense `num_documents`
2. If counts diverge by >1%, trigger a full backfill
3. Sample random posting IDs, compare `is_active` between Postgres and Typesense
4. Touch discrepant rows in Postgres so CDC picks them up on the next tick

## Web App Integration

- `TypesenseSearchProvider` implements the `SearchProvider` interface, replacing `PostgresSearchProvider` (one-shot cutover)
- All search, typeahead, browse-all modals, and watchlist search go through Typesense
- **Company detail page**: `getCompanyBySlug` reads the `company` collection by slug filter. Postgres is a fallback when Typesense errors or returns 0 hits (so brand-new companies whose Typesense upsert lagged still render)
- **Graceful degradation**: all Typesense errors return empty results; Postgres fallback for watchlist write functions
- **Caching**: no Redis cache on main search (Typesense is fast enough). Cached for unfiltered homepage (60s) and popular watchlists (120s). `getCompanyBySlug` is wrapped with a Redis cache (`ttl: 600`, key `company-slug:{slug}:{locale}`) that skips storing nulls so brand-new slugs aren't poisoned
- **Client**: `typesense-js` in the web app, connecting to `typesense.colophon-group.org` (Cloudflare tunnel) with the search-only key

## Read paths summary

Three data tiers, three read paths:

| Tier | Role | Reads |
|------|------|-------|
| Local Postgres (Hetzner) | Source of truth for `job_posting`, taxonomies, companies | Crawler workers, exporter, `refresh-typesense` count aggregations, watchlist active-posting counts (via crawler) |
| Supabase Postgres | Mirror of `job_posting` + companies + taxonomies; **only home** for user-facing tables (`user`, `session`, `watchlist`, `watchlist_company`, ...) | Auth, watchlist mutations, watchlist company-pair lookups, posting detail (full description blob), Postgres fallbacks |
| Typesense | In-memory search + denormalized read layer | Job search, all typeaheads, browse-all modals, watchlist search, company detail page, similar-company strip |

Aggregation queries against `job_posting` are deliberately kept on local Postgres, not Supabase, to keep Supabase compute reserved for user-facing CRUD. Two notable examples:

- **Watchlist active-posting counts** (`refresh-typesense`): pulls `(watchlist_id, company_id)` pairs from Supabase, runs `COUNT(*) WHERE is_active GROUP BY company_id` on local Postgres restricted to those companies, sums per watchlist in Python. Uses the partial index `idx_jp_company_active ON job_posting(company_id) WHERE is_active`.
- **Per-company taxonomy counts** (`refresh_typesense_counts`): aggregated against local Postgres directly, then upserted to the `company` / `location` / `occupation` / `seniority` / `technology` collections as `active_posting_count`.

Web pages do not aggregate `job_posting` directly -- they read precomputed counts from the Typesense doc fields above.

## Monitoring (Grafana/Prometheus)

Metrics exposed by the exporter and scraped by Alloy:

| Metric | Description |
|--------|-------------|
| `typesense_export_docs_total` | Total documents upserted to Typesense |
| `typesense_export_lag` | Cursor lag (seconds behind latest Postgres change) |
| `typesense_export_duration_seconds` | Time per Typesense batch upsert |
| `typesense_healthy` | 0 or 1, from `/health` endpoint |
| `typesense_memory_bytes` | Typesense process memory from `/stats.json` |
| `typesense_reconciliation_discrepancies` | Count mismatches found during daily reconciliation |

## Credentials Reference

All IPs, API keys, and connection strings are in `apps/crawler/.env.local` on the main branch. Never hardcode them. Key environment variables:

| Variable | Description |
|----------|-------------|
| `TYPESENSE_HOST` | Typesense private IP (for crawler) |
| `TYPESENSE_PORT` | 8108 |
| `TYPESENSE_PROTOCOL` | `http` (private network, no TLS) |
| `TYPESENSE_ADMIN_KEY` | Admin API key |
| `TYPESENSE_SEARCH_KEY` | Search-only key (web app, via tunnel uses `https`) |
| `TYPESENSE_WRITE_KEY` | Watchlist write key (web app) |
