# Job Search Migration

Migrates the core job search experience: keyword search, browse/top companies, load-more pagination, and histogram filters.

## Current implementation

### Files

| Component | File | Lines |
|-----------|------|-------|
| SearchProvider interface | `apps/web/src/lib/search/types.ts` | 64–93 |
| PostgresSearchProvider | `apps/web/src/lib/search/postgres.ts` | 246–667 |
| Provider factory | `apps/web/src/lib/search/index.ts` | 16–23 |
| Server actions | `apps/web/src/lib/actions/search.ts` | 218–478 |
| Search page UI | `apps/web/app/[lang]/(app)/explore/search-page.tsx` | 1–696 |
| Public API | `apps/web/app/api/v1/search/route.ts` | 1–100 |

### Methods to migrate

**`SearchProvider.search()`** — keyword search with faceted filters
- Current: PostgreSQL word-boundary regex (`titles[1] ~* \mword\M`) per keyword
- Scores by keyword hit count, groups by company, sorts by best match → active count → year count
- Returns top N companies with up to 10 sample postings each

**`SearchProvider.listTopCompanies()`** — browse mode (no keywords)
- Current: Groups all active postings by company, applies facet filters, ranks by active_matches
- Same filter set as search but no keyword scoring

**`SearchProvider.loadPostings()`** — paginated postings within a company
- Current: Fetches postings for a specific company_id, optionally keyword-filtered
- With keywords: sorted by keyword match count, then recency
- Without keywords: sorted by recency

**`SearchProvider.loadPostingsWithCounts()`** — same as above plus aggregate counts
- Returns `{ postings, activeCount, yearCount }` for the company

**`SearchProvider.getSalaryHistogram()`** — salary distribution chart
- Current: `width_bucket(salary_eur, 0, 300000, 30)` — 30 buckets of 10K EUR
- Supports all filter dimensions

**`SearchProvider.getExperienceHistogram()`** — experience distribution chart
- Current: `GROUP BY experience_min` — raw year values
- Supports all filter dimensions

### Current query flow

```
User types keywords + selects filters
  → parseSearchFilters() tokenizes input, resolves slugs → IDs
  → searchJobs() server action
    → cached() wrapper (Redis, TTL 300s)
      → getSearchProvider().search()
        → PostgresSearchProvider builds CTE with:
          - Word-boundary regex per keyword on titles[1]
          - WHERE clauses for each filter dimension
          - GROUP BY company_id
          - ORDER BY keyword_count DESC, active_matches DESC
          - LIMIT/OFFSET pagination
        → resolveLocationNames() for display
        → groupRows() flattens SQL rows into SearchResultCompany[]
```

## Typesense implementation

### Query mapping: `search()`

```typescript
// Typesense multi_search request
{
  searches: [{
    collection: "job_posting",
    q: keywords.join(" "),           // free-text query
    query_by: "title",               // search in title field only
    filter_by: `is_active:true && ${buildFilterString(filters)}`,
    sort_by: "_text_match:desc,first_seen_at:desc",
    group_by: "company_id",          // group results by company
    group_limit: 10,                 // max 10 postings per company group
    per_page: limit,                 // companies per page
    page: Math.floor(offset / limit) + 1,
    typo_tokens_threshold: 1,        // enable typo tolerance
  }]
}
```

**Filter string builder:**

```typescript
function buildFilterString(filters: SearchFilters): string {
  const parts: string[] = [];

  if (filters.locationIds?.length)
    parts.push(`location_ids:[${filters.locationIds.join(",")}]`);

  if (filters.occupationIds?.length)
    parts.push(`occupation_id:[${filters.occupationIds.join(",")}]`);

  if (filters.seniorityIds?.length)
    parts.push(`seniority_id:[${filters.seniorityIds.join(",")}]`);

  if (filters.technologyIds?.length)
    parts.push(`technology_ids:[${filters.technologyIds.join(",")}]`);

  if (filters.employmentTypes?.length)
    parts.push(`employment_type:[${filters.employmentTypes.join(",")}]`);

  if (filters.salaryMinEur != null || filters.salaryMaxEur != null) {
    const min = filters.salaryMinEur ?? 0;
    const max = filters.salaryMaxEur ?? 999999;
    parts.push(`salary_eur:[${min}..${max}]`);
  }

  // experience_min uses sentinel -1 for "not specified". Include sentinels
  // so jobs without stated experience aren't excluded by range filters.
  if (filters.experienceMin != null)
    parts.push(`experience_min:>=${filters.experienceMin}`);
  if (filters.experienceMax != null)
    parts.push(`experience_min:<=${filters.experienceMax} || experience_min:=-1`);

  // locales uses sentinel "any" for jobs with no detected language.
  // Include it so those jobs match any language filter.
  if (filters.languages?.length)
    parts.push(`locales:[${[...filters.languages, "any"].join(",")}]`);

  return parts.join(" && ");
}
```

**`buildFilterString` does NOT inject `is_active:true`** — callers prepend it explicitly. This keeps the function as a pure mechanical helper for user-specified filter dimensions. Query-level concerns (`is_active`, `first_seen_at` ranges) are the caller's responsibility:

```typescript
// Active search queries:
filter_by: `is_active:true && ${buildFilterString(filters)}`

// Year count queries (includes inactive/filled postings):
filter_by: `first_seen_at:>${oneYearAgoUnix} && ${buildFilterString(filters)}`
```
```

**Location/occupation hierarchy expansion**: The current Postgres implementation expands parent IDs to include all children (e.g., selecting "Germany" includes all German cities). This expansion happens in `parseSearchFilters()` before reaching the search provider. Typesense receives the already-expanded ID arrays — no change needed.

### Query mapping: `listTopCompanies()`

Typesense's `group_by` sorts groups by the best-matching *document's* sort key, not by group size. So `group_by: company_id` with `sort_by: first_seen_at:desc` would rank companies by most recent posting, not by posting count. For browse mode we want "top companies by number of matching postings."

**Critical**: Companies must be ranked by **filtered** posting count, not global count. If the user filters by "Berlin + Python", a company with 15 matching jobs must rank above one with 500 total jobs but 1 match.

**Facet-based approach** (2 queries, 1 HTTP call):

```typescript
// Step 1: Get company IDs ranked by FILTERED posting count
// facet_by returns values sorted by count descending
const facetResult = await typesense.multiSearch.perform({
  searches: [
    {
      collection: "job_posting",
      q: "*",
      filter_by: `is_active:true${buildFilterString(filters) ? " && " + buildFilterString(filters) : ""}`,
      facet_by: "company_id",
      max_facet_values: offset + limit,  // enough for pagination
      per_page: 0,                       // no docs needed, just facet counts
    },
  ],
});

// facet_counts[0].counts = [
//   { value: "company-abc", count: 15 },  ← 15 Berlin+Python jobs
//   { value: "company-xyz", count: 8 },
//   ...
// ]
const facetCounts = facetResult.results[0].facet_counts[0].counts;
const page = facetCounts.slice(offset, offset + limit);
const companyIds = page.map(f => f.value);
const matchCountMap = new Map(page.map(f => [f.value, f.count]));

// Step 2: Fetch postings for this page of companies
const postingResults = await typesense.collections("job_posting").documents().search({
  q: "*",
  filter_by: `company_id:[${companyIds.join(",")}] && is_active:true${buildFilterString(filters) ? " && " + buildFilterString(filters) : ""}`,
  group_by: "company_id",
  group_limit: 10,
  sort_by: "first_seen_at:desc",
  per_page: companyIds.length,
});
```

**How it works**: `facet_by: company_id` returns company IDs sorted by how many matching postings they have — exactly the filtered ranking we need. We paginate through the facet list, then fetch postings for the current page. The facet counts become `activeMatches` per company.

**Unfiltered case**: When no filters are active, the facet still works (counts all active postings per company). Alternatively, for the pure unfiltered case, querying the `company` collection by `active_posting_count:desc` is faster since counts are pre-computed. The implementation can branch:
- No filters → query `company` collection (pre-computed counts)
- With filters → facet approach (live filtered counts)

**`yearMatches` with filters**: Add a second facet query with `first_seen_at:>${oneYearAgoUnix}` to get filtered year counts per company. Batch via `multi_search`.

**Pagination limit**: `max_facet_values` caps how many companies we can paginate through. Set it to `offset + limit`. For deep pagination (page 10+), this means fetching a larger facet set. Acceptable for typical usage — most users don't go past page 3–4. If this becomes a bottleneck, switch to cursor-based pagination.

### Query mapping: `loadPostings()`

```typescript
{
  collection: "job_posting",
  q: keywords.length ? keywords.join(" ") : "*",
  query_by: "title",
  filter_by: `company_id:=${companyId} && is_active:true${buildFilterString(filters) ? " && " + buildFilterString(filters) : ""}`,
  sort_by: keywords.length
    ? "_text_match:desc,first_seen_at:desc"
    : "first_seen_at:desc",
  per_page: limit,
  page: Math.floor(offset / limit) + 1,
}
```

### Query mapping: `loadPostingsWithCounts()`

Same as `loadPostings()` plus live filtered count queries — these counts must reflect the user's active filters, not global pre-computed values:

```typescript
const filterStr = buildFilterString(filters);
const baseFilter = `company_id:=${companyId}${filterStr ? " && " + filterStr : ""}`;
const q = keywords.length ? keywords.join(" ") : "*";

const searches = [
  // Postings query (same as loadPostings)
  {
    collection: "job_posting",
    q,
    query_by: "title",
    filter_by: `is_active:true && ${baseFilter}`,
    sort_by: keywords.length ? "_text_match:desc,first_seen_at:desc" : "first_seen_at:desc",
    per_page: limit,
    page: Math.floor(offset / limit) + 1,
  },
  // Active count (filtered)
  {
    collection: "job_posting",
    q,
    query_by: "title",
    filter_by: `is_active:true && ${baseFilter}`,
    per_page: 0,  // activeCount = found
  },
  // Year count (filtered — includes inactive postings from past year)
  {
    collection: "job_posting",
    q,
    query_by: "title",
    filter_by: `first_seen_at:>${oneYearAgoUnix} && ${baseFilter}`,
    per_page: 0,  // yearCount = found
  },
];

// activeCount = results[1].found
// yearCount = results[2].found
```

Three queries in one `multi_search` call. Active count uses `is_active:true`, year count uses `first_seen_at` range without `is_active` (includes filled jobs). Both apply the user's filters.

### Query mapping: `getSalaryHistogram()`

Typesense supports facet ranges for numeric fields:

```typescript
{
  collection: "job_posting",
  q: keywords?.length ? keywords.join(" ") : "*",
  query_by: "title",
  filter_by: `is_active:true && salary_eur:>0 && ${buildFilterString(filters)}`,
  facet_by: "salary_eur(0-10k:[0,10000], 10-20k:[10000,20000], 20-30k:[20000,30000], 30-40k:[30000,40000], 40-50k:[40000,50000], 50-60k:[50000,60000], 60-70k:[60000,70000], 70-80k:[70000,80000], 80-90k:[80000,90000], 90-100k:[90000,100000], 100-110k:[100000,110000], 110-120k:[110000,120000], 120-130k:[120000,130000], 130-140k:[130000,140000], 140-150k:[140000,150000], 150-160k:[150000,160000], 160-170k:[160000,170000], 170-180k:[170000,180000], 180-190k:[180000,190000], 190-200k:[190000,200000], 200-210k:[200000,210000], 210-220k:[210000,220000], 220-230k:[220000,230000], 230-240k:[230000,240000], 240-250k:[240000,250000], 250-260k:[250000,260000], 260-270k:[260000,270000], 270-280k:[270000,280000], 280-290k:[280000,290000], 290-300k:[290000,300000])",
  max_facet_values: 31,
  per_page: 0,  // no documents needed
}
```

This returns bucket counts matching the current 30-bucket / 10K EUR layout. Transform the facet response into `SalaryBucket[]`.

**Note:** Typesense facet ranges use the syntax `field(val1, val2, ...)` where each value is a boundary. Verify the exact range syntax against the Typesense version deployed.

### Query mapping: `getExperienceHistogram()`

```typescript
{
  collection: "job_posting",
  q: keywords?.length ? keywords.join(" ") : "*",
  query_by: "title",
  filter_by: `is_active:true && ${buildFilterString(filters)}`,
  facet_by: "experience_min",
  max_facet_values: 30,  // cover 0-30 years
  per_page: 0,
}
```

Typesense returns `{ value: "3", count: 1234 }` facet entries. Transform to `ExperienceBucket[]`.

## Result mapping

Typesense `group_by` results need transformation to match `SearchResponse`:

```typescript
function mapGroupedHits(
  response: TypesenseSearchResponse,
  activeCountMap: Map<string, number>,   // from facet or group.found
  yearCountMap?: Map<string, number>,    // from year facet (optional)
): SearchResponse {
  const companies: SearchResultCompany[] = response.grouped_hits.map(group => {
    const firstHit = group.hits[0].document;
    const companyId = firstHit.company_id;
    return {
      company: {
        id: companyId,
        name: firstHit.company_name,
        slug: firstHit.company_slug,
        icon: firstHit.company_icon ?? null,
      },
      activeMatches: activeCountMap.get(companyId) ?? group.found,
      yearMatches: yearCountMap?.get(companyId) ?? 0,
      postings: group.hits.map(hit => ({
        id: hit.document.id,
        title: hit.document.title,
        firstSeenAt: new Date(hit.document.first_seen_at * 1000),
        relevanceScore: hit.text_match,
        locations: buildLocations(hit.document),
        isActive: hit.document.is_active,
      })),
    };
  });

  return {
    companies,
    totalCompanies: response.found,
    truncated: false,  // handle anon truncation in server action layer
  };
}
```

**`activeMatches` / `yearMatches` — how counts are obtained per method:**

| Method | activeMatches | yearMatches |
|--------|--------------|-------------|
| `search()` (keywords) | `group.found` — Typesense returns the filtered match count per group natively | Second `multi_search` query: same filters + `first_seen_at:>${oneYearAgoUnix}`, `facet_by: company_id`, `per_page: 0` |
| `listTopCompanies()` (no keywords, with filters) | From `facet_by: company_id` counts (step 1 of facet approach) | Second facet query with year filter |
| `listTopCompanies()` (no keywords, no filters) | From `company` collection `active_posting_count` (pre-computed) | From `company` collection `year_posting_count` (pre-computed) |
| `loadPostingsWithCounts()` | Live count query: `filter_by: company_id:X && is_active:true && filters`, `per_page: 0` → `found` | Live count query: same + `first_seen_at:>${oneYearAgoUnix}` |

For `search()` and `listTopCompanies()` with filters, `yearMatches` requires one extra facet query batched in the same `multi_search` call. For the unfiltered `listTopCompanies()`, counts come from the pre-computed `company` collection.

**`company_icon`**: Denormalized on each `job_posting` document (see schema in `00-master-plan.md`). No extra lookup needed.

## Code changes

### New files

| File | Purpose |
|------|---------|
| `apps/web/src/lib/search/typesense.ts` | `TypesenseSearchProvider` class |
| `apps/web/src/lib/search/typesense-client.ts` | Singleton Typesense client instance |
| `apps/web/src/lib/search/typesense-filters.ts` | `buildFilterString()` helper |

### Modified files

| File | Change |
|------|--------|
| `apps/web/src/lib/search/index.ts` | Replace `PostgresSearchProvider` with `TypesenseSearchProvider` (one-shot, no toggle) |
| `apps/web/package.json` | Add `typesense` dependency |
| `apps/crawler/src/exporter.py` | Add Typesense upsert step after Supabase upsert |
| `apps/crawler/pyproject.toml` | Add `typesense` dependency |

### Deleted files

| File | Why |
|------|-----|
| `apps/web/src/lib/search/postgres.ts` | Replaced entirely by Typesense provider |

### No changes needed

| File | Why |
|------|-----|
| `apps/web/src/lib/actions/search.ts` | Calls `getSearchProvider()` — provider swap is transparent |
| `apps/web/src/lib/actions/search-input.ts` | `parseSearchFilters()` stays — still resolves slugs → IDs |
| `apps/web/app/[lang]/(app)/explore/search-page.tsx` | UI unchanged |
| `apps/web/app/api/v1/search/route.ts` | Uses same server actions — transparent |
| `apps/web/src/lib/search/types.ts` | Interface stays the same |

## Edge cases

- **Empty keywords**: `q: "*"` matches all — equivalent to current listTopCompanies path
- **No results**: Typesense returns `{ found: 0, hits: [] }` — map to `{ companies: [], totalCompanies: 0 }`
- **Anon user truncation**: Handled in the server action layer (search.ts), not in the provider — no change needed
- **Location hierarchy expansion**: Already expanded to ID arrays before reaching the provider — Typesense filters on the expanded array
- **Year matches count**: Computed live per query using a filtered facet or count query (see result mapping table above). For `listTopCompanies()` without filters, falls back to pre-computed `year_posting_count` on the `company` collection.
- **`buildFilterString()` and `is_active`**: The function does NOT inject `is_active:true` — callers add it explicitly. The `yearCount` query uses `first_seen_at:>${oneYearAgoUnix}` without `is_active:true` to count all postings from the past year (including filled/inactive ones). This matches the current Postgres behavior.
- **Relevance scoring**: Typesense's `_text_match` score replaces the keyword count ranking. It incorporates typo distance, token position, and field weights — strictly better than the current integer count.
