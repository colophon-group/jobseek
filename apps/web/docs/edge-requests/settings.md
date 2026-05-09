# Settings Page (`/:lang/settings`)

**Route group:** `(app)` | **Rendering:** Dynamic (`force-dynamic` on app layout)

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/settings` HTML document | SSR | Serverless function — fetches preferences + languages + currencies |
| 2 | Middleware redirect | Edge function | Only if visiting `/settings` without locale prefix |
| 3-6 | JS chunks | Static (CDN) | Framework + GeneralSettings + settings layout sidebar |
| 7 | CSS bundle | Static (CDN) | Tailwind |
| 8 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font |
| 9 | `/js_wide_logo_black.svg` or `_white.svg` | Static (CDN) | AppHeader logo |
| 10 | `/favicon.ico` | Static (CDN) | Browser |
| 11 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` |
| 12 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` |
| 13 | Analytics beacon POST | Edge | Post-load telemetry |

## Server-side data fetching (during SSR)

- App layout: `getSession()`, `getPreferences()`, `getSavedJobStatuses()`, `getStarredCompanyIds()`
- `getPreferences()` — current user settings (language, currency, salary period)
- `getAvailableJobLanguages()` — list of available job posting languages
- `getCurrencyRates()` — available currencies for salary display

## Client-side requests (user interaction)

| Request | Type | Trigger |
|---------|------|---------|
| Server action: `updatePreferences()` | Serverless function | Save changed settings |

## Notes

- Settings layout adds a sidebar navigation (General, Account, Billing).
- No images beyond header logo. Form-based page.

## Fluid compute (serverless function duration)

### SSR render

| Step | Queries | Pattern | Cache | Est. duration |
|------|---------|---------|-------|---------------|
| `getSession()` | 1 | — | Redis 5min | 5-90ms |
| `getPreferences()` | 1 | parallel | None | 10-30ms |
| `getSavedJobStatuses()` | 1 | parallel | None | 10-30ms |
| `getStarredCompanyIds()` | 1 | parallel | None | 10-30ms |
| `getPreferences()` (page) | 0 | — | React `cache()` dedup | 0ms |
| `getAvailableJobLanguages()` | 1 (Typesense facet) | — | `'use cache'` 1h | ~600ms cold, <1ms warm |
| `getCurrencyRates()` | 1 | — | `'use cache'` 1h | ~200ms cold, <1ms warm |

**Total DB queries:** 7 (5 if languages + currencies cached)
**Estimated function duration:** 40-120ms (warm instance)

Lightweight. The language list and currency rates are cached for 1 hour —
most renders hit Redis for these and skip the DB.

## Estimated edge requests

**First visit (cold cache):** ~13
**Subsequent visit (warm cache):** ~2
