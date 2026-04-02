# Watchlist Search Migration

Migrates two search surfaces: the company search modal (used when adding companies to a watchlist) and the public watchlist discovery search.

## Current implementation

### searchCompaniesForWatchlist()

**File**: `apps/web/src/lib/actions/company.ts` (lines 85–230)

**What it does:**
- Paginated company search with optional text query + industry filter
- Computes per-company match counts (active/year) using the user's current watchlist filters (keywords, locations, occupations, etc.)
- Sorts starred companies first (if provided), then by active_matches
- Used by the "Add Company" modal in watchlist management

**Input params:**
- `query?: string` — company name search (min 2 chars)
- `industryId?: number` — filter by industry
- `locale: string`
- `offset, limit` (page size 20)
- Watchlist context filters: `keywords, locationIds, occupationIds, seniorityIds, technologyIds, salaryMin, salaryMax, experienceMin, experienceMax`
- `starredCompanyIds?: string[]` — companies already in the watchlist (sort first)

**Query logic:**
- Company matching: prefix + fuzzy (similarity > 0.3)
- Match counting: subquery counts active job_posting rows matching all filters per company, with window function for `filtered_total`
- Zero-match companies filtered out in SQL (`WHERE active_matches > 0 OR year_matches > 0`)
- Two SQL paths: starred-first (no text query) vs. text search

**Output:** `{ companies: Array<{ id, name, slug, icon, description, activeMatches, yearMatches }>, total }`

**Caching:** None

### searchPublicWatchlists()

**File**: `apps/web/src/lib/actions/watchlists.ts` (lines 547–561)

**What it does:**
- Simple ILIKE search on watchlist `title` and `description`
- Only public watchlists
- Ordered by `created_at DESC`

**Input params:** `query, offset, limit`

**Output:** `{ watchlists: Array<{ id, slug, title, description, isPublic, companyCount, activeJobCount, ownerName, ownerUsername, mirrorCount, ... }>, total }`

**Caching:** None

## Typesense implementation

### searchCompaniesForWatchlist()

This is the most complex migration because it combines company text search with job posting filter context (match counts).

**Approach: Two-step query**

Step 1 — Search companies by name in Typesense:

```typescript
{
  collection: "company",
  q: query || "*",
  query_by: "name",
  filter_by: buildCompanyFilterString({ industryId }),
  sort_by: query
    ? "_text_match:desc,active_posting_count:desc"
    : "active_posting_count:desc",
  per_page: limit,
  page: Math.floor(offset / limit) + 1,
  prefix: true,
  num_typos: 1,
}
```

Step 2 — For the returned companies, compute match counts via Typesense job_posting queries:

```typescript
// Batch: one search per company for active matches
const matchCountSearches = companyIds.map(companyId => ({
  collection: "job_posting",
  q: keywords?.length ? keywords.join(" ") : "*",
  query_by: "title",
  filter_by: `company_id:=${companyId} && is_active:true${filterStr ? " && " + filterStr : ""}`,
  per_page: 0,  // counts only
}));

const yearCountSearches = companyIds.map(companyId => ({
  collection: "job_posting",
  q: keywords?.length ? keywords.join(" ") : "*",  // Postgres year count DOES filter by keywords
  query_by: "title",
  filter_by: `company_id:=${companyId} && first_seen_at:>${oneYearAgoUnix}${filterStr ? " && " + filterStr : ""}`,
  per_page: 0,
}));

const response = await typesense.multiSearch.perform({
  searches: [...matchCountSearches, ...yearCountSearches],
});
```

**Zero-match filtering via facet approach**: The over-fetch + post-filter approach breaks pagination (the client's post-filter offset doesn't map to Typesense's pre-filter pages). Instead, when watchlist context filters are active, use the same facet-based approach as `listTopCompanies()`:

```typescript
// When filters are active: get companies ranked by filtered match count
const filterStr = buildFilterString(watchlistFilters);
const facetResult = await typesense.collections("job_posting").documents().search({
  q: "*",
  filter_by: `is_active:true${filterStr ? " && " + filterStr : ""}`,
  facet_by: "company_id",
  facet_strategy: "exhaustive",
  max_facet_values: offset + limit,
  per_page: 0,
});
// facet_counts gives company IDs ranked by filtered posting count
// Only companies with >0 matching postings appear — zero-match filtering is implicit
```

This eliminates the zero-match problem entirely — facets only return companies that have matching postings. Then apply the text query filter on those company IDs (step 1 of the original approach) if the user typed a name. `totalCompanies` comes from `facet_counts[0].stats.total_values`.

When no watchlist context filters are active, use the original company collection approach (step 1) since all active companies are relevant.

**Note on multi_search limits**: When using the facet approach (filtered case), the N×2 per-company count queries are eliminated — the facet itself gives per-company active counts. Year counts come from pre-computed `year_posting_count` on the company collection (approximate, acceptable). When using the original per-company multi_search approach (step 2 for non-facet paths), the default limit is 50 searches. With page size 20 × 2 count queries = 40 searches, this fits. If page size increases, batch into multiple calls.

**Starred companies sorting**: If `starredCompanyIds` is provided and no text query, do two Typesense queries:
1. Fetch starred companies: `filter_by: "id:[${starredCompanyIds.join(',')}]"`
2. Fetch remaining companies: `filter_by: "id:!=[${starredCompanyIds.join(',')}]"`
Concatenate results. **Both sets need match counts** — starred companies must go through the same count computation (facet or multi_search) to populate `activeMatches` / `yearMatches`. Don't skip count computation just because they're starred.

**No text query + watchlist filters**: When there's no text query but the user has active watchlist filters (keywords, locations, etc.), company ranking must be by **filtered** match count, not global `active_posting_count`. Use the same match count queries from step 2 to sort results. Without this, a company with 1000 total jobs but 0 filter-matching jobs would rank above one with 50 matching jobs.

**Location/occupation expansion**: The current `searchCompaniesForWatchlist()` calls `expandLocationIds()` and `expandOccupationIds()` on the watchlist context filters before computing match counts. The Typesense version must do the same — without expansion, selecting "Germany" as a watchlist location filter would miss postings in Berlin, Munich, etc.

**Industry filter:**

```typescript
function buildCompanyFilterString({ industryId }: { industryId?: number }): string {
  const parts: string[] = ["active_posting_count:>0"];
  if (industryId) parts.push(`industry_id:${industryId}`);
  return parts.join(" && ");
}
```

**Trade-off**: The match count computation requires N+1 queries (1 company search + N×2 count queries). This is more round-trips than the current single Postgres query with subqueries. However:
- All queries go via `multi_search` (single HTTP request)
- Typesense processes them in parallel
- Each individual query is <5ms
- Total expected latency: 10–30ms (vs 100–500ms for current Postgres with subqueries + similarity)

If performance is a concern at scale, consider pre-computing match counts and storing them on the company document (refreshed periodically).

### searchPublicWatchlists()

**Typesense query:**

```typescript
{
  collection: "watchlist",
  q: query,
  query_by: "title,description",
  filter_by: "is_public:true",
  sort_by: "_text_match:desc,created_at:desc",
  per_page: limit,
  page: Math.floor(offset / limit) + 1,
  prefix: true,
  num_typos: 1,
}
```

**Result mapping:**

```typescript
function mapWatchlistHit(hit: TypesenseHit): PublicWatchlist {
  const doc = hit.document;
  return {
    id: doc.id,
    slug: doc.slug,
    title: doc.title,
    description: doc.description ?? null,
    isPublic: true,
    companyCount: doc.company_count,
    activeJobCount: doc.active_job_count,
    ownerName: doc.owner_name,
    ownerUsername: doc.owner_username ?? null,
    mirrorCount: doc.mirror_count,
    createdAt: new Date(doc.created_at * 1000).toISOString(),
  };
}
```

**Updated watchlist collection schema** (extends master plan):

```json
{
  "name": "watchlist",
  "fields": [
    { "name": "id",              "type": "string" },
    { "name": "slug",            "type": "string",  "index": false },
    { "name": "title",           "type": "string" },
    { "name": "description",     "type": "string",  "optional": true },
    { "name": "owner_name",      "type": "string" },
    { "name": "owner_username",  "type": "string",  "optional": true, "index": false },
    { "name": "company_count",   "type": "int32" },
    { "name": "active_job_count","type": "int32" },
    { "name": "mirror_count",    "type": "int32" },
    { "name": "created_at",      "type": "int64" },
    { "name": "is_public",       "type": "bool",   "facet": true }
  ],
  "default_sorting_field": "created_at"
}
```

## Indexing

### Watchlist collection

Unlike job postings and taxonomies (which are crawler-sourced), watchlists are created by users in the web app. Indexing happens in the web app layer.

**Write hooks** — add Typesense upsert/delete calls alongside existing Supabase writes. **Must use `getWriteClient()` (TYPESENSE_WRITE_KEY), NOT the search client** — the search-only key cannot write and all upserts would silently fail. **All Typesense writes are fire-and-forget** — catch and log errors, never block the user mutation. If Typesense is down, the Supabase write succeeds and the periodic reconciliation job syncs watchlists later.

| Event | Action |
|-------|--------|
| `createWatchlist()` | If public: upsert to Typesense |
| `updateWatchlist()` (title, description, visibility) | If now public: upsert. If now private: delete from Typesense |
| `deleteWatchlist()` | Delete from Typesense |
| `copyWatchlist()` | Upsert the new copy (if public) + update source's `mirror_count` in Typesense |
| `addCompanyToWatchlist()` | Update `company_count` on Typesense doc (if public) |
| `removeCompanyFromWatchlist()` | Update `company_count` on Typesense doc (if public) |
| `clearWatchlistCompanies()` | Set `company_count` to 0 on Typesense doc (if public) |

**Count refresh + reconciliation**: A periodic job (every 15 min) that:
1. Recalculates `active_job_count` and `company_count` for all public watchlists
2. **Full reconciliation**: queries all public watchlists from Supabase, compares against Typesense collection, upserts any missing/divergent docs and deletes any that are no longer public. This covers watchlist writes lost during Typesense outages (fire-and-forget hooks don't retry).

This is a single job that handles both count freshness and data consistency. Since the watchlist collection is small (hundreds to low thousands of public watchlists), a full reconciliation every 15 min is cheap.

**Initial backfill**: Query all public watchlists from Supabase, compute counts, bulk upsert to Typesense.

### Company collection — match count caching

For `searchCompaniesForWatchlist`, consider adding pre-computed filter-specific match counts to avoid N×2 count queries per page load. This is an optimization for later — start with the multi_search approach and measure.

## getWatchlistPostings() — watchlist job feed

### Current implementation

**File**: `apps/web/src/lib/actions/watchlists.ts` (lines 575–712)

Core watchlist feature — paginated, filtered job postings across all companies in a watchlist. Supports keywords, locations, occupations, seniorities, technologies, salary range, experience range. Returns postings with company info and source URLs.

### Typesense query

Natural fit — essentially a search query scoped to a set of company IDs:

```typescript
const companyIds = watchlist.companies.map(c => c.id);
const filterStr = buildFilterString(filters);

{
  collection: "job_posting",
  q: keywords?.length ? keywords.join(" ") : "*",
  query_by: "title",
  filter_by: `is_active:true && company_id:[${companyIds.join(",")}]${filterStr ? " && " + filterStr : ""}`,
  sort_by: keywords?.length
    ? "_text_match:desc,first_seen_at:desc"
    : "first_seen_at:desc",
  per_page: limit,
  page: Math.floor(offset / limit) + 1,
}
// NOTE: No group_by — current function returns a flat paginated list of
// postings, not grouped by company. response.found gives the total count.
```

**Result mapping**: Each hit includes `source_url` (now in the schema) for display in the watchlist view. Company info (`company_name`, `company_slug`, `company_icon`) is denormalized on each posting — no extra lookup.

**Location/occupation expansion**: The current `getWatchlistPostings()` calls `expandLocationIds()` and `expandOccupationIds()` internally (it's NOT called via the SearchProvider, so `parseSearchFilters()` doesn't run first). The Typesense version must continue calling these expansion functions before building the filter string. Without expansion, selecting "Germany" in a watchlist would only match postings tagged with the country, not its cities.

**Large watchlists**: A watchlist with 200+ companies generates a `company_id:[uuid1,...,uuid200]` filter string of ~7KB. If this hits HTTP limits or causes parsing slowdowns, batch into multiple queries (e.g., 100 companies per batch) and merge results.

**"Any company" mode**: When the watchlist has no company restriction (user hasn't selected specific companies), omit the `company_id` filter — searches all postings matching the watchlist's keyword/filter criteria.

## Code changes

### Modified files

| File | Change |
|------|--------|
| `apps/web/src/lib/actions/company.ts` | Replace Postgres query in `searchCompaniesForWatchlist()` with Typesense two-step query |
| `apps/web/src/lib/actions/watchlists.ts` | Replace `searchPublicWatchlists()`, `getPopularWatchlists()`, `getWatchlistPostings()` with Typesense queries |
| Watchlist mutation actions: `createWatchlist`, `updateWatchlist`, `deleteWatchlist`, `copyWatchlist`, `addCompanyToWatchlist`, `removeCompanyFromWatchlist`, `clearWatchlistCompanies` | Add Typesense upsert/delete hooks (fire-and-forget) |

### New files

| File | Purpose |
|------|---------|
| `apps/web/src/lib/search/typesense-watchlist.ts` | (Optional) Watchlist indexing helpers — upsert/delete/refresh counts |

### No changes needed

| File | Why |
|------|-----|
| `components/watchlist/company-search-modal.tsx` | Calls `searchCompaniesForWatchlist()` — same interface |
| `components/watchlist/public-watchlist-search.tsx` | Calls `searchPublicWatchlists()` — same interface |

## Edge cases

- **Empty query in company search**: `q: "*"` with `active_posting_count:>0` — returns all companies ranked by posting count. Same as current behavior.
- **Starred companies with no text query**: Two-query approach (starred first, then remaining). Preserve current sort order.
- **Watchlist visibility toggle**: When a watchlist goes from public → private, delete from Typesense. When it goes private → public, upsert. Race condition: user toggles rapidly. Use Typesense's idempotent upsert — last write wins.
- **Stale match counts**: Match counts in `searchCompaniesForWatchlist` are computed live per request via multi_search. No staleness issue (unlike pre-computed counts).
- **Stale watchlist counts**: `active_job_count` on watchlist docs may lag by up to 15 min (cron interval). Acceptable for discovery — users see live counts when they open the actual watchlist.
- **Popular watchlists fallback**: `getPopularWatchlists()` (used when query < 2 chars) can be a simple Typesense query: `q: "*", sort_by: "mirror_count:desc", per_page: 10`.
