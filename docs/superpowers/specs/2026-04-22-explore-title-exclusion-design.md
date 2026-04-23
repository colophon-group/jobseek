# Explore Title-Keyword Exclusion Design

**Date:** 2026-04-22
**Status:** Draft — awaiting review

## Goal

Let a user exclude jobs whose title contains one or more user-specified keywords (e.g. `senior`, `staff`, `principal`) directly from the Explore page. Matching jobs are hidden from search results in real time. Exclusions live in URL state alongside all other Explore filters so they can be shared, bookmarked, and — when the user eventually clicks "Save as watchlist" — persisted into `watchlist.filters` without any follow-up migration.

## Architecture

The Explore page already keeps every active filter (`keywords`, `locationSlugs`, `occupationSlugs`, `salaryMin/Max`, `experienceMin/Max`, `languages`, etc.) in URL query params and routes them through `searchJobs()` in `apps/web/src/lib/actions/search.ts`, which delegates to `TypesenseSearchProvider.search()` in `apps/web/src/lib/search/typesense.ts`. We extend that pipeline with one new field, `excludeTitles`, threaded from URL params → server action → search provider, and applied as a **post-Typesense in-memory title filter**.

Typesense cannot do substring exclusion on a single field via `filter_by`, and its query-level `-term` negation matches across every `query_by` field (title, description, tags). Since the user asked for title-only exclusion, post-filtering is the only correct option. We compensate for the throughput hit by over-fetching from Typesense by 1.5× and then handling pagination short-fills at the server-action layer.

No DB migration is required for Phase 1: Explore state is URL-only, and when the user later saves an Explore view as a watchlist the existing `watchlist.filters` JSONB column stores `excludeTitles` as a new optional key without schema changes.

## Tech Stack

- **Frontend:** Next.js 15, React, TypeScript, Lingui (i18n), existing `SearchStateProvider`, existing filter-panel components
- **Search:** Typesense 27.1 (already deployed)
- **Types:** Shared `WatchlistFilters` (`apps/web/src/lib/actions/watchlists.ts`) and `SearchFilters` (`apps/web/src/lib/search/types.ts`)
- **Out of scope:** Postgres schema changes, new tables, Drizzle migrations, crawler-side changes

## Data Model

### New field in `WatchlistFilters`

Extend the existing type at `apps/web/src/lib/actions/watchlists.ts:30`:

```ts
export type WatchlistFilters = {
  keywords?: string[];
  excludeTitles?: string[];      // NEW — case-insensitive substrings, e.g. ["senior","staff","principal"]
  locationSlugs?: string[];
  occupationSlugs?: string[];
  senioritySlugs?: string[];
  technologySlugs?: string[];
  salaryMin?: number;
  salaryMax?: number;
  salaryCurrency?: string;
  experienceMin?: number;
  experienceMax?: number;
  anyCompany?: boolean;
};
```

### New field in `SearchFilters`

Extend the interface at `apps/web/src/lib/search/types.ts:30`:

```ts
export interface SearchFilters {
  // ... existing fields
  excludeTitles?: string[];       // NEW
  locale: string;
}
```

### Rules

- Values are **plain words or short phrases**, not regex — users type `senior`, not `senior.*`
- Matching is **case-insensitive** and uses **word-boundary semantics**: keyword `lead` matches the title `Tech Lead` but not `Leadership Coach`. This avoids the surprise of substring matches like `senior` catching `Seniority Product Lead`.
- Implementation: each keyword is `escapeRegex()`'d, then wrapped as `\\b<escaped>\\b`, then joined with `|` into one compiled `RegExp` with the `i` flag
- Empty strings and whitespace-only entries are dropped before being applied
- Duplicates are deduplicated client-side and server-side (case-insensitive compare)
- Keywords may contain spaces (e.g., `head of`) — the word-boundary wrap still matches correctly because `\b` anchors at both ends of the phrase
- A safe cap of **50 exclusion keywords** prevents runaway filter strings; any overflow is ignored with a UI hint
- The crawler's `apps/crawler/data/alert-filters.yaml` stays independent — it's still the source of truth for the CLI pipeline, and phase 1 does not try to unify them

## URL State

New query param: `exclude` — comma-separated, URL-encoded substrings.

Example: `/explore?q=python&exclude=senior%2Cstaff%2Cprincipal`

Parsing: split on `,`, trim each token, drop empties, dedupe, cap at 50. Serialization is the inverse.

All existing URL param handling lives in `apps/web/src/lib/search/params.ts` (or `query-params.ts`) — the new `exclude` param goes through the same helpers so it round-trips identically to every other filter.

## Components

### 1. Exclusion input in the Explore filter panel

Add a new section to the existing filter panel in `apps/web/app/[lang]/(app)/explore/search-page.tsx` (or the filter-drawer component it composes). The section follows the established tag-input pattern already used for `keywords`:

- Heading: "Hide jobs with these words in the title"
- Placeholder: e.g. "senior, staff, principal"
- Enter or `,` adds a tag; Backspace on empty input removes the last tag; each tag has a × close button
- Input is **below** the positive-keyword input so the mental model is "include these, exclude those"
- Disabled state: never — the input is always available, even when the list is empty

### 2. Active-filter chip row

Active exclusions render in the existing active-filters chip row (where applied locations/occupations already appear) with a visual distinction:

- Prefix each chip with `−` or a "hide" icon (not the existing `+` / filter icon)
- Each chip is dismissable; dismiss removes the keyword from URL state
- A single "Clear all exclusions" action removes every exclusion keyword at once

### 3. Result-count disclosure

The existing result-count display (e.g. "1,240 companies") keeps showing the raw Typesense `found` value. When `excludeTitles.length > 0`, append a tooltip or muted suffix: "Some results hidden by your exclusions."

We deliberately **do not** compute "exact post-filter total" across all pages — that would require scanning every company, defeating the purpose of search pagination.

## Data Flow

```
URL (?q=python&exclude=senior,staff)
        │
        ▼
Explore page reads `exclude` via the existing URL-param helpers
(`apps/web/src/lib/search/params.ts` / `query-params.ts`) → excludeTitles: string[]
        │
        ▼
searchJobs({..., excludeTitles}) in lib/actions/search.ts
        │
        ▼
TypesenseSearchProvider.search({..., excludeTitles})
  │
  ├── overFetchLimit = Math.min(Math.ceil(limit * 1.5), 100)
  │
  ├── Typesense query runs as today (no changes to filter_by / query_by)
  │     → returns `companies: SearchResultCompany[]` with nested `postings`
  │
  ├── If excludeTitles.length === 0 → return Typesense result unchanged (fast path)
  │
  ├── Build regex once:
  │     new RegExp("\\b(?:" + excludeTitles.map(escapeRegex).join("|") + ")\\b", "i")
  │
  ├── For each company in results:
  │     - Filter company.postings → drop any posting whose title matches
  │     - If postings.length === 0 after filter → drop the company entirely
  │     - Else keep company as-is; activeMatches / yearMatches remain the raw
  │       Typesense totals (an upper bound, since those counts span all matching
  │       postings for the company, not just the preview postings array we
  │       actually filter). The "Some results hidden by your exclusions" hint
  │       in the UI is how we disclose this honestly.
  │
  ├── Trim the surviving companies to `limit`
  │
  └── If survivors < limit AND Typesense had more results:
        Follow-up fetch with offset = original offset + overFetchLimit
        (Bounded: at most one follow-up fetch per request to cap latency)
        │
        ▼
SearchResponse returned to Explore page, rendered as today
```

## Error Handling

- **Invalid regex characters in user input:** every keyword is passed through an `escapeRegex()` helper before going into the compiled `RegExp`. Users can't accidentally create malformed regex.
- **Empty / whitespace-only keywords:** stripped at three layers (URL parse → server action → provider). No keyword ever reaches the regex compiler empty.
- **Over-fetch safety:** the 1.5× buffer is capped at Typesense's per-page maximum (100) to avoid pathological requests.
- **Short-page fallback:** a single follow-up fetch handles the case where the initial filter removes enough results to leave < `limit` survivors. We do not loop indefinitely — if a second fetch still comes up short, we return what we have and Typesense pagination state remains consistent.
- **Degraded mode:** if the filter regex construction throws (defensive), we log a warning, skip post-filtering, and return raw Typesense results with `degraded: true` set on `SearchResponse`.
- **Keyword count overflow:** if a URL arrives with > 50 exclusion tokens, we truncate server-side and continue; the UI shows a subtle hint next to the input explaining the cap was hit.

## Save-as-Watchlist Compatibility

No changes to the save-as-watchlist flow are required. The existing "Save search as watchlist" button (`apps/web/src/components/search/save-search-button.tsx`) reads the active filter state and passes it to `createWatchlist()`, which JSON-serializes the whole object into `watchlist.filters`. Because `excludeTitles` is just another optional key on `WatchlistFilters`, it round-trips into the JSONB column without any code change in the save path.

Reading back a saved watchlist that contains `excludeTitles` is also free for Phase 1 — but **applying** those exclusions when viewing a watchlist is explicitly deferred (see "Deferred" below). A Phase 2 change to `runWatchlistSearch()` (in `watchlists.ts:~580-650`) would add the same post-filter step there.

## Testing

### Unit

- `escapeRegex()` correctness: special characters (`. * + ? ^ $ ( ) [ ] { } | \`) are escaped
- URL param parser:
  - `exclude=senior` → `["senior"]`
  - `exclude=senior,,staff` → `["senior","staff"]`
  - `exclude=` → `[]`
  - `exclude=` absent → `undefined`
  - 50-token cap honored
  - Encoded characters (`%2C`, spaces) round-trip correctly
- URL param serializer is the inverse of the parser

### Provider-level

- No exclusions → output identical to existing Typesense output
- Single keyword matches one posting title in one company → that posting is dropped, counts decremented, company kept if other postings survive
- All postings in a company match → company dropped from result set
- Multiple keywords → any-match (OR) semantics, not all-match
- Case-insensitivity: `"SENIOR"`, `"Senior"`, `"senior"` all match a keyword `"senior"`
- Word-boundary semantics: keyword `"senior"` matches `"Senior Software Engineer"` but NOT `"Seniority Product Lead"`
- Multi-word keywords: keyword `"head of"` matches `"Head of Product"` and `"Regional Head of Sales"`
- Over-fetch fills a short page: initial fetch returns 20, exclusions drop 5 → follow-up fetch fills to 20
- One follow-up cap honored: if the second fetch still comes up short, we return < limit without looping

### Integration (Playwright / UI)

- Add a keyword via the new input → URL updates → chip appears → matching jobs disappear from results
- Dismiss a chip → URL updates → removed keyword no longer filters
- "Clear all exclusions" action removes every exclusion chip
- Share a URL with exclusions → loading it in another session restores the same filter state and results
- Save the Explore view as a watchlist → inspect the DB row's `filters` JSONB → confirms `excludeTitles` is present

## Deferred (explicitly not in this spec)

- Applying `excludeTitles` in `runWatchlistSearch()` when viewing a saved watchlist
- A watchlist-editor UI section for exclusions (Phase 2)
- Per-user default exclusions (`user.default_exclude_titles`) that auto-apply across sessions
- "For You" page that composes exclusions with Phase 2 resume scoring
- Crawler `alert-filters.yaml` ↔ web `excludeTitles` unification
- Email alerts keyed off exclusions

## Open Questions

None remaining — all decisions are resolved.
