# Shared Watchlist Page (`/:lang/:userSlug/:watchlistSlug`)

**Route group:** `(app)` | **Rendering:** Dynamic (`force-dynamic` on app layout)

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/:userSlug/:watchlistSlug` HTML document | SSR | Serverless function — fetches watchlist + postings + resolves filters |
| 2 | Middleware redirect | Edge function | Only if visiting without locale prefix |
| 3-9 | JS chunks | Static (CDN) | Framework + WatchlistViewPage + job cards + filter display |
| 10 | CSS bundle | Static (CDN) | Tailwind |
| 11 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font |
| 12 | `/js_wide_logo_black.svg` or `_white.svg` | Static (CDN) | AppHeader logo |
| 13 | `/favicon.ico` | Static (CDN) | Browser |
| 14 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` |
| 15 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` |
| 16 | Analytics beacon POST | Edge | Post-load telemetry |
| 17-N | `/_next/image?url=...` (company logos) | Edge | Logos for companies in the watchlist |

## Server-side data fetching (during SSR)

- App layout: `getSession()`, `getPreferences()`, `getSavedJobStatuses()`, `getStarredCompanyIds()`
- `getWatchlistByUserAndSlug(userSlug, watchlistSlug)` — watchlist details + companies
- `resolveLocationSlugs()`, `resolveOccupationSlugs()`, `resolveSenioritySlugs()`, `resolveTechnologySlugs()` — resolve filter slugs to display names
- `getWatchlistPostings(...)` — matching job postings
- `getUserPlan()`, `canCreateWatchlist()` — plan checks

## Client-side requests (user interaction)

| Request | Type | Trigger |
|---------|------|---------|
| Server action: `getWatchlistPostings()` | Serverless function | Paginate results |
| Server action: `getPostingDetail()` | Serverless function | Click on a job |
| Server action: `toggleSavedJob()` | Serverless function | Save a job from the watchlist |
| Server action: `forkWatchlist()` | Serverless function | Fork/clone the watchlist |

## Notes

- **Publicly accessible** — visitors without accounts can view public watchlists.
- Contains BreadcrumbList JSON-LD structured data.
- The heaviest SSR of all pages: resolves multiple filter types + fetches postings in parallel.
- Company logos depend on watchlist scope — a watchlist tracking 5 companies shows ~5 logos.

## Fluid compute (serverless function duration)

### SSR render

| Step | Queries | Pattern | Cache | Est. duration |
|------|---------|---------|-------|---------------|
| `getSession()` | 1 | — | Redis 5min | 5-90ms |
| `getPreferences()` | 1 | parallel | None | 10-30ms |
| `getSavedJobStatuses()` | 1 | parallel | None | 10-30ms |
| `getStarredCompanyIds()` | 1 | parallel | None | 10-30ms |
| `getWatchlistByUserAndSlug()` | 4 | sequential | None | 30-100ms |
| Resolve filter slugs (×4 types) | 4 | parallel | None | 10-30ms |
| `getWatchlistPostings()` | 4 | mixed | None | 30-100ms |
| `getUserPlan()` + `canCreateWatchlist()` | 2 | sequential | None | 15-40ms |

**Total DB queries:** 10-14
**Estimated function duration:** 100-350ms (warm instance)

**Heaviest single render in the app.** `getWatchlistByUserAndSlug()` alone
runs 4 sequential queries (resolve user → fetch watchlist → touch
lastAccessedAt → fetch companies). Then filter slug resolution and posting
queries add another 8 queries.

No Redis caching on any watchlist query — every render hits the DB. This is
a strong candidate for caching, especially for public watchlists that are
shared via social media and may receive bursts of traffic.

### Client-side server actions

| Action | Queries | Cache | Est. duration |
|--------|---------|-------|---------------|
| `getWatchlistPostings()` (paginate) | 4 (mixed) | None | 30-120ms |
| `getPostingDetail()` | 3 (sequential) | Redis 5min | 20-100ms |
| `toggleSavedJob()` | 2 | None | 15-50ms |
| `copyWatchlist()` | 3 (sequential) | None | 20-80ms |

## Estimated edge requests

**First visit (cold cache):** ~21 (16 base + ~5 company logos)
**Subsequent visit (warm cache):** ~2
