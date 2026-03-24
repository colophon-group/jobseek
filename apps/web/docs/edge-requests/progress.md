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

## Estimated edge requests

**First visit (cold cache):** ~13
**Subsequent visit (warm cache):** ~2
