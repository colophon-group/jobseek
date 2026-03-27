# Data Fetching & Anonymous Truncation

## Architecture

The `(app)` route group serves **static page shells from CDN**. All data
fetching happens client-side via Next.js server actions called on mount.

```
Browser loads static HTML (CDN)
  → AppBootstrapProvider calls fetchAppBootstrap() once
  → Each page's loader component calls its data server action
  → Server actions execute DB queries, return JSON
  → Client renders with fetched data
```

### Why not SSR?

The layout previously used `export const dynamic = "force-dynamic"`, causing
every page navigation to trigger a full server-side render — 4+ DB queries in
the layout alone (session, preferences, saved jobs, starred companies), plus
7-12 per page. This drove high Vercel fluid compute costs.

With client-side fetching:
- The layout renders once as static HTML, served from CDN (zero compute)
- `AppBootstrapProvider` fetches user data once on mount via a single
  server action, and the data persists across navigations (React doesn't
  unmount layouts)
- Each page shows a skeleton, then loads data — the server action invocation
  is much cheaper than a full SSR render (JSON response vs. full React tree)

### Why server actions, not the `/api/v1/*` routes?

The app has two data paths:

| Path | Consumers | Auth | Data shape |
|------|-----------|------|------------|
| **Server actions** | UI (client components) | Cookie-based, automatic | Rich (filter state, preferences, geo) |
| **`/api/v1/*` routes** | External (AI agents, integrations) | None (rate-limited by IP) | Stripped-down (5 companies max, simplified schema) |

Server actions were chosen for the UI because:

1. **Already the data layer** — all business logic (filter parsing, location
   expansion, preference resolution, caching) lives in server actions. The
   API routes are thin wrappers that call these same actions.
2. **Auth for free** — server actions receive request cookies automatically
   when called from client components. No auth middleware needed.
3. **Type safety** — end-to-end TypeScript types without manual
   request/response schemas.
4. **Richer data** — the UI needs geo sorting, salary conversion, job
   language preferences, parsed filter state. The API routes intentionally
   omit this.

Server actions called from client components are POST requests under the
hood — functionally equivalent to API calls, but with better DX.

## Anonymous Truncation

Unauthenticated users see limited results to prevent data scraping while
keeping the product usable.

### Limits

| Context | Limit | Constant |
|---------|-------|----------|
| Search results (companies) | 15 | `ANON_MAX_COMPANIES` |
| Company card postings (search page) | 20 | `ANON_MAX_CARD_POSTINGS` |
| Company detail page postings | 40 | `ANON_MAX_POSTINGS` |
| Watchlist postings | 20 | `ANON_MAX_WATCHLIST_POSTINGS` |

Constants are in `src/lib/search/constants.ts`.

### Enforcement

Truncation is **enforced server-side** in each server action
(`searchJobs`, `listTopCompanies`, `loadMorePostings`,
`getCompanyPostings`, `getWatchlistPostings`). Each checks
`getSessionUserId()` — if null, caps results at the limit and sets
`truncated: true` in the response.

The client reads the `truncated` flag and replaces the infinite scroll
sentinel with a `TruncationPrompt` ("Sign in to see more"). The client-side
`hasMore` check is a UX optimization — even if bypassed, the server action
returns empty results.

### Why not restrict filters?

All filters remain available to anonymous users. The protection comes from
truncation, not filter restriction. With broad results and no fine-grained
enumeration, each query returns overlapping data. Combined with the existing
IP rate limiter (30 req/60s), comprehensive scraping is uneconomical.

## Bootstrap Flow

`AppBootstrapProvider` (client component in the layout) calls a single
`fetchAppBootstrap()` server action on mount:

```
fetchAppBootstrap()
  → getSession()          // Redis → DB fallback
  → if authenticated:
      Promise.all([
        getPreferences(),
        getSavedJobStatuses(),
        getStarredCompanyIds(),
      ])
  → returns { user, prefs, savedStatuses, starredIds }
```

This replaces the 4 separate SSR fetches that ran on every navigation.
The data is passed to nested providers (`SessionProvider`,
`SavedJobsProvider`, `StarredCompaniesProvider`, etc.) and persists
across page navigations.

### `isPending` state

While the bootstrap fetch is in flight, `SessionProvider` exposes
`isPending: true`. Auth-dependent components (header avatar, save/star
buttons, truncation prompt) check this to avoid flashing incorrect UI.

## ISR for SEO

Company and watchlist detail pages use `export const revalidate = 600`
for `generateMetadata()`. This caches OG tags and page titles for 10
minutes via ISR, preserving SEO without per-request compute. The page
body itself is a client component that fetches its own data.

## Page Conversion Pattern

Each page follows this structure:

```
page.tsx (server component, sync)
  → resolves locale from params
  → renders <PageLoader locale={locale} />

page-loader.tsx (client component)
  → calls server action on mount
  → shows skeleton while loading
  → renders existing page component with data
```

The existing client components (SearchPage, CompanyPage, etc.) are
unchanged — they still receive initial data as props. The loader is a
thin bridge that replaces the former SSR data fetch.
