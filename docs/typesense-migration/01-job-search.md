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
    filter_by: buildFilterString({   // all facet filters
      is_active: true,
      location_ids, occupation_id, seniority_id,
      technology_ids, employment_type,
      salary_eur, experience_min, locales
    }),
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
  const parts: string[] = ["is_active:true"];

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

  if (filters.experienceMin != null)
    parts.push(`experience_min:>=${filters.experienceMin}`);
  if (filters.experienceMax != null)
    parts.push(`experience_min:<=${filters.experienceMax}`);

  if (filters.languages?.length)
    parts.push(`locales:[${filters.languages.join(",")}]`);

  return parts.join(" && ");
}
```

**Location/occupation hierarchy expansion**: The current Postgres implementation expands parent IDs to include all children (e.g., selecting "Germany" includes all German cities). This expansion happens in `parseSearchFilters()` before reaching the search provider. Typesense receives the already-expanded ID arrays — no change needed.

### Query mapping: `listTopCompanies()`

Typesense's `group_by` sorts groups by the best-matching *document's* sort key, not by group size. So `group_by: company_id` with `sort_by: first_seen_at:desc` would rank companies by most recent posting, not by posting count. For browse mode we want "top companies by number of postings."

**Two-step approach** (2 queries, not N+1):

```typescript
// Step 1: Get top companies ranked by posting count
const companyResults = await typesense.collections("company").documents().search({
  q: "*",
  filter_by: "active_posting_count:>0",
  sort_by: "active_posting_count:desc",
  per_page: limit,
  page: Math.floor(offset / limit) + 1,
});

// Step 2: Fetch postings for those companies in one query
const companyIds = companyResults.hits.map(h => h.document.id);
const postingResults = await typesense.collections("job_posting").documents().search({
  q: "*",
  filter_by: `company_id:[${companyIds.join(",")}] && is_active:true && ${buildFilterString(filters)}`,
  group_by: "company_id",
  group_limit: 10,
  sort_by: "first_seen_at:desc",
  per_page: companyIds.length,
});
```

This is 2 queries (batched via `multi_search` = 1 HTTP round-trip). The `company` collection has pre-computed `active_posting_count` and `year_posting_count` — no per-query count subqueries needed.

### Query mapping: `loadPostings()`

```typescript
{
  collection: "job_posting",
  q: keywords.length ? keywords.join(" ") : "*",
  query_by: "title",
  filter_by: `company_id:${companyId} && is_active:true && ${buildFilterString(filters)}`,
  sort_by: keywords.length
    ? "_text_match:desc,first_seen_at:desc"
    : "first_seen_at:desc",
  per_page: limit,
  page: Math.floor(offset / limit) + 1,
}
```

### Query mapping: `loadPostingsWithCounts()`

Same as `loadPostings()` plus a company doc lookup for pre-computed counts:

```typescript
// Batch via multi_search: postings query + company doc lookup
const searches = [
  // Postings query (same as loadPostings)
  {
    collection: "job_posting",
    q: keywords.length ? keywords.join(" ") : "*",
    query_by: "title",
    filter_by: `company_id:${companyId} && is_active:true && ${buildFilterString(filters)}`,
    sort_by: keywords.length ? "_text_match:desc,first_seen_at:desc" : "first_seen_at:desc",
    per_page: limit,
    page: Math.floor(offset / limit) + 1,
  },
  // Company doc for pre-computed counts
  {
    collection: "company",
    q: "*",
    filter_by: `id:${companyId}`,
    per_page: 1,
  },
];

// activeCount = company.active_posting_count
// yearCount = company.year_posting_count
```

Counts come from the `company` collection (pre-computed, approximate). Single `multi_search` call.

### Query mapping: `getSalaryHistogram()`

Typesense supports facet ranges for numeric fields:

```typescript
{
  collection: "job_posting",
  q: keywords?.length ? keywords.join(" ") : "*",
  query_by: "title",
  filter_by: `is_active:true && salary_eur:>0 && ${buildFilterString(filters)}`,
  facet_by: "salary_eur(0, 10000, 20000, 30000, 40000, 50000, 60000, 70000, 80000, 90000, 100000, 110000, 120000, 130000, 140000, 150000, 160000, 170000, 180000, 190000, 200000, 210000, 220000, 230000, 240000, 250000, 260000, 270000, 280000, 290000, 300000)",
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
  companyMap: Map<string, { activeCount: number; yearCount: number }>,
): SearchResponse {
  const companies: SearchResultCompany[] = response.grouped_hits.map(group => {
    const firstHit = group.hits[0].document;
    const counts = companyMap.get(firstHit.company_id);
    return {
      company: {
        id: firstHit.company_id,
        name: firstHit.company_name,
        slug: firstHit.company_slug,
        icon: firstHit.company_icon ?? null,  // denormalized on job_posting
      },
      activeMatches: counts?.activeCount ?? group.found.value,
      yearMatches: counts?.yearCount ?? 0,
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

**`activeMatches` / `yearMatches`**: Pre-computed on the `company` collection as `active_posting_count` and `year_posting_count`, refreshed periodically. For `search()`, after getting grouped results, batch-fetch company docs for the returned company IDs via `multi_search` to get counts. This is 1 extra query batched in the same `multi_search` call — not N queries.

For `listTopCompanies()`, the counts come directly from the `company` collection query (step 1 of the two-step approach).

Counts are approximate (refreshed every ~30 min) — this is acceptable.

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
- **Year matches count**: Pre-computed on the `company` collection as `year_posting_count`. Refreshed periodically by `refresh_typesense_counts()`. Approximate — acceptable for display.
- **`buildFilterString()` and `is_active`**: The function always injects `is_active:true`. This is correct for all search queries. The `yearCount` pre-computation (in the crawler's `refresh_typesense_counts()`) runs its own SQL query without this restriction, so it can count both active and inactive postings from the last year. No conflict.
- **Relevance scoring**: Typesense's `_text_match` score replaces the keyword count ranking. It incorporates typo distance, token position, and field weights — strictly better than the current integer count.
