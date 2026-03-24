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

## Estimated edge requests

**First visit (cold cache):** ~14
**Subsequent visit (warm cache):** ~2
