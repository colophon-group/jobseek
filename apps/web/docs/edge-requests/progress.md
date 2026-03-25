# Progress Page (`/:lang/progress`)

**Route group:** `(app)` | **Rendering:** Dynamic (`force-dynamic` on app layout)

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/progress` HTML document | SSR | Serverless function — fetches platform stats |
| 2 | Middleware redirect | Edge function | Only if visiting `/progress` without locale prefix |
| 3-6 | JS chunks | Static (CDN) | Framework + page + CompanyRequestForm |
| 7 | CSS bundle | Static (CDN) | Tailwind |
| 8 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font |
| 9 | `/js_wide_logo_black.svg` or `_white.svg` | Static (CDN) | AppHeader logo |
| 10 | `/favicon.ico` | Static (CDN) | Browser |
| 11 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` |
| 12 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` |
| 13 | Analytics beacon POST | Edge | Post-load telemetry |

## Server-side data fetching (during SSR)

- App layout: `getSession()`, `getPreferences()`, `getSavedJobStatuses()`, `getStarredCompanyIds()`
- `getStats()` — company count and job posting count

## Client-side requests (user interaction)

| Request | Type | Trigger |
|---------|------|---------|
| Server action: company request submission | Serverless function | Submit company request form |

## Notes

- "Under Active Development" placeholder page showing platform stats.
- Contains a CompanyRequestForm for requesting new companies to track.
- Lightweight page — no images beyond header logo, no company logos.

## Fluid compute (serverless function duration)

### SSR render

| Step | Queries | Pattern | Cache | Est. duration |
|------|---------|---------|-------|---------------|
| `getSession()` | 1 | — | Redis 5min | 5-90ms |
| `getPreferences()` | 1 | parallel | None | 10-30ms |
| `getSavedJobStatuses()` | 1 | parallel | None | 10-30ms |
| `getStarredCompanyIds()` | 1 | parallel | None | 10-30ms |
| `getStats()` (public) | 2 | parallel | Redis 6h | 10-30ms |

**Total DB queries:** 6 (4 if stats cached)
**Estimated function duration:** 30-80ms (warm instance)

Lightest `(app)` page. Public stats (company count + posting count) are
cached for 6 hours and run in `Promise.all()` — often a single Redis lookup.

## Estimated edge requests

**First visit (cold cache):** ~13
**Subsequent visit (warm cache):** ~2
