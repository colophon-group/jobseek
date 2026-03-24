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
- `getStats()` — funnel data, conversion rates, activity heatmap

## Notes

- Data-visualization page (charts, funnel, heatmap). No images beyond header logo.
- All stats computed server-side. No client-side API calls on load.
- Chart JS bundles may be slightly larger than typical pages.

## Estimated edge requests

**First visit (cold cache):** ~14
**Subsequent visit (warm cache):** ~2
