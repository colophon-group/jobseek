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
- Match counting: subquery counts active job_posting rows matching all filters per company
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
  filter_by: `company_id:${companyId} && is_active:true && ${buildFilterString(filters)}`,
  per_page: 0,  // counts only
}));

const yearCountSearches = companyIds.map(companyId => ({
  collection: "job_posting",
  q: keywords?.length ? keywords.join(" ") : "*",
  query_by: "title",
  filter_by: `company_id:${companyId} && first_seen_at:>${oneYearAgoUnix} && ${buildFilterString(filters)}`,
  per_page: 0,
}));

const response = await typesense.multiSearch.perform({
  searches: [...matchCountSearches, ...yearCountSearches],
});
```

**Note on multi_search limits**: Typesense defaults to max 50 searches per multi_search request. With page size 20 companies × 2 count queries = 40 searches, this fits. If page size increases, batch into multiple multi_search calls.

**Starred companies sorting**: If `starredCompanyIds` is provided and no text query, do two Typesense queries:
1. Fetch starred companies: `filter_by: "id:[${starredCompanyIds.join(',')}]"`
2. Fetch remaining companies: `filter_by: "id:!=[${starredCompanyIds.join(',')}]"`
Concatenate results.

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

**Write hooks** — add Typesense upsert/delete calls alongside existing Supabase writes:

| Event | Action |
|-------|--------|
| Watchlist created | If public: upsert to Typesense |
| Watchlist updated (title, description, visibility) | If now public: upsert. If now private: delete from Typesense |
| Watchlist deleted | Delete from Typesense |
| Company added/removed from watchlist | Update `company_count` on Typesense doc |

**Count refresh**: `active_job_count` depends on job posting data (not user actions). Refresh periodically — either:
- A cron job (every 15 min) that recalculates counts for all public watchlists
- Triggered after exporter batches (more real-time but couples crawler to watchlist logic)

Recommend the cron approach for simplicity.

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
  group_by: "company_id",
  group_limit: groupLimit,
  per_page: limit,
  page: Math.floor(offset / limit) + 1,
}
```

**Result mapping**: Each hit includes `source_url` (now in the schema) for display in the watchlist view. Company info (`company_name`, `company_slug`, `company_icon`) is denormalized on each posting — no extra lookup.

**"Any company" mode**: When the watchlist has no company filter, omit the `company_id` filter — searches all postings.

## Code changes

### Modified files

| File | Change |
|------|--------|
| `apps/web/src/lib/actions/company.ts` | Replace Postgres query in `searchCompaniesForWatchlist()` with Typesense two-step query |
| `apps/web/src/lib/actions/watchlists.ts` | Replace ILIKE query in `searchPublicWatchlists()` with Typesense query; replace `getWatchlistPostings()` with Typesense query |
| Watchlist mutation actions (create/update/delete) | Add Typesense upsert/delete hooks |

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
