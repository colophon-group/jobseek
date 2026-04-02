# Development Plan — Agent Work Distribution

Phase-based plan with work distributed across implementation and verification subagents. Follows the repo's parallel orchestration pattern (independent tracks, convergence gates, evidence-based verification).

## Agent roles

| Role | Responsibility |
|------|---------------|
| **Orchestrator** (main agent) | Sequences phases, spawns subagents, gates convergence, makes trade-off decisions |
| **Impl** subagent | Writes code, creates files, runs commands. Reports what was done + any issues. |
| **Verify** subagent | Reads code, runs tests, queries APIs, compares outputs. Reports pass/fail with evidence. |

Impl and Verify never run in the same subagent — separation ensures the person building it isn't the one checking it.

---

## Phase 1: Infrastructure

Manual/ops phase. Orchestrator runs this directly (no subagents — sequential commands with verification between steps).

### Steps

1. Provision Hetzner CX22 (4 GB RAM, dedicated IPv4, disk backups)
2. SSH in, install Docker
3. Deploy Typesense container (docker-compose.yml from master plan)
4. Configure firewall: port 8108 open to crawler IP + web app IP only
5. Set up TLS termination (Caddy reverse proxy)
6. Generate API keys:
   - `TYPESENSE_ADMIN_KEY` (exporter + sync)
   - `TYPESENSE_SEARCH_KEY` (web app, search-only scope)
7. Add keys to GitHub secrets + crawler env file + web app env

### Gate: Infrastructure ready

```bash
# From crawler machine
curl -s https://<typesense-host>/health -H "X-TYPESENSE-API-KEY: $TYPESENSE_ADMIN_KEY"
# → {"ok": true}

# From web app machine
curl -s https://<typesense-host>/health -H "X-TYPESENSE-API-KEY: $TYPESENSE_SEARCH_KEY"
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
1. Run `scripts/typesense-setup.py` against the Typesense instance
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
3. After `_export_changed_postings()` Supabase upsert, add `_index_to_typesense(rows)`:
   - For each row, build Typesense document:
     - Denormalize names from in-memory maps
     - Convert `first_seen_at` / `last_seen_at` to Unix timestamps
     - Set `company_icon` from company map
   - Batch upsert via `documents.import_(docs, {"action": "upsert"})`
   - On failure: log error, do NOT block Supabase export or cursor advance
4. Add `--backfill-typesense` CLI flag to `cli.py`:
   - Iterates all `job_posting` rows (not just changed ones)
   - Same denormalization + upsert logic
   - Progress logging every 10K rows
5. Add taxonomy map refresh: reload maps every 10 minutes (or on SIGHUP)

**Report**: Files modified, new functions, error handling approach.

### Impl-2C: Sync Typesense integration

**Scope**: Extend `apps/crawler/src/sync.py` to write taxonomy, company, and initial watchlist data to Typesense.

**Steps:**
1. After each taxonomy sync block, add Typesense bulk upsert:
   - **Locations**: Query all locations + all locale names + lat/lng. Build one doc per location with `name_en`, `name_de`, `name_fr`, `name_it`, `coordinates: [lat, lng]`. Count active postings per location. Upsert to `location` collection.
   - **Occupations**: Query all (occupation, locale) pairs with display names + aliases. Count active postings. Upsert to `occupation` collection (one doc per entity+locale, id = `{occupation_id}-{locale}`).
   - **Seniorities**: Same pattern as occupations.
   - **Technologies**: Query all technologies. Count active postings. Upsert to `technology` collection.
2. After company sync, upsert to `company` collection:
   - Name, slug, icon, industry_id, industry_name
   - `active_posting_count` and `year_posting_count` from job_posting counts
3. Add public watchlist sync:
   - Query all public watchlists from Supabase (or local if available)
   - Build docs with title, description, owner info, counts
   - Upsert to `watchlist` collection
4. Add `active_posting_count` refresh as a standalone function callable by a periodic job

**Report**: Files modified, collections populated, document counts per collection.

### Verify-2B: Verify exporter indexing

**Scope**: Run the exporter and confirm job postings appear in Typesense with correct denormalized data.

**Steps:**
1. Run exporter for ~30 seconds (let it process a few batches)
2. Query Typesense: `GET /collections/job_posting/documents/search?q=*&per_page=5`
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

**Scope**: Run sync and confirm taxonomy + company collections are populated correctly.

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

**Scope**: Run full backfill and verify data parity with Postgres.

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
   -- Typesense
   GET /collections/job_posting → num_documents
   GET /collections/job_posting/documents/search?q=*&filter_by=is_active:true&per_page=0 → found
   ```
4. Counts should match within 0.1% (some postings may be updated during backfill)
5. Spot-check 10 random postings: fetch by ID from both Postgres and Typesense, compare fields

**Gate**: Total count parity, active count parity, spot-check fields match.

---

## Phase 3: Search provider

### Subagent layout

```
Orchestrator
  ├── Impl-3A: TypesenseSearchProvider        ──┐
  │   (search, listTopCompanies, loadPostings,  │
  │    loadPostingsWithCounts, histograms)       │
  │                                              │
  ├── Impl-3B: Typeahead functions              │  (parallel — independent code)
  │   (5 suggest functions + multi_search)       │
  │                                              │
  ├── Impl-3C: Watchlist search                 │
  │   (searchCompaniesForWatchlist,              │
  │    searchPublicWatchlists, write hooks)      │
  │                                              │
  ├── Impl-3D: Cleanup Postgres search code     ──── (after 3A/3B/3C complete)
  │                                              │
  ├── Verify-3A: Search provider correctness    ──┘
  ├── Verify-3B: Typeahead correctness          ──┘
  └── Verify-3C: Watchlist search correctness   ──┘
```

**Dependency**: Impl-3A, 3B, 3C are independent and run in parallel. Impl-3D runs after all three complete. Verify subagents can start as soon as their corresponding Impl finishes (don't wait for all Impl to complete).

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
   - Always includes `is_active:true`
   - Maps each filter dimension (locationIds, occupationIds, salary range, etc.)
   - See `01-job-search.md` for exact mapping
4. Create `typesense.ts` implementing `SearchProvider`:
   - `search()` — `multi_search` with `q: keywords.join(" ")`, `group_by: "company_id"`, `group_limit: 10`
   - `listTopCompanies()` — two-step: query `company` collection sorted by `active_posting_count`, then fetch postings per company
   - `loadPostings()` — filter by `company_id`, sort by text match or recency
   - `loadPostingsWithCounts()` — `loadPostings()` + count queries via `multi_search` (active + year counts)
   - `getSalaryHistogram()` — faceted range query on `salary_eur` with 30 boundaries (0, 10K, ..., 300K)
   - `getExperienceHistogram()` — facet on `experience_min`, transform to `ExperienceBucket[]`
5. Update `apps/web/src/lib/search/index.ts`:
   - Replace `PostgresSearchProvider` with `TypesenseSearchProvider` directly (no toggle)

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
4. Add Typesense write hooks to watchlist mutation actions:
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

### Verify-3A: Search provider correctness

**Scope**: Compare Typesense search results against known Postgres results for a set of test queries.

**Depends on**: Impl-3A complete.

**Steps:**
1. Prepare 10 test queries covering:
   - Single keyword ("Python")
   - Multi-keyword ("Senior React Developer")
   - Keyword + location filter
   - Keyword + salary range
   - Keyword + multiple filters (occupation + seniority + technology)
   - Empty keyword (browse mode / listTopCompanies)
   - Keyword with typo ("Pythno", "Deveolper")
   - Very specific query (should return few results)
   - Very broad query (should return many results)
   - loadPostings for a specific company
2. For each query, call the TypesenseSearchProvider methods and verify:
   - `companies` array is non-empty (for queries that should match)
   - `totalCompanies` is reasonable
   - Each company has `postings` array with valid posting objects
   - `posting.title` is non-empty
   - `posting.locations` is an array of `{ name, type }` objects
   - `posting.firstSeenAt` is a valid date
   - `posting.relevanceScore` is present for keyword queries
3. Test histograms:
   - `getSalaryHistogram()` returns array of `{ min, max, count }` buckets
   - Bucket boundaries are 10K apart (0-10K, 10K-20K, ..., 290K-300K)
   - `getExperienceHistogram()` returns array of `{ years, count }`
   - Both return non-empty arrays for unfiltered queries
4. Test edge cases:
   - Empty keyword search → delegates to listTopCompanies behavior
   - No matching results → `{ companies: [], totalCompanies: 0 }`
   - Very large offset → empty results, no error

**Gate**: All 10 queries return valid results, histograms work, edge cases handled.

### Verify-3B: Typeahead correctness

**Scope**: Verify all 5 suggest functions return correct results from Typesense.

**Depends on**: Impl-3B complete.

**Steps:**
1. **suggestLocations**:
   - Query "Ber" → should return Berlin (prefix match)
   - Query "Münch" with locale=de → should return München (German locale match)
   - Query "Munich" with locale=en → should return Munich
   - Query with user coords near Berlin → Berlin should rank higher than other "Ber*" matches
   - Query "Zurich" (without umlaut) → should match Zürich via typo tolerance
   - Verify `parentName` is populated (e.g., "Germany" for Berlin)
   - Verify `type` field is one of macro/country/region/city
2. **suggestCompanies**:
   - Query known company name prefix → should return the company
   - Verify `icon` field is present (or null)
   - Verify only companies with active postings are returned
3. **suggestOccupations**:
   - Query "Develop" → should return developer-related occupations
   - Query with locale=de → should return German names
   - Query alias (e.g., "Softwareentwickler") → should match and set `matchedName`
   - Verify locale fallback: query gibberish with locale=fr → retry with locale=en
4. **suggestSeniorities**:
   - Query "Sen" → should return Senior
   - Verify locale filtering works
5. **suggestTechnologies**:
   - Query "Pyth" → should return Python
   - Query "C++" → should return C++ (symbols_to_index test)
   - Query "c#" → should return C#
   - Query ".net" → should return .NET
   - Verify `num_typos: 0` — "Pyhton" should NOT match (prefix only, no fuzzy)

**Gate**: All suggest functions return correct results, locale handling works, special characters handled.

### Verify-3C: Watchlist search correctness

**Scope**: Verify watchlist search and write hooks.

**Depends on**: Impl-3C complete.

**Steps:**
1. **searchCompaniesForWatchlist**:
   - Search by company name → returns matching companies
   - Filter by industry → only matching industry companies returned
   - Verify `activeMatches` and `yearMatches` counts are populated
   - Test with starredCompanyIds → starred companies appear first
2. **searchPublicWatchlists**:
   - Search by watchlist title → returns matching public watchlists
   - Search by description keywords → returns matches
   - Verify only public watchlists returned (`is_public: true`)
   - Verify `companyCount`, `activeJobCount` are populated
3. **Write hooks** (if testable in dev):
   - Create a public watchlist → verify it appears in Typesense within seconds
   - Update watchlist title → verify Typesense doc updated
   - Toggle visibility to private → verify deleted from Typesense
   - Toggle back to public → verify re-indexed
4. **getPopularWatchlists**:
   - Returns watchlists sorted by `mirror_count` desc
   - All results are public

**Gate**: Search functions return correct results, write hooks work for CRUD + visibility toggle.

---

## Phase 4: Deploy

### Subagent layout

```
Orchestrator
  ├── Impl-4A: Deploy crawler (exporter + sync)
  ├── Impl-4B: Deploy web app
  │
  └── Verify-4: Production smoke test         ──── Gate: production working
```

### Impl-4A: Deploy crawler

**Steps:**
1. Merge crawler changes to main
2. Deploy crawler with new Typesense env vars
3. Verify exporter logs show Typesense upserts succeeding
4. Run sync to populate taxonomy + company collections

### Impl-4B: Deploy web app

**Depends on**: Impl-4A complete (index must be populated before web app queries it).

**Steps:**
1. Merge web app changes to main
2. Deploy with Typesense env vars (`TYPESENSE_HOST`, `TYPESENSE_PORT`, `TYPESENSE_PROTOCOL`, `TYPESENSE_SEARCH_KEY`)
3. Verify no build errors

### Verify-4: Production smoke test

**Depends on**: Impl-4A and Impl-4B both deployed.

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

## Phase 5: Cleanup

Single Impl subagent, no verification needed (these are deletions).

### Impl-5: Remove legacy search infrastructure

**Steps:**
1. Drop unused Supabase GIN indexes on `location_ids`, `technology_ids` (if only used by search)
2. Drop Supabase trigram extension + similarity indexes (if no other consumers)
3. Remove stale Redis cache keys (`search:*`, `loc-suggest:*`, `company-suggest:*`, `occ-suggest:*`, `sen-suggest:*`, `tech-suggest:*`, `salary-histogram:*`, `experience-histogram:*`)
4. Update `CLAUDE.md` architecture section
5. Update any docs referencing Postgres search

---

## Execution summary

```
Phase 1: Infrastructure                          (orchestrator, sequential)
  │
Phase 2: Indexing pipeline
  ├── Impl-2A  → Verify-2A                       (schema setup)
  ├── Impl-2B ─┐                                 (exporter + sync, parallel)
  ├── Impl-2C ─┤→ Verify-2B, Verify-2C
  │            └→ Verify-2D                       (backfill + parity check)
  │
Phase 3: Search provider
  ├── Impl-3A ──→ Verify-3A                       (core search, parallel)
  ├── Impl-3B ──→ Verify-3B                       (typeahead, parallel)
  ├── Impl-3C ──→ Verify-3C                       (watchlist, parallel)
  └── Impl-3D                                     (cleanup, after 3A+3B+3C)
  │
Phase 4: Deploy
  ├── Impl-4A → Impl-4B → Verify-4               (sequential: crawler first)
  │
Phase 5: Cleanup
  └── Impl-5                                      (delete legacy code)
```

### Parallelism map

| Time | Slot 1 | Slot 2 | Slot 3 |
|------|--------|--------|--------|
| Phase 2a | Impl-2A | | |
| Phase 2a verify | Verify-2A | | |
| Phase 2b | Impl-2B (exporter) | Impl-2C (sync) | |
| Phase 2b verify | Verify-2B | Verify-2C | |
| Phase 2c | Verify-2D (backfill) | | |
| Phase 3a | Impl-3A (search) | Impl-3B (typeahead) | Impl-3C (watchlist) |
| Phase 3a verify | Verify-3A | Verify-3B | Verify-3C |
| Phase 3b | Impl-3D (cleanup) | | |
| Phase 4 | Impl-4A → Impl-4B → Verify-4 | | |
| Phase 5 | Impl-5 | | |

Maximum 3 concurrent subagents (Phase 3a). Most of the wall-clock time savings come from parallelizing the three independent search provider tracks.
