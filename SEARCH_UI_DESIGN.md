# Search UI — MVP Design

## Overview

Full-text keyword search across job postings, with results grouped by company. Lives at `/app` (authenticated area).

## Search Behavior

- **Input**: Single search bar at the top of the page, explicit submit (Enter / button)
- **Scope**: Keyword search across all job posting fields (title, description, locations, employment type, etc.)
- **Language filtering**: Only surface postings matching the current UI locale. Non-English postings hidden unless the UI is set to that language. (Per-language knobs planned for later.)
- **Engine**: Postgres `tsvector` full-text search. Abstract behind a search service interface so the backend can be swapped (e.g. Meilisearch, Typesense) without changing the UI or API contract.
- **URL state**: Query and filters encoded in URL search params (`?q=...`) so searches are shareable/bookmarkable. Also persist search state in session/cookie to survive navigation within the app.

## Results Layout

Results are **grouped by company**, displayed as cards.

### Company Card

```
┌──────────────────────────────────────────────────────┐
│ [icon] Company Name                   [Follow] (noop) │
│ 5 active · 12 in the last year                        │
│ ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ │
│ │ ▸ Senior Software Engineer              2 days ago │ │
│ │ ▸ Backend Developer                    1 week ago  │ │
│ │ ▸ Product Manager                     3 weeks ago  │ │
│ │ ▸ Data Analyst                        1 month ago  │ │
│ │ ▸ DevOps Engineer                     2 months ago │ │
│ │                        (scrollable)                │ │
│ └────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

- **Icon**: Company `icon` field (minified/small)
- **Company name**: Text, clickable area inactive for now (future: link to `/app/company/[slug]`)
- **Follow button**: Rendered but disabled/noop for MVP
- **Stats line**: `{activeMatches} active · {yearMatches} in the last year`
  - `activeMatches` — matching postings with `status = 'active'`
  - `yearMatches` — matching postings (active + delisted) in the DB (DB holds all postings < 1 year; older data moves to long-term storage)
- **Job list**: All matching postings shown in a **scrollable container** within the card (max-height capped, vertical overflow scroll). Each row is **clickable** (noop for MVP; future: opens job detail pop-up). Each row shows:
  - **Title** (left-aligned)
  - **Relative time** since `firstSeenAt` (right-aligned, e.g. "2 days ago", "3 weeks ago")
- Sorted by relevance to query, then by `firstSeenAt` descending (newest first).

### Sorting

1. **Company cards**: By number of matches descending (most relevant companies first)
2. **Postings within a card**: By search relevance, then by `firstSeenAt` descending

### Pagination

- **Infinite scroll** on company cards. Load next batch when user scrolls near bottom.
- Batch size: ~10 companies per load (tunable).
- Job list within a card scrolls independently (max-height container).

## States

### Empty State (no query)

Show the largest companies by active job count as a default view. Same card layout, no search ranking — just ordered by `activeJobCount` descending.

> Future: replace with personalized recommendations or trending companies.

### Zero Results State

Prompt to submit a company request:

> **No results found for "{query}"**
>
> Did not find the company you were looking for?
> [Request a company →](/app/progress)

Link to the progress page which has the company request form.

### Loading State

Skeleton cards while search is in flight.

## Data Flow

```
URL params (?q=...)
       │
       ▼
Server Action / API route
       │
       ▼
Search Service (interface)
       │
       ├── PostgresSearchProvider (MVP: tsvector)
       └── [future: MeilisearchProvider, TypesenseProvider]
       │
       ▼
{companies: [{company, activeMatches, yearMatches, postings: [...]}]}
       │
       ▼
React Server Component (initial render)
  + Client Component (infinite scroll, URL sync)
```

### Search Service Interface

```typescript
interface SearchResult {
  company: {
    id: string;
    name: string;
    slug: string;
    icon: string | null;
  };
  /** Matching postings that are currently active */
  activeMatches: number;
  /** Matching postings (active + delisted) in the DB (DB holds < 1 year) */
  yearMatches: number;
  postings: {
    id: string;
    title: string | null;
    firstSeenAt: Date;
    relevanceScore: number;
  }[];
}

interface SearchProvider {
  search(params: {
    query: string;
    language: string;
    offset: number;
    limit: number;
  }): Promise<{
    companies: SearchResult[];
    totalCompanies: number;
  }>;

  /** Default listing when no query is provided */
  listTopCompanies(params: {
    language: string;
    offset: number;
    limit: number;
  }): Promise<{
    companies: SearchResult[];
    totalCompanies: number;
  }>;
}
```

## DB Requirements (Postgres MVP)

- Add `tsvector` column or generated column on `job_posting` combining title, description, locations, employment type
- GIN index on the tsvector column
- Query filters on `status = 'active'` and `language` matching UI locale
- Aggregation per company: conditional `COUNT(*)` for active matches vs year matches
- DB assumed to hold all postings < 1 year; older data archived to long-term storage

## Component Structure

```
app/[lang]/(app)/app/
├── page.tsx                    # Server component: reads ?q=, calls search, renders initial batch
├── search-bar.tsx              # Client: input + submit, pushes to URL params
├── search-results.tsx          # Client: renders company cards, handles infinite scroll
├── company-card.tsx            # Company card with scrollable job list
├── empty-state.tsx             # Top companies (no query)
├── zero-results.tsx            # "No results" + company request CTA
└── progress/
    ├── page.tsx                # Existing "under development" page
    └── company-request-form.tsx
```

## Future (out of MVP scope)

- **Language knobs**: User can opt into postings in specific languages regardless of UI locale
- **Company page**: `/app/company/[slug]` — full listing, details, follow
- **Job detail pop-up**: Click a posting row to see full description in a modal/drawer
- **Saved searches & alerts**: Save a query, get notified on new matches
- **Follow button**: Subscribe to a company for notifications
- **Advanced filters**: Employment type, location type, salary range, date range
- **Mobile layout**: Collapsible filter panel, responsive cards
- **Search engine swap**: Migrate from tsvector to dedicated search (Meilisearch/Typesense)
- **Personalized default view**: Replace "top companies" with recommendations
