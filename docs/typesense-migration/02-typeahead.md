# Typeahead Migration

Migrates all 5 autocomplete/suggest functions from Postgres `similarity()` to Typesense search-as-you-type. These serve both the header search bar dropdown and the individual filter modals.

## Current implementation

### Shared pattern

All suggest functions follow the same pattern:
1. Minimum 2-character query threshold
2. Two-phase matching: prefix match (rank 1) then fuzzy via `similarity()` (rank 2, threshold 0.25–0.3)
3. Filter to entities with active job postings
4. Redis cache (TTL 600–3600s)
5. Return up to 5–8 results

### Files

| Function | File | Lines | Cache TTL |
|----------|------|-------|-----------|
| `suggestLocations()` | `apps/web/src/lib/actions/locations.ts` | 15–144 | 3600s |
| `suggestCompanies()` | `apps/web/src/lib/actions/company.ts` | 22–71 | 600s |
| `suggestOccupations()` | `apps/web/src/lib/actions/taxonomy.ts` | 15–84 | 3600s |
| `suggestSeniorities()` | `apps/web/src/lib/actions/taxonomy.ts` | 86–155 | 3600s |
| `suggestTechnologies()` | `apps/web/src/lib/actions/taxonomy.ts` | 492–520 | 3600s |

### UI consumers

| Component | File | Functions used |
|-----------|------|----------------|
| Header search bar | `components/search/search-bar.tsx` | All 5 suggest functions |
| Location filter modal | `components/search/location-search-modal.tsx` | `suggestLocations()` |
| Occupation filter modal | `components/search/occupation-modal.tsx` | `suggestOccupations()` |
| Seniority filter modal | `components/search/seniority-modal.tsx` | `suggestSeniorities()` |
| Technology filter modal | `components/search/technology-modal.tsx` | `suggestTechnologies()` |

### Performance problem

Postgres `similarity()` requires a sequential scan of the trigram index for each query. With growing taxonomy tables and the function being called on every keystroke (debounced 200–300ms), this produces 50–200ms response times. Typesense search-as-you-type is designed for <10ms latency on this exact workload.

## Typesense implementation

### suggestLocations()

**Current behavior:**
- Prefix + fuzzy matching on `location_name.name`
- Filters to locations with active postings
- Multi-locale: prefers user's locale, falls back to 'en'
- Geo-sorting: nearby locations (<300km) sorted by distance, far locations by population
- Returns: `{ id, slug, name, type, parentName }`

**Typesense query:**

```typescript
{
  collection: "location",
  q: query,
  query_by: `name_${locale},name_en`,       // prefer user locale, fall back to English
  query_by_weights: "3,1",                   // boost user locale matches
  filter_by: "has_active_postings:true",
  sort_by: userLat && userLng
    ? `_text_match:desc,coordinates(${userLat},${userLng}, precision: 5km):asc,active_posting_count:desc`
    : "_text_match:desc,active_posting_count:desc",
  per_page: 8,
  prefix: true,                    // prefix search (match start of words)
  num_typos: 1,                    // allow 1 typo
  drop_tokens_threshold: 0,        // don't drop tokens
}
```

**Geo-sorting via native `geopoint`**: The `location` collection has a `coordinates` field of type `geopoint` storing `[lat, lng]` from the Postgres `location` table. This completely replaces the client-side Haversine calculation (`locations.ts:423-434`) and the near/far sorting logic (`locations.ts:118-135`).

The `precision: 5km` parameter buckets locations into 5km geo bands. Within the same text match quality and geo band, locations are ranked by `active_posting_count` (posting volume). This closely matches the current behavior where:
- `match_rank` (prefix vs fuzzy) → `_text_match` (Typesense relevance score)
- Distance within 300km → `coordinates(lat, lng, precision: 5km)` geo bucketing
- Population for far locations → `active_posting_count` (better proxy for search relevance)

Locations without coordinates (macro regions like "European Union") have `coordinates` as optional. Typesense sorts them to the end when geo-sorting via `missing_values: last` (default behavior for optional geopoint fields).

**Multi-locale via multi-field approach**: One document per location with `name_en`, `name_de`, `name_fr`, `name_it` fields. Each field has its own `locale` property in the schema for correct tokenization:
- German: preserves umlauts (München → München, not Munchen)
- French: preserves accents (Genève → Genève)
- `query_by=name_${locale},name_en` searches user locale first, then English fallback
- `query_by_weights=3,1` boosts user-locale matches so "München" ranks above "Munich" for German users

This avoids duplicating coordinates and IDs across locale documents and avoids deduplication in results.

**Result mapping:**

```typescript
function mapLocationSuggestion(hit: TypesenseHit, locale: string): LocationSuggestion {
  const doc = hit.document;
  return {
    id: doc.location_id,  // numeric ID, not the string document id
    slug: doc.slug,
    name: doc[`name_${locale}`] ?? doc.name_en,
    type: doc.type,
    parentName: doc.parent_name ?? null,
  };
}
```

### suggestCompanies()

**Current behavior:**
- Prefix + fuzzy matching on `company.name` (similarity > 0.3)
- Filters to companies with active postings
- Returns: `{ id, name, slug, icon }`
- Limit: 5

**Typesense query:**

```typescript
{
  collection: "company",
  q: query,
  query_by: "name",
  filter_by: "active_posting_count:>0",
  sort_by: "_text_match:desc,active_posting_count:desc",
  per_page: 5,
  prefix: true,
  num_typos: 1,
}
```

**Result mapping:**

```typescript
function mapCompanySuggestion(hit: TypesenseHit): CompanySuggestion {
  const doc = hit.document;
  return {
    id: doc.id,
    name: doc.name,
    slug: doc.slug,
    icon: doc.icon ?? null,
  };
}
```

### suggestOccupations()

**Current behavior:**
- Prefix + fuzzy matching on `occupation_name.name` (similarity > 0.25)
- Multi-locale: prefers user locale, falls back to 'en'
- Returns `matchedName` if the alias that matched differs from the display name
- Filters to occupations with active postings
- Limit: 5

**Typesense query:**

```typescript
{
  collection: "occupation",
  q: query,
  query_by: "name,aliases",       // search display name + aliases
  filter_by: `has_active_postings:true && locale:${locale}`,
  sort_by: "_text_match:desc,active_posting_count:desc",
  per_page: 5,
  prefix: true,
  num_typos: 1,
}
```

**`matchedName` handling**: Typesense highlights which field matched. Check `hit.highlights` — if the match is in `aliases` rather than `name`, set `matchedName` to the highlighted alias value.

```typescript
function mapOccupationSuggestion(hit: TypesenseHit): OccupationSuggestion {
  const doc = hit.document;
  const aliasHighlight = hit.highlights?.find(h => h.field === "aliases");
  return {
    id: doc.occupation_id,  // numeric ID, not the composite "42-de" document id
    slug: doc.slug,
    name: doc.name,
    // For array fields like aliases, snippets[0] gives the first matched alias text.
    // matched_tokens is string[][] (nested array per array element), not flat string[].
    matchedName: aliasHighlight?.snippets?.[0] ?? undefined,
  };
}
```

**Per-locale docs**: Each (occupation, locale) pair is a separate document (e.g., `softwaredev-en`, `softwaredev-de`). This is the right choice for occupations because:
- Aliases are locale-specific ("Softwareentwickler" is a German alias, not English)
- The `name` field needs correct locale tokenization (one field can only have one `locale`)
- The collection is tiny (~400 docs for 100 occupations × 4 locales)

Filter by `locale:${userLocale}` to get locale-specific names. If no results, retry with `locale:en` fallback.

### suggestSeniorities()

**Current behavior:**
- Identical pattern to occupations (prefix + fuzzy, multi-locale, matchedName)
- Filters to seniorities with active postings
- Limit: 5

**Typesense query:**

```typescript
{
  collection: "seniority",
  q: query,
  query_by: "name,aliases",
  filter_by: `has_active_postings:true && locale:${locale}`,
  sort_by: "_text_match:desc,active_posting_count:desc",
  per_page: 5,
  prefix: true,
  num_typos: 1,
}
```

Result mapping identical to occupations.

### suggestTechnologies()

**Current behavior:**
- Prefix matching only (no fuzzy) on `technology.slug` and `technology.name`
- Filters to technologies with active postings
- No locale (technology names are language-agnostic)
- Limit: 5

**Typesense query:**

```typescript
{
  collection: "technology",
  q: query,
  query_by: "name,slug",
  filter_by: "has_active_postings:true",
  sort_by: "_text_match:desc,active_posting_count:desc",
  per_page: 5,
  prefix: true,
  num_typos: 0,   // no typo tolerance — match current prefix-only behavior
}
```

**Note**: Technologies like "C++", "C#", ".NET" contain special characters. The `technology` collection schema includes `token_separators` and `symbols_to_index` for `+`, `#`, `.` — ensuring "C++" is indexed as a single token.

## Unified typeahead via multi_search

The header search bar fires all 5 suggest functions in parallel on each keystroke. With Typesense, these can be batched into a single `multi_search` request:

```typescript
const response = await typesense.multiSearch.perform({
  searches: [
    { collection: "location",   q: query, query_by: "name_en", ... },
    { collection: "company",    q: query, query_by: "name", ... },
    { collection: "occupation", q: query, query_by: "name,aliases", ... },
    { collection: "seniority",  q: query, query_by: "name,aliases", ... },
    { collection: "technology", q: query, query_by: "name,slug", ... },
  ],
});
```

**Benefits:**
- Single HTTP round-trip instead of 5 parallel Postgres queries
- Typesense processes all 5 searches in parallel internally
- Expected latency: <10ms total (vs 50–200ms per Postgres query)

## Caching strategy

With Typesense response times under 10ms, Redis caching for typeahead becomes overhead rather than optimization. The Redis serialization/deserialization alone can take 1–5ms.

**Recommendation**: Remove Redis caching for all typeahead functions. Typesense is the cache.

If there's concern about Typesense load under high traffic, add caching back with a short TTL (30–60s) — but benchmark first.

## Code changes

### Modified files

| File | Change |
|------|--------|
| `apps/web/src/lib/actions/locations.ts` | Replace Postgres query with Typesense search in `suggestLocations()` |
| `apps/web/src/lib/actions/company.ts` | Replace Postgres query with Typesense search in `suggestCompanies()` |
| `apps/web/src/lib/actions/taxonomy.ts` | Replace Postgres queries with Typesense search in `suggestOccupations()`, `suggestSeniorities()`, `suggestTechnologies()` |
| `apps/web/src/components/search/search-bar.tsx` | Optionally: replace 5 parallel calls with single `multi_search` call via a new server action |

### New files

| File | Purpose |
|------|---------|
| `apps/web/src/lib/actions/suggest.ts` | (Optional) Unified `suggestAll(query, locale, lat, lng)` server action using `multi_search` |

### No changes needed

| File | Why |
|------|-----|
| `components/search/location-search-modal.tsx` | Calls `suggestLocations()` — same interface |
| `components/search/occupation-modal.tsx` | Calls `suggestOccupations()` — same interface |
| `components/search/seniority-modal.tsx` | Calls `suggestSeniorities()` — same interface |
| `components/search/technology-modal.tsx` | Calls `suggestTechnologies()` — same interface |

## Indexing

### Location collection

Populated by `sync.py` after location table sync:

1. Query all locations from local Postgres with names in all locales
2. Join with active posting counts: `SELECT location_id, COUNT(*) FROM unnest(location_ids) ... WHERE is_active GROUP BY 1`
3. Build documents with `name_en`, `name_de`, `name_fr`, `name_it`, `coordinates` (from lat/lng)
4. Bulk upsert to Typesense

**Refresh trigger**: After `sync.py` runs, or on a timer (every 30 min) to update `active_posting_count` and `has_active_postings`.

### Occupation / Seniority collections

Populated by `sync.py` after taxonomy sync:

1. Query all (occupation/seniority, locale) pairs with display names + aliases
2. Join with active posting counts
3. Bulk upsert — one document per (entity, locale) pair

### Technology collection

Populated by `sync.py`:

1. Query all technologies with name, slug, category
2. Join with active posting counts
3. Bulk upsert — one document per technology (no locale dimension)

### Company collection

Populated by `sync.py` for metadata + exporter for counts (see master plan).

## Edge cases

- **Query < 2 chars**: Current functions return empty. Keep this behavior client-side — don't hit Typesense for 1-char queries (too broad).
- **No results for user locale**: Fall back to `locale:en` for occupations/seniorities (second query). For locations, `query_by: "name_${locale},name_en"` handles this natively — it searches both fields in one query, prioritizing user locale via `query_by_weights`.
- **Already-selected items**: Currently filtered out client-side in `search-bar.tsx`. No change needed — continue filtering the Typesense response in the component.
- **Special characters in tech names**: Handled by `symbols_to_index` on the technology collection schema.
- **Location geo-sorting without user coords**: Falls back to `_text_match:desc,active_posting_count:desc`. Replaces the current population-based fallback — posting count is a better proxy for search relevance.
- **Coordinate order**: Typesense uses `[lat, lng]` — NOT GeoJSON's `[lng, lat]`. The Postgres `location` table stores `lat` and `lng` as separate columns, so pass them in the correct order.
