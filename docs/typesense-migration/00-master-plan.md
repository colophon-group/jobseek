# Typesense Migration — Master Plan

## Overview

Replace all Postgres-backed search (keyword matching, typeahead, histograms, watchlist search) with Typesense. Self-hosted on a dedicated 4 GB Hetzner box with disk backups and a dedicated IPv4.

## Architecture

### Current

```
Crawler workers
  → local Postgres (source of truth)
  → exporter.py (CDC, 1-2s ticks)
    → Supabase Postgres

Web app (Next.js)
  → Supabase Postgres (regex keyword match, similarity() typeahead, SQL histograms)
```

### Target

```
Crawler workers
  → local Postgres (source of truth)
  → exporter.py (CDC, 1-2s ticks)
    → Supabase Postgres (unchanged — still serves non-search reads)
    → Typesense (new — all search queries)

sync.py (one-shot, on CSV changes)
  → local Postgres + Supabase (unchanged)
  → Typesense (new — taxonomy & company collections)

Web app (Next.js)
  → Typesense (keyword search, typeahead, faceted histograms, watchlist search)
  → Supabase Postgres (posting detail, user data, non-search reads)
```

### Data flow diagram

```
                    ┌─────────────┐
                    │  CSVs       │
                    │  (companies,│
                    │  taxonomies)│
                    └──────┬──────┘
                           │ sync.py
                    ┌──────▼──────┐
                    │ Local       │◄──── Crawler workers
                    │ Postgres    │      (monitor + scrape)
                    └──────┬──────┘
                           │ exporter.py
                ┌──────────┼──────────┐
                ▼                     ▼
        ┌──────────────┐     ┌──────────────┐
        │  Supabase    │     │  Typesense   │
        │  (non-search │     │  (all search │
        │   reads)     │     │   queries)   │
        └──────────────┘     └──────────────┘
                ▲                     ▲
                │                     │
                └──────────┬──────────┘
                    ┌──────┴──────┐
                    │  Next.js    │
                    │  Web App    │
                    └─────────────┘
```

## Deployment

### Hardware

- **Machine**: Hetzner CX22 or equivalent — 4 GB RAM, 2 vCPU, 40 GB SSD, dedicated IPv4
- **OS**: Ubuntu 22.04 LTS (or match existing Hetzner fleet)
- **Backups**: Hetzner disk backups enabled (daily snapshots)
- **Networking**: Private network with existing Hetzner machines if available; otherwise public IPv4 with firewall

### Typesense installation

Run as a Docker container for simple upgrades and isolation:

```yaml
# docker-compose.yml on the Typesense machine
services:
  typesense:
    image: typesense/typesense:27.1
    restart: always
    ports:
      - "8108:8108"
    volumes:
      - /mnt/typesense-data:/data
    command: >
      --data-dir /data
      --api-key=${TYPESENSE_API_KEY}
      --enable-cors
    environment:
      - TYPESENSE_API_KEY=${TYPESENSE_API_KEY}
```

### Security

- **Firewall (ufw or Hetzner firewall)**: Port 8108 open only to:
  - Crawler machine (exporter writes)
  - Web app server / Vercel edge (search reads)
- **API keys**: Two scoped keys:
  - `TYPESENSE_ADMIN_KEY` — full access, used by exporter and sync only
  - `TYPESENSE_SEARCH_KEY` — search-only, used by web app
- **TLS**: Terminate TLS at a reverse proxy (Caddy or nginx) in front of Typesense, or use Typesense's built-in `--ssl-certificate` / `--ssl-certificate-key` flags

### Resource budget (4 GB RAM)

| Component | Estimated RAM |
|-----------|--------------|
| OS + Docker overhead | ~400 MB |
| `job_posting` collection (1M docs, ~500 B each) | ~1.0–1.5 GB |
| Taxonomy collections (locations, occupations, seniorities, technologies) | ~50–100 MB |
| `company` collection (~50K docs) | ~30–50 MB |
| `watchlist` collection | ~10 MB |
| **Headroom** | **~1.8–2.5 GB** |

Comfortable up to ~3M postings on 4 GB. At 5M+, upgrade to 8 GB.

### Monitoring

- **Health check**: `GET /health` — returns `{ "ok": true }`, poll every 30s
- **Metrics**: `GET /stats.json` — latency percentiles, memory usage, request counts
- Expose to Grafana via a simple Prometheus exporter or direct JSON polling
- **Alerts**:
  - RAM usage > 80% → warning
  - RAM usage > 90% → critical (OOM risk)
  - Health endpoint unreachable → critical
  - Search latency p99 > 200ms → warning

## Collections

### `job_posting` (primary search collection)

```json
{
  "name": "job_posting",
  "fields": [
    { "name": "id",              "type": "string",   "index": false },
    { "name": "company_id",      "type": "string",   "index": false },
    { "name": "company_name",    "type": "string",   "facet": true },
    { "name": "company_slug",    "type": "string",   "index": false },
    { "name": "title",           "type": "string" },
    { "name": "is_active",       "type": "bool",     "facet": true },
    { "name": "location_ids",    "type": "int32[]",  "facet": true },
    { "name": "location_names",  "type": "string[]", "facet": true },
    { "name": "location_types",  "type": "string[]", "facet": true },
    { "name": "occupation_id",   "type": "int32",    "facet": true, "optional": true },
    { "name": "occupation_name", "type": "string",   "facet": true, "optional": true },
    { "name": "seniority_id",    "type": "int32",    "facet": true, "optional": true },
    { "name": "seniority_name",  "type": "string",   "facet": true, "optional": true },
    { "name": "technology_ids",  "type": "int32[]",  "facet": true },
    { "name": "technology_names","type": "string[]",  "facet": true },
    { "name": "employment_type", "type": "string",   "facet": true, "optional": true },
    { "name": "salary_eur",      "type": "int32",    "facet": true, "optional": true },
    { "name": "experience_min",  "type": "int32",    "facet": true, "optional": true },
    { "name": "locales",         "type": "string[]", "facet": true },
    { "name": "first_seen_at",   "type": "int64" },
    { "name": "last_seen_at",    "type": "int64",    "optional": true }
  ],
  "default_sorting_field": "first_seen_at",
  "token_separators": ["-", "/"]
}
```

**Key design decisions:**
- `title` is the only full-text searchable field (matches current scope — structured data only, no descriptions)
- Taxonomy names are denormalized onto each document (e.g., `occupation_name`, `location_names[]`) so Typesense can search and facet on them without joins
- IDs are kept alongside names for filter-by-ID queries (e.g., location hierarchy expansion)
- `salary_eur` and `experience_min` are numeric for range filtering and histogram faceting
- `first_seen_at` / `last_seen_at` stored as Unix timestamps (int64) for sorting and range queries
- Fields that are only used for display (not search/filter) have `index: false` to save RAM

### `location` (typeahead collection)

```json
{
  "name": "location",
  "fields": [
    { "name": "id",          "type": "int32" },
    { "name": "slug",        "type": "string",  "index": false },
    { "name": "name",        "type": "string" },
    { "name": "name_en",     "type": "string",  "optional": true },
    { "name": "parent_name", "type": "string",  "optional": true },
    { "name": "type",        "type": "string",  "facet": true },
    { "name": "population",  "type": "int32",   "optional": true },
    { "name": "lat",         "type": "float",   "optional": true },
    { "name": "lng",         "type": "float",   "optional": true },
    { "name": "has_active_postings", "type": "bool", "facet": true },
    { "name": "active_posting_count", "type": "int32" }
  ],
  "default_sorting_field": "active_posting_count"
}
```

**Notes:**
- One document per (location, locale) pair — or one doc with `name` in user's preferred locale resolved at query time via `query_by` override
- Geo-sorting handled by Typesense's built-in `_geo_distance_km` if we add a `coordinates` geopoint field, or by client-side re-ranking
- `has_active_postings` filters out locations with no jobs
- `active_posting_count` as default sort surfaces popular locations first

### `occupation` (typeahead collection)

```json
{
  "name": "occupation",
  "fields": [
    { "name": "id",          "type": "int32" },
    { "name": "slug",        "type": "string",  "index": false },
    { "name": "name",        "type": "string" },
    { "name": "aliases",     "type": "string[]" },
    { "name": "domain_name", "type": "string",  "facet": true, "optional": true },
    { "name": "locale",      "type": "string",  "facet": true },
    { "name": "has_active_postings", "type": "bool", "facet": true },
    { "name": "active_posting_count", "type": "int32" }
  ],
  "default_sorting_field": "active_posting_count"
}
```

### `seniority` (typeahead collection)

```json
{
  "name": "seniority",
  "fields": [
    { "name": "id",          "type": "int32" },
    { "name": "slug",        "type": "string",  "index": false },
    { "name": "name",        "type": "string" },
    { "name": "aliases",     "type": "string[]" },
    { "name": "locale",      "type": "string",  "facet": true },
    { "name": "has_active_postings", "type": "bool", "facet": true },
    { "name": "active_posting_count", "type": "int32" }
  ],
  "default_sorting_field": "active_posting_count"
}
```

### `technology` (typeahead collection)

```json
{
  "name": "technology",
  "fields": [
    { "name": "id",       "type": "int32" },
    { "name": "slug",     "type": "string" },
    { "name": "name",     "type": "string" },
    { "name": "category", "type": "string",  "facet": true, "optional": true },
    { "name": "has_active_postings", "type": "bool", "facet": true },
    { "name": "active_posting_count", "type": "int32" }
  ],
  "default_sorting_field": "active_posting_count"
}
```

### `company` (typeahead + watchlist search collection)

```json
{
  "name": "company",
  "fields": [
    { "name": "id",              "type": "string" },
    { "name": "name",            "type": "string" },
    { "name": "slug",            "type": "string",  "index": false },
    { "name": "icon",            "type": "string",  "index": false, "optional": true },
    { "name": "description",     "type": "string",  "optional": true },
    { "name": "industry_id",     "type": "int32",   "facet": true, "optional": true },
    { "name": "industry_name",   "type": "string",  "facet": true, "optional": true },
    { "name": "active_posting_count", "type": "int32" },
    { "name": "year_posting_count",   "type": "int32" }
  ],
  "default_sorting_field": "active_posting_count"
}
```

### `watchlist` (public watchlist search)

```json
{
  "name": "watchlist",
  "fields": [
    { "name": "id",            "type": "string" },
    { "name": "slug",          "type": "string",  "index": false },
    { "name": "title",         "type": "string" },
    { "name": "description",   "type": "string",  "optional": true },
    { "name": "owner_name",    "type": "string" },
    { "name": "company_count", "type": "int32" },
    { "name": "active_job_count", "type": "int32" },
    { "name": "mirror_count",  "type": "int32" },
    { "name": "created_at",    "type": "int64" },
    { "name": "is_public",     "type": "bool",   "facet": true }
  ],
  "default_sorting_field": "created_at"
}
```

## Indexing Pipeline

### Job postings — extend exporter.py

The exporter already runs a CDC loop reading from local Postgres (`WHERE updated_at > cursor`). Add a Typesense upsert step after the Supabase upsert in the same batch cycle.

```
exporter tick:
  1. SELECT changed postings (unchanged)
  2. Upsert to Supabase (unchanged)
  3. [NEW] Upsert to Typesense
     - Denormalize: resolve location_ids → location_names,
       occupation_id → occupation_name, seniority_id → seniority_name,
       technology_ids → technology_names using in-memory lookup tables
     - Batch upsert via POST /collections/job_posting/documents/import
       (action: upsert, batch_size: 40 — Typesense recommendation)
  4. Advance cursor (unchanged)
```

**Denormalization strategy**: The exporter already connects to local Postgres which has all taxonomy tables. Load taxonomy name maps into memory at startup (they're small — hundreds of rows each). Refresh on a timer or when sync.py runs.

**Deletion handling**: When `is_active` flips to false, the document stays in Typesense with `is_active: false`. All search queries filter on `is_active:true`. Periodically purge documents where `last_seen_at < now - 1 year` via a cleanup job.

**Initial backfill**: On first deployment, run a one-shot full export:
```bash
# Export all postings, not just changed ones
uv run crawler export --backfill-typesense
```
This iterates through all job_posting rows in batches (same LIMIT/OFFSET as normal export) and upserts to Typesense. At 1M rows / 2000 per batch / ~1s per batch = ~8 minutes.

### Taxonomy collections — extend sync.py

sync.py already writes to local Postgres + Supabase + Redis. Add Typesense as a fourth target.

After each taxonomy table sync:
1. Query all rows from the taxonomy table
2. Enrich with `active_posting_count` (count of active job_posting rows referencing this ID)
3. Bulk upsert to the corresponding Typesense collection

Since sync.py runs infrequently (on CSV changes / deploys), this adds negligible overhead.

### Company collection — dual source

- **Initial load + updates**: sync.py writes company metadata (name, slug, icon, industry)
- **Posting counts**: Updated by exporter after each batch (recount active/year postings per company, batch update the Typesense company documents)
- Alternative: A periodic job (every 5 min) that recalculates counts and patches company docs

### Watchlist collection — web app writes

Watchlists are created/updated by users in the web app. Add Typesense upsert hooks:
- On watchlist create/update/delete → upsert/delete in Typesense `watchlist` collection
- Only index public watchlists (`is_public: true`)
- Counts (`active_job_count`, `company_count`) refreshed periodically or on access

### Collection aliasing for zero-downtime reindexing

Use Typesense collection aliases for safe schema changes:

```
job_posting_v1  ←  job_posting (alias)
```

To reindex with a new schema:
1. Create `job_posting_v2` with new schema
2. Backfill all documents into v2
3. Swap alias: `job_posting` → `job_posting_v2`
4. Drop `job_posting_v1`

## Web App Integration

### TypesenseSearchProvider

Create `apps/web/src/lib/search/typesense.ts` implementing the existing `SearchProvider` interface. Drop-in replacement for `PostgresSearchProvider`.

```typescript
// apps/web/src/lib/search/index.ts
export function getSearchProvider(): SearchProvider {
  if (process.env.SEARCH_PROVIDER === "typesense") {
    return new TypesenseSearchProvider();
  }
  return new PostgresSearchProvider();
}
```

Toggle via `SEARCH_PROVIDER=typesense` env var. Keeps Postgres as fallback during migration.

### Client library

Use `typesense-js` (official Node.js client) in the web app:

```typescript
import Typesense from "typesense";

const client = new Typesense.Client({
  nodes: [{ host: process.env.TYPESENSE_HOST!, port: 8108, protocol: "https" }],
  apiKey: process.env.TYPESENSE_SEARCH_KEY!,
  connectionTimeoutSeconds: 2,
});
```

### Python client (crawler)

Use `typesense` Python package in the crawler:

```python
import typesense

client = typesense.Client({
    "nodes": [{"host": settings.typesense_host, "port": "8108", "protocol": "https"}],
    "api_key": settings.typesense_admin_key,
    "connection_timeout_seconds": 5,
})
```

### Caching

Typesense queries are fast (<10ms for structured search). The existing Redis cache layer in the web app can be simplified:
- **Remove** caching for keyword search and typeahead (Typesense is faster than Redis deserialization for small payloads)
- **Keep** caching for histogram aggregations if they prove slower than expected
- **Keep** caching for the public API route (rate limiting / abuse protection)

Evaluate after benchmarking — start with caching disabled for Typesense queries, add back if needed.

## Rollout Plan

### Phase 1: Infrastructure (1–2 days)

1. Provision Hetzner CX22 (4 GB RAM, dedicated IPv4)
2. Install Docker, deploy Typesense container
3. Configure firewall (allow crawler + web app IPs only)
4. Set up TLS termination
5. Generate API keys, add to GitHub secrets / env files
6. Verify health endpoint from crawler and web app machines

### Phase 2: Indexing pipeline (3–5 days)

1. Add `typesense` Python package to crawler dependencies
2. Create collection schemas via a setup script (`scripts/typesense-setup.py`)
3. Extend `exporter.py` with Typesense upsert step
4. Extend `sync.py` with taxonomy + company collection sync
5. Run initial backfill
6. Verify document counts match Postgres
7. Set up monitoring (health + RAM + latency)

### Phase 3: Search provider (3–5 days)

1. Add `typesense` JS package to web app dependencies
2. Implement `TypesenseSearchProvider` (search, listTopCompanies, loadPostings, histograms)
3. Implement Typesense-backed suggest functions (all 5 typeahead surfaces)
4. Implement watchlist search via Typesense
5. Wire up `SEARCH_PROVIDER` env var toggle
6. Test locally against Typesense instance

### Phase 4: Validation & cutover (2–3 days)

1. Deploy web app with `SEARCH_PROVIDER=typesense` to staging
2. Compare search results quality vs Postgres (spot-check queries)
3. Benchmark latency (expect 5–50ms Typesense vs 50–300ms Postgres)
4. Deploy to production
5. Monitor error rates, latency, RAM usage for 48h
6. Remove Postgres search code and `SEARCH_PROVIDER` toggle once stable

### Phase 5: Cleanup

1. Remove `PostgresSearchProvider` class
2. Remove Postgres trigram extension / similarity indexes (if no other users)
3. Remove search-related Redis cache keys
4. Update CLAUDE.md / docs with new architecture
5. Drop unused Supabase indexes (search-specific GIN indexes, etc.)

## Configuration

New environment variables:

```bash
# Crawler (exporter + sync)
TYPESENSE_HOST=<typesense-ipv4>
TYPESENSE_PORT=8108
TYPESENSE_PROTOCOL=https
TYPESENSE_ADMIN_KEY=<admin-key>

# Web app
TYPESENSE_HOST=<typesense-ipv4>
TYPESENSE_PORT=8108
TYPESENSE_PROTOCOL=https
TYPESENSE_SEARCH_KEY=<search-only-key>
SEARCH_PROVIDER=typesense  # or "postgres" for fallback
```

## Risks and mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Typesense OOM | Search outage | RAM monitoring + alerts at 80%, upgrade path to 8 GB |
| Index corruption on crash | Stale search results | Disk backups, re-backfill from Postgres (source of truth) |
| Exporter Typesense upsert failure | Index falls behind | Retry with backoff, alert on lag, Supabase export unaffected |
| Schema migration needed | Reindex downtime | Collection aliasing (zero-downtime swap) |
| Typesense version upgrade | Breaking changes | Pin Docker image version, test upgrades in staging |
| Network partition (crawler ↔ Typesense) | Index falls behind | Exporter retries, cursor doesn't advance on failure |

## Open questions

- **Location geo-sorting**: Typesense supports `_geo_distance_km` natively via a `geopoint` field type. Should we add `coordinates: geopoint` to the location collection for native geo-sorting in typeahead, or handle it client-side?
- **Multi-locale typeahead**: One document per (entity, locale) pair, or one document with all locale names as a string array? The former is simpler to query; the latter saves documents.
- **Posting count refresh cadence**: How often should `active_posting_count` on taxonomy/company docs be refreshed? Every exporter tick is accurate but adds write load. Every 5 min is a pragmatic default.
