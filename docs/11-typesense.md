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
| `TYPESENSE_WRITE_KEY` | `documents:upsert/delete/update` on `watchlist` collection only | Web app watchlist mutations | Cloudflare tunnel |

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

Collection schemas are defined in `scripts/typesense-setup.py`. Run it to create or recreate collections:

```bash
cd apps/crawler && uv run python ../../scripts/typesense-setup.py         # Create (idempotent)
cd apps/crawler && uv run python ../../scripts/typesense-setup.py --force  # Drop + recreate
```

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
- **Graceful degradation**: all Typesense errors return empty results; Postgres fallback for watchlist write functions
- **Caching**: no Redis cache on main search (Typesense is fast enough). Cached for unfiltered homepage (60s) and popular watchlists (120s)
- **Client**: `typesense-js` in the web app, connecting to `typesense.colophon-group.org` (Cloudflare tunnel) with the search-only key

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
