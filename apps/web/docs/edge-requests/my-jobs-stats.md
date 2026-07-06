# My Jobs Stats Page (`/:lang/my-jobs/stats`)

**Route group:** `(app)` | **Rendering:** Dynamic (`force-dynamic` on app layout)

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/my-jobs/stats` HTML document | SSR | Serverless function — fetches stats aggregates |
| 2 | Middleware redirect | Edge function | Only if visiting `/my-jobs/stats` without locale prefix |
| 3-7 | JS chunks | Static (CDN) | Framework + StatsPage + chart components |
| 8 | CSS bundle | Static (CDN) | Tailwind |
| 9 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font |
| 10 | `/js_wide_logo_black.svg` or `_white.svg` | Static (CDN) | AppHeader logo |
| 11 | `/favicon.ico` | Static (CDN) | Browser |
| 12 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` |
| 13 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` |
| 14 | Analytics beacon POST | Edge | Post-load telemetry |

## Server-side data fetching (during SSR)

- App layout: `getSession()`, `getPreferences()`, `getSavedJobStatuses()`, `getStarredCompanyIds()`
- `getMyJobsStats()` — funnel data, conversion rates, activity heatmap

## Notes

- Data-visualization page (charts, funnel, heatmap). No images beyond header logo.
- All stats computed server-side. No client-side API calls on load.
- Chart JS bundles may be slightly larger than typical pages.

## Fluid compute (serverless function duration)

### SSR render

| Step | Queries | Pattern | Cache | Est. duration |
|------|---------|---------|-------|---------------|
| `getSession()` | 1 | — | Redis 5min | 5-90ms |
| `getPreferences()` | 1 | parallel | None | 10-30ms |
| `getSavedJobStatuses()` | 1 | parallel | None | 10-30ms |
| `getStarredCompanyIds()` | 1 | parallel | None | 10-30ms |
| `getMyJobsStats()` funnel | 1 | sequential | None | 15-40ms |
| `getMyJobsStats()` activity | 1 | sequential | None | 15-40ms |

**Total DB queries:** 6
**Estimated function duration:** 50-150ms (warm instance)

Moderate compute. Stats queries aggregate over the user's saved jobs and
interviews — heavier than a simple SELECT but bounded by the user's data
volume. No Redis caching.

## Estimated edge requests

**First visit (cold cache):** ~14
**Subsequent visit (warm cache):** ~2
