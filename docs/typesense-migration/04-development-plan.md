# Development Plan — Agent Work Distribution

Phase-based plan with work distributed across implementation and verification subagents. Follows the repo's parallel orchestration pattern (independent tracks, convergence gates, evidence-based verification).

Development and testing run against a local Typesense instance on the dev machine (Docker). Production deployment to Hetzner happens after E2E tests pass.

## Agent roles

| Role | Responsibility |
|------|---------------|
| **Orchestrator** (main agent) | Sequences phases, spawns subagents, gates convergence, makes trade-off decisions |
| **Impl** subagent | Writes code, creates files, runs commands. Reports what was done + any issues. |
| **Verify** subagent | Reads code, runs tests, queries APIs, compares outputs. Reports pass/fail with evidence. |

Impl and Verify never run in the same subagent — separation ensures the person building it isn't the one checking it.

---

## Phase 1: Local Typesense deployment

Orchestrator runs this directly. Add Typesense to the existing `docker-compose.yml` on this machine.

### Steps

1. Add `typesense` service to `/Users/Viktor/jobseek/docker-compose.yml`:
   ```yaml
   services:
     postgres:
       # ... existing postgres config unchanged ...

     typesense:
       image: typesense/typesense:27.1
       restart: unless-stopped
       ports:
         - "8108:8108"
       volumes:
         - typesense-data:/data
       command: >
         --data-dir /data
         --api-key=local_dev_typesense_key
         --enable-cors

   volumes:
     pgdata:
     typesense-data:
   ```
2. Start the container: `docker compose up -d typesense`
3. Add local dev env vars to crawler `.env` and web app `.env.local`:
   ```bash
   TYPESENSE_HOST=localhost
   TYPESENSE_PORT=8108
   TYPESENSE_PROTOCOL=http
   TYPESENSE_ADMIN_KEY=local_dev_typesense_key
   TYPESENSE_SEARCH_KEY=local_dev_typesense_key  # same key for local dev
   ```

### Gate: Local Typesense running

```bash
curl -s http://localhost:8108/health -H "X-TYPESENSE-API-KEY: local_dev_typesense_key"
# → {"ok": true}
```

---

## Phase 2: Collection schemas + indexing pipeline

### Subagent layout

```
Orchestrator
  ├── Impl-2A: Collection setup script        ──┐
  │                                              ├── Gate: schemas created
  ├── Verify-2A: Verify schemas exist           ──┘
  │
  ├── Impl-2B: Exporter Typesense integration  ──┐
  ├── Impl-2C: Sync Typesense integration       │  (parallel — independent code paths)
  │                                              ├── Gate: indexing pipeline works
  ├── Verify-2B: Verify exporter indexing       ──┘
  ├── Verify-2C: Verify sync indexing           ──┘
  │
  └── Verify-2D: Full backfill + count check   ──── Gate: data parity
```

### Impl-2A: Collection setup script

**Scope**: Create `scripts/typesense-setup.py` that creates all 7 collection schemas (with aliases) against a running Typesense instance.

**Steps:**
1. Add `typesense` to crawler's `pyproject.toml` dependencies
2. Add Typesense config fields to `apps/crawler/src/config.py`:
   - `typesense_host`, `typesense_port`, `typesense_protocol`, `typesense_admin_key`
3. Create `scripts/typesense-setup.py`:
   - Reads collection schemas from a Python dict (matching `00-master-plan.md` definitions)
   - For each collection: create `{name}_v1`, then alias `{name}` → `{name}_v1`
   - Idempotent — skip if alias already exists, drop-and-recreate if `--force` flag
4. Create `apps/crawler/src/typesense_client.py` — shared singleton client factory

**Report**: List of collections created, any errors.

### Verify-2A: Verify schemas exist

**Scope**: Confirm all 7 collections + aliases exist with correct fields.

**Steps:**
1. Run `scripts/typesense-setup.py` against local Typesense
2. For each collection (`job_posting`, `location`, `occupation`, `seniority`, `technology`, `company`, `watchlist`):
   - `GET /collections/{name}` — verify it returns schema
   - Check field count matches expected
   - Check `geopoint` field exists on `location`
   - Check `token_separators` / `symbols_to_index` on `technology`
3. For each alias: `GET /aliases/{name}` — verify points to `{name}_v1`

**Gate**: All 7 collections exist, all 7 aliases resolve, field counts match.

---

### Impl-2B: Exporter Typesense integration

**Scope**: Extend `apps/crawler/src/exporter.py` to upsert job postings to Typesense after Supabase export.

**Steps:**
1. In `exporter.py`, add a Typesense client initialization at startup
2. Load taxonomy name maps into memory at startup:
   - `location_id → {name_en, name_de, name_fr, name_it}` from `location_name` table
   - `occupation_id → name` from `occupation_name` (display name per locale — pick `en` for denormalized field)
   - `seniority_id → name` from `seniority_name` (same)
   - `technology_id → name` from `technology` table
   - `company_id → {name, slug, icon}` from `company` table
3. Implement **two-cursor design with concurrent upserts** (see `00-master-plan.md`):
   - Add a separate cursor `last_export_ts:typesense:job_posting` in `exporter_state`
   - Uses the same `Cursor = tuple[datetime, uuid.UUID]` format and `_get_cursor` / `_save_cursor` helpers as the existing Supabase cursor (keyset pagination on `(updated_at, id)`)
   - SELECT uses `MIN(supabase_cursor, typesense_cursor)` to ensure both targets see all rows
   - Run Supabase + Typesense upserts concurrently via `asyncio.gather(return_exceptions=True)`
   - Each target's cursor advances independently on success
   - If Typesense fails: log error, Typesense cursor stalls, Supabase unaffected
4. Add `last_seen_at` to the SELECT for Typesense only — do NOT add to `_POSTING_COLUMNS` (would break Supabase COPY since Supabase lacks this column). Either widen the SELECT and strip before COPY (same pattern as `updated_at`), or run a supplementary query.
5. Implement `_index_to_typesense(rows)`:
   - For each row, build Typesense document:
     - Denormalize names from in-memory maps
     - Denormalize `location_geo_types` from location table (city/region/country/macro)
     - Convert `first_seen_at` / `last_seen_at` to Unix timestamps
     - Set `company_icon` from company map
     - Set sentinel values: `experience_min = -1` for NULL, `locales = ["_none"]` for empty array
   - Batch upsert via `client.collections['job_posting'].documents.import_(docs, {'action': 'upsert'})`
6. Add `--backfill-typesense` CLI flag to `cli.py`:
   - Iterates all `job_posting` rows (not just changed ones)
   - Same denormalization + upsert logic
   - Progress logging every 10K rows
7. Add taxonomy map refresh: reload maps every 10 minutes (or on SIGHUP)
8. Extend `run_reconciliation()` with Typesense check:
   - Compare doc counts (Postgres vs Typesense)
   - Sample 100 random IDs, compare `is_active` / `updated_at`
   - Touch discrepant rows so CDC cursor picks them up

**Report**: Files modified, new functions, error handling approach.

### Impl-2C: Sync Typesense integration

**Scope**: Extend `apps/crawler/src/sync.py` to write taxonomy, company, and initial watchlist data to Typesense.

**Steps:**
1. After each taxonomy sync block, add Typesense bulk upsert **outside the Supabase transaction** (fire-and-forget — if Typesense fails, don't roll back the CSV sync):
   - **Locations**: Query from **Supabase** (local Postgres lacks lat/lng/slug). All locale names from `location_name`, coordinates from `location.lat/lng`, parent_name from parent join. Build one doc per location. Count active postings. Upsert to `location` collection.
   - **Occupations**: Query all (occupation, locale) pairs with display names + aliases. Count active postings. Upsert to `occupation` collection (one doc per entity+locale, id = `{occupation_id}-{locale}`).
   - **Seniorities**: Same pattern as occupations.
   - **Technologies**: Query all technologies. Count active postings. Upsert to `technology` collection.
2. After company sync, upsert to `company` collection:
   - Name, slug, icon, industry_id, industry_name
   - `active_posting_count` and `year_posting_count` from job_posting counts
3. Add public watchlist sync:
   - Query from **Supabase** (watchlists are user-created, only exist on Supabase): `SELECT w.*, u.name AS owner_name, u.username AS owner_username FROM watchlist w JOIN user u ON ... WHERE w.is_public = true`
   - `company_count`: count from watchlist_company junction table
   - `active_job_count`: count from job_posting joined via watchlist companies
   - `mirror_count`: `SELECT COUNT(*) FROM watchlist WHERE source_watchlist_id = $1`
   - Build docs with title, description, owner info, counts
   - Upsert to `watchlist` collection
4. Implement `refresh_typesense_counts()` as a standalone function:
   - For each taxonomy collection: `SELECT {id}, COUNT(*) FROM job_posting WHERE is_active GROUP BY 1` → batch-update docs
   - For company collection: same + `year_posting_count` (postings from last year)
   - Idempotent, callable by sync.py after each run + on a timer (~30 min)
   - Counts are approximate — imprecise values (100+, 1100+) are acceptable
5. Implement taxonomy rename detection in sync.py:
   - Before syncing each taxonomy, snapshot the current name maps
   - After sync, diff against new maps
   - If any names changed: directly update affected Typesense documents
     - Query `SELECT id FROM job_posting WHERE {taxonomy_id} = $1`
     - Build partial update docs with the new denormalized name
     - Batch upsert to Typesense job_posting collection
   - Self-contained — no inter-process signaling, no CDC dependency

**Report**: Files modified, collections populated, document counts per collection.

### Verify-2B: Verify exporter indexing

**Scope**: Run the exporter against local Postgres + local Typesense and confirm job postings appear with correct denormalized data.

**Steps:**
1. Run exporter for ~30 seconds (let it process a few batches)
2. Query local Typesense: `GET http://localhost:8108/collections/job_posting/documents/search?q=*&per_page=5`
3. For each returned document, verify:
   - `title` is non-empty string
   - `company_name` matches the `company_id` → name mapping
   - `location_names` array is non-empty (if `location_ids` is non-empty)
   - `occupation_name` is set (if `occupation_id` is set)
   - `seniority_name` is set (if `seniority_id` is set)
   - `technology_names` length matches `technology_ids` length
   - `first_seen_at` is a valid Unix timestamp
   - `is_active` is boolean
   - `salary_eur` is null or positive integer
4. Check total document count: `GET /collections/job_posting` → `num_documents`
5. Compare with Postgres: `SELECT COUNT(*) FROM job_posting WHERE updated_at > <cursor_start>`

**Gate**: Documents present, denormalized fields correct, count roughly matches export batch.

### Verify-2C: Verify sync indexing

**Scope**: Run sync and confirm taxonomy + company collections are populated correctly in local Typesense.

**Steps:**
1. Run `uv run crawler sync`
2. For each taxonomy collection, verify:
   - **Locations**: Check total doc count. Sample 5 docs — verify `name_en` present, `coordinates` is `[lat, lng]` array (for cities/countries with lat/lng), `type` is one of macro/country/region/city.
   - **Occupations**: Check doc count ≈ (occupation count × 4 locales). Sample docs — verify `locale` field, `name` non-empty, `aliases` is array.
   - **Seniorities**: Same checks as occupations.
   - **Technologies**: Check doc count. Sample docs — verify `slug`, `name`, `category` fields. Specifically check "C++", "C#", ".NET" are findable via search.
3. For `company` collection: check doc count matches Postgres company count. Sample 5 — verify `name`, `active_posting_count > 0` for known active companies.
4. For `watchlist` collection: check only public watchlists indexed. Verify `is_public: true` on all docs.

**Gate**: All taxonomy collections populated, doc counts match, sample data correct.

### Verify-2D: Full backfill + count check

**Scope**: Run full backfill and verify data parity with local Postgres.

**Steps:**
1. Run `uv run crawler export --backfill-typesense`
2. Wait for completion (monitor progress logs)
3. Compare counts:
   ```sql
   -- Postgres
   SELECT COUNT(*) FROM job_posting;
   SELECT COUNT(*) FROM job_posting WHERE is_active = true;
   ```
   ```
   -- Typesense (local)
   GET http://localhost:8108/collections/job_posting → num_documents
   GET http://localhost:8108/collections/job_posting/documents/search?q=*&filter_by=is_active:true&per_page=0 → found
   ```
4. Counts should match within 0.1% (some postings may be updated during backfill)
5. Spot-check 10 random postings: fetch by ID from both Postgres and Typesense, compare fields

**Gate**: Total count parity, active count parity, spot-check fields match.

---

## Phase 3: Search provider implementation

### Subagent layout

```
Orchestrator
  ├── Impl-3A: TypesenseSearchProvider        ──┐
  │   (search, listTopCompanies, loadPostings,  │  (parallel — independent code)
  │    loadPostingsWithCounts, histograms,       │
  │    graceful degradation)                     │
  │                                              │
  ├── Impl-3B: Typeahead + browse-all modals    │
  │   (5 suggest functions + multi_search +      │
  │    getGlobalLocationsGrouped,                │
  │    getAllOccupationsGrouped,                  │
  │    getAllSeniorities,                         │
  │    getAllTechnologiesGrouped)                 │
  │                                              │
  ├── Impl-3C: Watchlist search + postings      │
  │   (searchCompaniesForWatchlist,              │
  │    searchPublicWatchlists, write hooks,      │
  │    getWatchlistPostings)                     │
  │                                              │
  └── Impl-3D: Cleanup Postgres search code     ──── (after 3A/3B/3C complete)
```

**Dependency**: Impl-3A, 3B, 3C are independent and run in parallel. Impl-3D runs after all three complete.

**Shared file note**: Impl-3B and Impl-3C both modify `actions/company.ts` (suggestCompanies vs searchCompaniesForWatchlist). Impl-3B also modifies `actions/taxonomy.ts` for both suggest and browse-all functions. These are different functions in the same file — merge conflicts are possible but straightforward to resolve. The orchestrator should merge after both complete.

**Type change**: Add `degraded?: boolean` to the `SearchResponse` interface in `types.ts` (Impl-3A's responsibility).

### Impl-3A: TypesenseSearchProvider

**Scope**: Implement the core search provider that replaces `PostgresSearchProvider`.

**Files to create:**
- `apps/web/src/lib/search/typesense-client.ts` — singleton Typesense client
- `apps/web/src/lib/search/typesense-filters.ts` — `buildFilterString()` helper
- `apps/web/src/lib/search/typesense.ts` — `TypesenseSearchProvider` class

**Steps:**
1. Add `typesense` to `apps/web/package.json`
2. Create `typesense-client.ts`:
   - Singleton client using `TYPESENSE_HOST`, `TYPESENSE_PORT`, `TYPESENSE_PROTOCOL`, `TYPESENSE_SEARCH_KEY` env vars
   - Connection timeout: 2s
3. Create `typesense-filters.ts`:
   - `buildFilterString(filters: SearchFilters): string` — builds Typesense `filter_by` string
   - Does NOT inject `is_active:true` — callers add it explicitly
   - Maps each filter dimension (locationIds, occupationIds, salary range, etc.)
   - Sentinel handling: `experience_min` filter includes `|| experience_min:=-1` in ALL cases (min-only, max-only, both bounds — see `01-job-search.md` for exact logic); `locales` filter includes `"_none"` sentinel
   - See `01-job-search.md` for exact mapping
4. Create `typesense.ts` implementing `SearchProvider`:
   - `search()` — `multi_search` with `q: keywords.join(" ")`, `group_by: "company_id"`, `group_limit: 10`
   - `listTopCompanies()` — two paths: (a) with filters: `facet_by: company_id` on `job_posting` for filtered ranking + `facet_strategy: "exhaustive"`, then fetch postings; (b) without filters: query `company` collection by `active_posting_count`, then fetch postings. See `01-job-search.md` for details.
   - `loadPostings()` — filter by `company_id`, sort by text match or recency
   - `loadPostingsWithCounts()` — `loadPostings()` + count queries via `multi_search` (active + year counts)
   - `getSalaryHistogram()` — faceted range query on `salary_eur` with 30 boundaries (0, 10K, ..., 300K)
   - `getExperienceHistogram()` — facet on `experience_min`, transform to `ExperienceBucket[]`
5. Update `apps/web/src/lib/search/index.ts`:
   - Replace `PostgresSearchProvider` with `TypesenseSearchProvider` directly (no toggle)
6. Add graceful degradation: all SearchProvider methods catch connection errors and return empty results with `degraded: true` flag, rather than propagating exceptions to the UI

**Report**: Files created/modified, methods implemented, any design decisions made.

### Impl-3B: Typeahead functions

**Scope**: Replace all 5 Postgres suggest functions with Typesense queries.

**Steps:**
1. Rewrite `suggestLocations()` in `apps/web/src/lib/actions/locations.ts`:
   - Query `location` collection with `query_by=name_${locale},name_en`, `query_by_weights=3,1`
   - Geo-sort: `coordinates(lat, lng, precision: 5km):asc` when user coords available
   - Fallback sort: `active_posting_count:desc`
   - Filter: `has_active_postings:true`
   - Remove Redis `cached()` wrapper (Typesense is fast enough)
   - Remove `_haversineKm()` helper function
   - Remove `_querySuggestions()` inner function — inline the Typesense query
2. Rewrite `suggestCompanies()` in `apps/web/src/lib/actions/company.ts`:
   - Query `company` collection, `query_by=name`, `filter_by=active_posting_count:>0`
   - Remove Redis cache
3. Rewrite `suggestOccupations()` in `apps/web/src/lib/actions/taxonomy.ts`:
   - Query `occupation` collection, `query_by=name,aliases`, `filter_by=has_active_postings:true && locale:${locale}`
   - Locale fallback: if 0 results, retry with `locale:en`
   - Extract `matchedName` from `hit.highlights` on `aliases` field
   - Remove Redis cache
4. Rewrite `suggestSeniorities()` — same pattern as occupations
5. Rewrite `suggestTechnologies()` — `query_by=name,slug`, `num_typos: 0` (prefix only), no locale filter
6. (Optional) Create unified `suggestAll()` server action:
   - Single `multi_search` with all 5 collection queries
   - Used by header search bar for single-roundtrip typeahead
   - Keep individual functions for filter modals
7. Rewrite browse-all modal functions using `facet_by` on `job_posting`:
   - `getGlobalLocationsGrouped()` in `actions/locations.ts`:
     - `facet_by: location_ids` with user's active filters → per-location filtered counts
     - Client-side hierarchy assembly using cached location parent_id relationships
     - Bottom-up count aggregation (cities → regions → countries)
   - `getAllOccupationsGrouped()` in `actions/taxonomy.ts`:
     - `facet_by: occupation_id` with filters → per-occupation filtered counts
     - Client-side domain grouping using cached occupation domain_id
   - `getAllSeniorities()` in `actions/taxonomy.ts`:
     - `facet_by: seniority_id` with filters → per-seniority filtered counts
     - Flat list, no hierarchy
   - `getAllTechnologiesGrouped()` in `actions/taxonomy.ts`:
     - `facet_by: technology_ids` with filters → per-technology filtered counts
     - Client-side category grouping using cached technology category
   - All four use the same pattern: single `per_page: 0` query with facet + filters, then resolve IDs to display names via lookup tables
   - Add graceful degradation (catch connection errors, return empty)

**Report**: Files modified, functions rewritten, cache wrappers removed.

### Impl-3C: Watchlist search

**Scope**: Replace watchlist search functions + add Typesense write hooks.

**Steps:**
1. Rewrite `searchCompaniesForWatchlist()` in `apps/web/src/lib/actions/company.ts`:
   - Step 1: Search `company` collection by name + industry filter
   - Step 2: `multi_search` for match counts (active + year per company)
   - Starred companies: two-query approach (starred first, then rest)
   - See `03-watchlist-search.md` for details
2. Rewrite `searchPublicWatchlists()` in `apps/web/src/lib/actions/watchlists.ts`:
   - Query `watchlist` collection, `query_by=title,description`, `filter_by=is_public:true`
3. Rewrite `getPopularWatchlists()`:
   - `q: "*"`, `sort_by=mirror_count:desc`, `per_page: 10`
4. Rewrite `getWatchlistPostings()` in `actions/watchlists.ts`:
   - Query `job_posting` collection with `company_id:[ids]` filter + user's active filters
   - NO `group_by` — current function returns a flat paginated list, not grouped by company. `response.found` gives the total count.
   - Result includes `source_url` (now in schema) for watchlist display
   - "Any company" mode: omit `company_id` filter
   - Add graceful degradation
5. Add Typesense write hooks to watchlist mutation actions:
   - Find watchlist create/update/delete actions
   - After Supabase write, upsert/delete in Typesense `watchlist` collection
   - Only index public watchlists
   - On visibility toggle: upsert if now public, delete if now private

**Report**: Files modified, write hooks added, search functions rewritten.

### Impl-3D: Cleanup Postgres search code

**Scope**: Remove old Postgres search implementation after 3A/3B/3C are complete.

**Depends on**: Impl-3A, Impl-3B, Impl-3C all complete.

**Steps:**
1. Delete `apps/web/src/lib/search/postgres.ts`
2. Remove unused imports in `apps/web/src/lib/search/index.ts`
3. Remove search-related Redis cache calls:
   - `cached()` wrappers in search.ts server actions that are now unnecessary
   - Keep `cached()` for non-search functions (e.g., `getPostingDetail`, `expandLocationIds`, `resolveLocationSlugs`)
4. Remove `_haversineKm()` from `locations.ts` (if not already removed by Impl-3B)
5. Check for any remaining references to `PostgresSearchProvider` — grep and remove
6. Verify no dead imports or unused variables

**Report**: Files deleted, cache calls removed, grep confirms no remaining references.

---

## Phase 4: E2E testing

Runs against local Typesense (http://localhost:8108) with backfilled data from Phase 2. Tests are actual test files committed to the repo, not manual verification steps. A Verify subagent writes the tests, an Impl subagent fixes any failures.

### Prerequisites

- Local Typesense running with all collections populated (Phase 2 complete)
- All search provider code implemented (Phase 3 complete)
- Local Postgres has real data (from crawler or seed)

### Subagent layout

```
Orchestrator
  ├── Impl-4A: E2E test suite (crawler/indexing)     ──┐
  ├── Impl-4B: E2E test suite (web/search)            │  (parallel — independent test files)
  │                                                     │
  ├── Verify-4A: Run crawler E2E tests                ──┤
  ├── Verify-4B: Run web E2E tests                    ──┤
  │                                                     │
  └── Impl-4Fix: Fix failures found by Verify         ──── (iterate until green)
```

### Impl-4A: Crawler E2E test suite

**Scope**: Write pytest tests that verify the indexing pipeline against local Typesense.

**File**: `apps/crawler/tests/e2e/test_typesense_indexing.py`

**Requires**: Local Typesense running, local Postgres with data. Tests are skipped if Typesense is unavailable (`pytest.mark.skipif`).

**Tests to write:**

```python
# ── Schema tests ──

def test_all_collections_exist():
    """All 7 collections (job_posting, location, occupation, seniority,
    technology, company, watchlist) are accessible via their aliases."""

def test_job_posting_schema_fields():
    """job_posting collection has all expected fields with correct types.
    Specifically: title (string), is_active (bool), location_ids (int32[]),
    salary_eur (int32, optional), coordinates NOT present (only on location)."""

def test_location_schema_has_geopoint():
    """location collection has 'coordinates' field of type 'geopoint'."""

def test_technology_schema_has_symbols():
    """technology collection has token_separators and symbols_to_index
    configured for +, #, . characters."""

# ── Indexing data integrity tests ──

def test_job_posting_count_matches_postgres():
    """Typesense job_posting doc count matches
    SELECT COUNT(*) FROM job_posting (within 1%)."""

def test_active_posting_count_matches_postgres():
    """Typesense is_active:true count matches
    SELECT COUNT(*) FROM job_posting WHERE is_active = true (within 1%)."""

def test_job_posting_denormalized_fields():
    """Sample 20 random postings from Typesense. For each:
    - title is non-empty
    - company_name matches company table lookup by company_id
    - location_names length == location_ids length (when both non-empty)
    - occupation_name matches occupation_name table (when occupation_id set)
    - seniority_name matches seniority_name table (when seniority_id set)
    - technology_names length == technology_ids length
    - first_seen_at is a valid unix timestamp > 0
    - salary_eur is None or > 0"""

def test_job_posting_timestamps_are_unix():
    """first_seen_at and last_seen_at are integers (unix timestamps),
    not ISO strings. Values should be > 1600000000 (post-2020)."""

def test_job_posting_sentinel_experience():
    """Postings where Postgres experience_min IS NULL should have
    experience_min = -1 in Typesense (sentinel value)."""

def test_job_posting_sentinel_locales():
    """Postings where Postgres locales = '{}' (empty) should have
    locales = ['_none'] in Typesense (sentinel value)."""

def test_job_posting_location_geo_types():
    """Sample 10 postings with location_ids. Each should have
    location_geo_types array of same length, with values in
    ['city', 'region', 'country', 'macro']."""

def test_job_posting_has_source_url():
    """Sample postings should have source_url field (string or null)."""

# ── Taxonomy collection tests ──

def test_location_collection_has_coordinates():
    """Sample 10 locations of type 'city'. Each should have a
    'coordinates' field that is a [lat, lng] array with lat in [-90, 90]
    and lng in [-180, 180]."""

def test_location_collection_multilingual():
    """Sample 5 locations. Each should have name_en populated.
    At least some should have name_de, name_fr, name_it populated."""

def test_occupation_collection_per_locale():
    """Occupation docs have a 'locale' field. For a known occupation slug,
    verify docs exist for at least 'en' and 'de' locales."""

def test_technology_special_characters():
    """Search for 'C++' in technology collection → returns a result.
    Search for 'C#' → returns a result. Search for '.NET' → returns a result."""

def test_company_collection_posting_counts():
    """Sample 5 companies from Typesense. Each with active_posting_count > 0
    should have a matching count in Postgres:
    SELECT COUNT(*) FROM job_posting WHERE company_id = $1 AND is_active."""

# ── Exporter CDC tests ──

def test_exporter_indexes_new_postings():
    """Insert a synthetic posting into local Postgres with a known title.
    Run one exporter tick. Verify the posting appears in Typesense
    with correct denormalized fields. Clean up after."""
```

### Impl-4B: Web E2E test suite

**Scope**: Write vitest tests that verify the TypesenseSearchProvider and all search functions against local Typesense.

**File**: `apps/web/src/lib/search/__tests__/typesense.e2e.test.ts`

**Requires**: Local Typesense running with populated data. Tests use the real TypesenseSearchProvider, not mocks.

**Tests to write:**

```typescript
// ── SearchProvider.search() ──

test("search with single keyword returns companies with matching postings", async () => {
  // Search for a common keyword like "Engineer"
  // Expect: companies array non-empty, each has postings with titles containing the keyword
  // Expect: totalCompanies > 0
});

test("search with multiple keywords ranks by relevance", async () => {
  // Search for "Senior React Developer"
  // Expect: results with more keyword matches in title rank higher
});

test("search with typo returns results via typo tolerance", async () => {
  // Search for "Develoer" (missing 'p')
  // Expect: still returns developer positions
});

test("search with location filter restricts results", async () => {
  // Search with a known location ID
  // Expect: all returned postings have that location_id in their location_ids array
});

test("search with salary range filter works", async () => {
  // Search with salaryMinEur=50000, salaryMaxEur=100000
  // Expect: all returned postings have salary_eur in range (where set)
});

test("search with multiple filters combines with AND", async () => {
  // Search with keyword + location + occupation + seniority
  // Expect: results satisfy all filters simultaneously
});

test("search with no results returns empty", async () => {
  // Search for "xyznonexistentkeyword12345"
  // Expect: { companies: [], totalCompanies: 0 }
});

test("search pagination works", async () => {
  // Search with offset=0, limit=5, then offset=5, limit=5
  // Expect: different company sets, no overlap
});

// ── SearchProvider.listTopCompanies() ──

test("listTopCompanies returns companies sorted by posting count", async () => {
  // No keywords, just filters
  // Expect: companies sorted by activeMatches descending
});

test("listTopCompanies with filters restricts results", async () => {
  // Apply location filter
  // Expect: only companies with postings in that location
});

// ── SearchProvider.loadPostings() ──

test("loadPostings returns postings for a specific company", async () => {
  // Pick a known company_id
  // Expect: all postings belong to that company
});

test("loadPostings with keywords sorts by relevance", async () => {
  // Load postings with keyword filter
  // Expect: postings with keyword in title rank first
});

test("loadPostings without keywords sorts by recency", async () => {
  // Load postings without keywords
  // Expect: postings sorted by firstSeenAt descending
});

// ── SearchProvider.loadPostingsWithCounts() ──

test("loadPostingsWithCounts returns activeCount and yearCount", async () => {
  // Pick a known company_id
  // Expect: activeCount > 0, yearCount >= activeCount
  // Expect: postings array populated
});

// ── Histograms ──

test("getSalaryHistogram returns 10K EUR buckets", async () => {
  // No filters
  // Expect: array of { min, max, count } buckets
  // Expect: bucket width is 10000 (max - min)
  // Expect: at least some buckets have count > 0
});

test("getSalaryHistogram respects filters", async () => {
  // With location filter
  // Expect: total count across buckets <= unfiltered total
});

test("getExperienceHistogram returns year buckets", async () => {
  // No filters
  // Expect: array of { years, count }
  // Expect: years are non-negative integers
  // Expect: at least some buckets have count > 0
});

// ── Typeahead: suggestLocations() ──

test("suggestLocations returns prefix matches", async () => {
  // Query "Ber"
  // Expect: results include Berlin (or other Ber* cities)
});

test("suggestLocations respects locale", async () => {
  // Query "Münch" with locale=de
  // Expect: München in results
});

test("suggestLocations handles typos", async () => {
  // Query "Zurich" (no umlaut)
  // Expect: Zürich in results via typo tolerance
});

test("suggestLocations geo-sorts when coordinates provided", async () => {
  // Query "B" with coords near Berlin (52.52, 13.40)
  // Expect: Berlin ranks higher than Barcelona or Budapest
});

test("suggestLocations includes parentName", async () => {
  // Query for a city
  // Expect: parentName is set (country or region name)
});

test("suggestLocations returns correct type field", async () => {
  // Expect: each result has type in ["macro", "country", "region", "city"]
});

test("suggestLocations only returns locations with active postings", async () => {
  // Expect: all results have has_active_postings = true
});

// ── Typeahead: suggestCompanies() ──

test("suggestCompanies returns prefix matches", async () => {
  // Query first 3 chars of a known company name
  // Expect: that company in results
});

test("suggestCompanies only returns companies with active postings", async () => {
  // All results should have active_posting_count > 0
});

// ── Typeahead: suggestOccupations() ──

test("suggestOccupations returns prefix matches", async () => {
  // Query "Develop"
  // Expect: developer-related occupations in results
});

test("suggestOccupations matches aliases and sets matchedName", async () => {
  // Query a known alias (e.g., "Softwareentwickler" for locale=de)
  // Expect: result with matchedName set to the alias
});

test("suggestOccupations falls back to locale=en", async () => {
  // Query with locale=fr for something only available in en
  // Expect: still returns result (en fallback)
});

// ── Typeahead: suggestSeniorities() ──

test("suggestSeniorities returns prefix matches", async () => {
  // Query "Sen"
  // Expect: "Senior" in results
});

// ── Typeahead: suggestTechnologies() ──

test("suggestTechnologies returns prefix matches", async () => {
  // Query "Pyth"
  // Expect: "Python" in results
});

test("suggestTechnologies handles special characters", async () => {
  // Query "C++" → expect C++ result
  // Query "C#" → expect C# result
  // Query ".NET" → expect .NET result
});

test("suggestTechnologies does not fuzzy match (prefix only)", async () => {
  // Query "Pyhton" (typo)
  // Expect: no results (num_typos: 0)
});

// ── Watchlist search ──

test("searchCompaniesForWatchlist returns companies by name", async () => {
  // Query with known company name prefix
  // Expect: matching companies with activeMatches/yearMatches populated
});

test("searchCompaniesForWatchlist filters by industry", async () => {
  // Query with industryId set
  // Expect: all results have matching industry
});

test("searchPublicWatchlists searches title and description", async () => {
  // Requires at least one public watchlist in Typesense
  // Query with word from a known watchlist title
  // Expect: that watchlist in results
});

test("searchPublicWatchlists only returns public watchlists", async () => {
  // All results should have is_public = true
});

// ── Watchlist postings ──

test("getWatchlistPostings returns postings scoped to company IDs", async () => {
  // Pick 3 company IDs, query watchlist postings
  // Expect: all returned postings belong to one of the 3 companies
});

test("getWatchlistPostings applies filters", async () => {
  // Query with location filter
  // Expect: all returned postings have matching location_id
});

test("getWatchlistPostings includes source_url", async () => {
  // Expect: each posting has source_url field (string or null)
});

// ── Browse-all modals (faceted counts) ──

test("getGlobalLocationsGrouped returns filtered counts", async () => {
  // Query with no filters → get location facet counts
  // Expect: non-empty array of { locationId, count }
  // Expect: counts are positive integers
});

test("getGlobalLocationsGrouped respects filters", async () => {
  // Query with occupation filter → get location facet counts
  // Expect: total count across all locations <= unfiltered total
});

test("getAllOccupationsGrouped returns filtered counts", async () => {
  // Query with no filters → get occupation facet counts
  // Expect: non-empty results
});

test("getAllSeniorities returns filtered counts", async () => {
  // Query with no filters → get seniority facet counts
  // Expect: non-empty results
});

test("getAllTechnologiesGrouped returns filtered counts", async () => {
  // Query with no filters → get technology facet counts
  // Expect: non-empty results
});

// ── Sentinel value tests ──

test("experience filter includes jobs without experience requirement", async () => {
  // Search with experienceMax=5
  // Expect: results include postings with experience_min=-1 (sentinel for NULL)
  // Verify these are jobs that genuinely have no experience requirement in Postgres
});

test("language filter includes jobs with no detected language", async () => {
  // Search with languages=["en"]
  // Expect: results include postings with locales=["_none"] (sentinel for empty)
});

// ── Graceful degradation ──

test("search returns empty results when Typesense is unreachable", async () => {
  // Point client at wrong host/port
  // Expect: { companies: [], totalCompanies: 0, degraded: true }
  // Expect: no thrown exception
});
```

### Verify-4A: Run crawler E2E tests

**Scope**: Execute the crawler E2E test suite, report results.

**Steps:**
1. Ensure local Typesense is running and populated
2. Run: `cd apps/crawler && uv run pytest tests/e2e/test_typesense_indexing.py -v`
3. Report: number of tests passed/failed/skipped, full output of any failures

**Gate**: All tests pass.

### Verify-4B: Run web E2E tests

**Scope**: Execute the web E2E test suite, report results.

**Steps:**
1. Ensure local Typesense is running and populated
2. Run: `cd apps/web && pnpm vitest run src/lib/search/__tests__/typesense.e2e.test.ts`
3. Report: number of tests passed/failed/skipped, full output of any failures

**Gate**: All tests pass.

### Impl-4Fix: Fix failures

**Scope**: Fix any test failures found by Verify-4A/4B.

**Depends on**: Verify-4A or Verify-4B reporting failures.

**Steps:**
1. Read the failure output from the Verify subagent
2. Diagnose: is it a test issue (bad assertion) or an implementation bug?
3. Fix the root cause
4. Re-run the failing test to confirm the fix

**Iterate**: Orchestrator re-runs Verify after each fix round until all tests pass.

---

## Phase 5: Production deployment

### Subagent layout

```
Orchestrator
  ├── Impl-5A: Provision Hetzner + deploy Typesense
  ├── Impl-5B: Deploy crawler (exporter + sync)
  ├── Impl-5C: Deploy web app
  │
  └── Verify-5: Production smoke test         ──── Gate: production working
```

### Impl-5A: Provision Hetzner + deploy Typesense

**Steps:**
1. Provision Hetzner CX22 (4 GB RAM, dedicated IPv4, disk backups)
2. Install Docker, deploy Typesense container (port 8108 bound to localhost only)
3. Configure firewall: port 8108 open to crawler machine IP only (not public)
4. Set up Cloudflare tunnel for web app access:
   - `cloudflared tunnel create typesense`
   - Configure tunnel to route `typesense.yourdomain.com` → `localhost:8108`
   - Install as systemd service (auto-start on reboot)
   - Verify tunnel health from external network
5. Set up TLS for crawler-to-Typesense (Caddy reverse proxy or Typesense built-in SSL)
6. Generate production API keys (admin + search-only)
7. Run `scripts/typesense-setup.py` to create collections
8. Add keys + Cloudflare tunnel hostname to GitHub secrets + env files + Vercel env vars

**Gate**: 
- From crawler: `curl -s https://<direct-ip>:8108/health` returns `{"ok": true}`
- From internet: `curl -s https://typesense.yourdomain.com/health -H "X-TYPESENSE-API-KEY: $SEARCH_KEY"` returns `{"ok": true}`

### Impl-5B: Deploy crawler + backfill

**Depends on**: Impl-5A complete.

**Steps:**
1. Merge crawler changes to main
2. Deploy crawler with production Typesense env vars
3. Run sync to populate taxonomy + company collections
4. Run backfill to populate job_posting collection
5. **Gate: wait for backfill to complete** — verify `num_documents` matches Postgres count within 1%
6. Verify exporter logs show Typesense upserts succeeding

### Impl-5C: Deploy web app

**Depends on**: Impl-5B complete AND backfill verified (index must be fully populated before the web app queries it — otherwise users see biased partial results during the 8-minute backfill window).

**Steps:**
1. Merge web app changes to main
2. Deploy with production Typesense env vars
3. Verify no build errors

### Verify-5: Production smoke test

**Depends on**: Impl-5B and Impl-5C both deployed.

**Steps:**
1. Hit the live site — perform a keyword search, verify results load
2. Test typeahead — type in the search bar, verify suggestions appear within 100ms
3. Test filter modals — location search, occupation search
4. Test browse mode — verify top companies page loads
5. Check Typesense RAM usage: `GET /stats.json` — confirm under 80%
6. Check exporter metrics: verify Typesense export lag < 10s
7. Monitor error rates for 1 hour — no 5xx spikes

**Gate**: Site works, search feels fast, no errors, RAM under control.

---

## Phase 6: Cleanup

Single Impl subagent, no verification needed (these are deletions).

### Impl-6: Remove legacy search infrastructure

**Steps:**
1. Drop unused Supabase GIN indexes on `location_ids`, `technology_ids` (if only used by search)
2. Drop Supabase trigram extension + similarity indexes (if no other consumers)
3. Remove stale Redis cache keys (`search:*`, `loc-suggest:*`, `company-suggest:*`, `occ-suggest:*`, `sen-suggest:*`, `tech-suggest:*`, `salary-histogram:*`, `experience-histogram:*`)
4. Update `CLAUDE.md` architecture section
5. Update any docs referencing Postgres search

---

## Execution summary

```
Phase 1: Local Typesense deployment                   (orchestrator, sequential)
  │
Phase 2: Indexing pipeline
  ├── Impl-2A  → Verify-2A                            (schema setup)
  ├── Impl-2B ─┐                                      (exporter + sync, parallel)
  ├── Impl-2C ─┤→ Verify-2B, Verify-2C
  │            └→ Verify-2D                            (backfill + parity check)
  │
Phase 3: Search provider implementation
  ├── Impl-3A ─┐                                      (search + typeahead + watchlist, parallel)
  ├── Impl-3B ─┤
  ├── Impl-3C ─┘
  └── Impl-3D                                         (cleanup, after 3A+3B+3C)
  │
Phase 4: E2E testing
  ├── Impl-4A ─┐                                      (write test suites, parallel)
  ├── Impl-4B ─┘
  ├── Verify-4A, Verify-4B                            (run tests)
  └── Impl-4Fix → Verify loop                         (fix + re-run until green)
  │
Phase 5: Production deployment
  ├── Impl-5A → Impl-5B → Impl-5C                    (sequential: infra → crawler → web)
  └── Verify-5                                         (smoke test)
  │
Phase 6: Cleanup
  └── Impl-6                                           (delete legacy code)
```

### Parallelism map

| Time | Slot 1 | Slot 2 | Slot 3 |
|------|--------|--------|--------|
| Phase 1 | Orchestrator: docker-compose up | | |
| Phase 2a | Impl-2A (schemas) | | |
| Phase 2a verify | Verify-2A | | |
| Phase 2b | Impl-2B (exporter) | Impl-2C (sync) | |
| Phase 2b verify | Verify-2B | Verify-2C | |
| Phase 2c | Verify-2D (backfill) | | |
| Phase 3 | Impl-3A (search provider) | Impl-3B (typeahead) | Impl-3C (watchlist) |
| Phase 3 cleanup | Impl-3D | | |
| Phase 4 write | Impl-4A (crawler tests) | Impl-4B (web tests) | |
| Phase 4 run | Verify-4A | Verify-4B | |
| Phase 4 fix | Impl-4Fix → Verify loop | | |
| Phase 5 | Impl-5A → 5B → 5C → Verify-5 | | |
| Phase 6 | Impl-6 | | |

Maximum 3 concurrent subagents (Phase 3). E2E test suites are written in parallel (2 slots), then run, then failures fixed iteratively.
