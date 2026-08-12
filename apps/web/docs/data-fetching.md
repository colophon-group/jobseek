# Data Fetching & Anonymous Truncation

## Architecture

The `(app)` route group serves **cached page shells from CDN**. Reads use the
cheapest boundary that preserves correctness:

- anonymous, viewer-independent defaults render in cached Server Components;
- signed-in or request-specific data loads through narrowly scoped Server
  Actions after hydration;
- interactive search refreshes use browser-direct Typesense where the public
  search contract permits it;
- mutations remain Server Actions.

```
Browser loads cached HTML with anonymous result markup (CDN)
  → App layout embeds cached, viewer-independent currency rates
  → Anonymous AppBootstrapProvider selects local anonymous context (no RPC)
  → Signed-in/bootstrap or filtered views request personalized data
  → Client owns subsequent interaction and mutation state
```

### Why not SSR?

The layout previously used `export const dynamic = "force-dynamic"`, causing
every page navigation to trigger a full server-side render — 4+ DB queries in
the layout alone (session, preferences, saved jobs, starred companies), plus
7-12 per page. This drove high Vercel fluid compute costs.

With cached defaults plus conditional client fetching:
- Anonymous Explore/company/watchlist documents contain meaningful result
  markup before JavaScript runs.
- `AppBootstrapProvider` calls `fetchAppBootstrap()` only when the
  non-sensitive `logged_in` hint says a personalized session may exist.
- Viewer-independent currency rates come from the server layout's hourly
  `use cache` service read; the client provider does not POST a Server Action
  on mount. Cold/error shells receive the migration's complete approximate
  currency seed rather than a EUR-only identity-conversion fallback.
- Filtered and signed-in views retain the personalized behavior they need.

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

## Server Action Naming

Server action names are part of the UI data contract, so read-action verbs
carry meaning:

- **Granular reads** use `getX` when they return one independently useful
  resource or aggregate (`getPostingDetail`, `getSiteStats`,
  `getMyJobsStats`). Existing domain verbs such as `searchX`, `listX`,
  `suggestX`, `resolveX`, and `expandX` remain valid when they describe the
  operation more precisely than `get`.
- **Composite page/bootstrap bundles** use `fetchX` and live in
  `bootstrap.ts` or `*-page-data.ts`. These actions collect several granular
  reads into the full shape a loader needs, so their names include the bundle
  type: `fetchAppBootstrap`, `fetchExplorePageData`,
  `fetchExplorePageDefaults`, `fetchCompanyPageData`,
  `fetchCompanyPageDefaults`, and `fetchWatchlistPageData`.
- **Cursor or infinite-scroll reads** are still reads; use `getX` or a
  domain verb, not `loadX`. The company-card posting action is
  `getMorePostings`.

Exported server action function names must be unique across
`src/lib/actions/*.ts`; add a domain qualifier when the noun would otherwise
be ambiguous across modules. `src/lib/actions/__tests__/naming-conventions.test.ts`
enforces the uniqueness rule, blocks `load*` exports, and keeps `fetch*`
reserved for bootstrap/page-data bundlers.

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
(`searchJobs`, `listTopCompanies`, `getMorePostings`,
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
`fetchAppBootstrap()` Server Action only when `logged_in` is present:

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
Anonymous viewers take a synchronous local path with no bootstrap request.
The cached server layout separately passes the currency-rate table into
`SalaryDisplayProvider`; this data is public and viewer-independent, so it
does not belong in either the authenticated bootstrap or a mount effect.

The authenticated data is passed to nested providers (`SessionProvider`,
`SavedJobsProvider`, `StarredCompaniesProvider`, etc.) and persists
across page navigations.

### `isPending` state

While the bootstrap fetch is in flight, `SessionProvider` exposes
`isPending: true`. Auth-dependent components (header avatar, save/star
buttons, truncation prompt) check this to avoid flashing incorrect UI.

## ISR for SEO

Explore, company, and watchlist detail routes use Cache Components to embed
anonymous defaults and cached metadata without request-bound APIs. Dynamic
personalization stays outside those cache functions. Keep the production-build
classification assertions in `__tests__/build-output.test.ts` green.

## Page Conversion Pattern

High-traffic public app routes follow this structure:

```
page.tsx (cached server component)
  → resolves locale from params
  → fetches viewer-independent defaults
  → renders the client page with initialData

client page
  → renders initialData in raw HTML
  → conditionally calls a personalized Server Action for auth/filter hints
  → handles subsequent interaction through browser search/actions
```

Do not use `useSearchParams()` in the cached result-owning subtree merely to
restore filters after hydration: it suspends the subtree and can replace raw
results with `loading.tsx`. Use a query-agnostic server snapshot and observe
the browser URL after hydration instead.
