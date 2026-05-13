# Watchlists Page (`/:lang/watchlists`)

**Route group:** `(app)` | **Rendering:** Dynamic (`force-dynamic` on app layout)

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/watchlists` HTML document | SSR | Serverless function — fetches user's watchlists |
| 2 | Middleware redirect | Edge function | Only if visiting `/watchlists` without locale prefix |
| 3-7 | JS chunks | Static (CDN) | Framework + WatchlistsPage + watchlist cards |
| 8 | CSS bundle | Static (CDN) | Tailwind |
| 9 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font |
| 10 | `/js_wide_logo_black.svg` or `_white.svg` | Static (CDN) | AppHeader logo |
| 11 | `/favicon.ico` | Static (CDN) | Browser |
| 12 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` |
| 13 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` |
| 14 | Analytics beacon POST | Edge | Post-load telemetry |

## Server-side data fetching (during SSR)

- App layout: `getSession()`, `getPreferences()`, `getSavedJobStatuses()`, `getStarredCompanyIds()`
- `getUserWatchlists()` — all user's watchlists
- `canCreateWatchlist(userId)` — check plan limits

## Client-side requests (user interaction)

| Request | Type | Trigger |
|---------|------|---------|
| Server action: `createWatchlist()` | Serverless function | Create new watchlist |
| Server action: `deleteWatchlist()` | Serverless function | Delete a watchlist |

## Notes

- Watchlist cards may show company logos if the watchlist is scoped to specific companies.
- Number of company logo `/_next/image` requests depends on watchlist content.

## Fluid compute (serverless function duration)

### SSR render

| Step | Queries | Pattern | Cache | Est. duration |
|------|---------|---------|-------|---------------|
| `getSession()` | 1 | — | Redis 5min | 5-90ms |
| `getPreferences()` | 1 | parallel | None | 10-30ms |
| `getSavedJobStatuses()` | 1 | parallel | None | 10-30ms |
| `getStarredCompanyIds()` | 1 | parallel | None | 10-30ms |
| `getUserWatchlists()` | 1 | — | None | 12-38ms |
| `canCreateWatchlist()` | 1 | — | None | 10-20ms |

**Total DB queries:** 5 (constant in N — fixed by #3176)
**Estimated function duration:** 50-100ms (warm instance)

**Previously the worst N+1 pattern in the app.** Pre-fix, `getUserWatchlists`
ran `resolveFilteredJobCount()` once per watchlist — each leg ran 4
parallel taxonomy lookups + 1 Typesense filtered count. A user with 50
watchlists paid 1.5-2.5s of mostly-serial Typesense round-trips (the
Typesense host is a single CX22 with effective concurrency ~2) on every
`/watchlists` load. Issue #3176 (PR fixing this file) collapsed the
fan-out into a single SQL query whose `watchlist_company JOIN
job_posting` subquery returns the denormalized "company-scope" active
count alongside each row.

**Trade-off:** the listing badge now ignores the per-watchlist filter
clauses (keywords, locations, work_mode, …) and the viewer's job-language
preference. The watchlist detail page still surfaces the filter-applied
count via `getWatchlistPostingDisplayCounts()`. Issue #3261 tracks a
batched `multi_search`-based path if/when filter-precise badges are
needed on the listing.

### Client-side server actions

| Action | Queries | Cache | Est. duration |
|--------|---------|-------|---------------|
| `createWatchlist()` | 2 (sequential) | None | 15-50ms |
| `deleteWatchlist()` | 2 (sequential) | None | 15-50ms |

## Estimated edge requests

**First visit (cold cache):** ~14
**Subsequent visit (warm cache):** ~2
