# Explore Page (`/:lang/explore`)

**Route group:** `(app)` | **Rendering:** Dynamic (`force-dynamic` on app layout)

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/explore` HTML document | SSR | Serverless function — runs DB queries for search results |
| 2 | Middleware redirect | Edge function | Only if visiting `/explore` without locale prefix |
| 3-10 | JS chunks | Static (CDN) | Framework + SearchPage + search bar + filter components + company cards + job detail panel (heavy page) |
| 11 | CSS bundle | Static (CDN) | Tailwind |
| 12 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font |
| 13 | `/js_wide_logo_black.svg` or `_white.svg` | Static (CDN) | AppHeader logo |
| 14 | `/favicon.ico` | Static (CDN) | Browser |
| 15 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` |
| 16 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` |
| 17 | Analytics beacon POST | Edge | Post-load telemetry |
| 18-N | `/_next/image?url=...` (company logos) | Edge | Next.js image optimization for company logo/icon images from `jobseek-assets.colophon-group.org` |

## Server-side data fetching (during SSR)

These run server-side and do NOT generate additional edge requests:
- `getSession()` — session check
- `getPreferences()` — user preferences (currency, language, etc.)
- `getSavedJobStatuses()` — saved job status map
- `getStarredCompanyIds()` — starred companies
- `parseSearchFilters()` — resolve search params to IDs
- `searchJobs()` or `listTopCompanies()` — main search query

All via server actions (direct DB calls, no HTTP round-trips).

## Client-side requests (user interaction)

| Request | Type | Trigger |
|---------|------|---------|
| Server action: `searchJobs()` | Serverless function | User types search / changes filters |
| Server action: `listTopCompanies()` | Serverless function | Load more results |
| Server action: `getPostingDetail()` | Serverless function | Click on a job posting |
| `fetch(posting.descriptionUrl)` | External | View job description HTML (external URL from company ATS) |
| Server action: `toggleSavedJob()` | Serverless function | Save/unsave a job |
| Server action: `toggleStarredCompany()` | Serverless function | Star/unstar a company |
| `/flags/{country}.svg` | Static (CDN) | Country flag icons in location filters (cached 1 year) |

## Prefetch requests (eliminated)

Before the prefetch fix, this page was the worst offender for phantom SSR:

| Link | Target | Cost | Status |
|------|--------|------|--------|
| 10 company card `<Link>` elements | `/company/:slug` (dynamic SSR) | 10 serverless invocations, each running full company page SSR with DB queries | **Fixed** — `prefetch={false}` |
| Language note "change" link | `/settings` (dynamic SSR) | 1 serverless invocation | **Fixed** — `prefetch={false}` |
| Job detail company link | `/company/:slug` (dynamic SSR) | 1 serverless invocation per detail view | **Fixed** — `prefetch={false}` |

Up to **11 phantom SSR invocations per explore page view** are now eliminated.

## Notes

- **Heaviest page in the app.** The app layout (`force-dynamic`) fetches session, preferences, saved jobs, and starred companies on every request — all server-side.
- Company logos come from the remote CDN (`jobseek-assets.colophon-group.org`) and are optimized through `/_next/image`. Each visible company generates 1 image optimization edge request (cached after first hit, 1 year TTL).
- With 10 companies visible by default, expect ~10 additional `/_next/image` requests on first load.
- Search interactions trigger server actions (Next.js RSC protocol), each counting as 1 edge request + 1 serverless function invocation.
- Flag SVGs for location chips are served from `/flags/` with 1-year immutable cache.

## Fluid compute (serverless function duration)

### SSR render

| Step | Queries | Pattern | Cache | Est. duration |
|------|---------|---------|-------|---------------|
| `getSession()` | 1 | — | Redis 5min | 5-90ms |
| `getPreferences()` | 1 | parallel | None | 10-30ms |
| `getSavedJobStatuses()` | 1 | parallel | None | 10-30ms |
| `getStarredCompanyIds()` | 1 | parallel | None | 10-30ms |
| `parseSearchFilters()` | 0-4 | parallel | None | 5-20ms (slug→ID lookups) |
| `searchJobs()` or `listTopCompanies()` | 3-5 | mixed | Redis 5-10min | 20-100ms |
| `getPreferences()` (page) | 0 | — | React `cache()` dedup | 0ms |

**Total DB queries:** 7-12
**Estimated function duration:** 80-250ms (warm instance)

This is the **heaviest page** in the app. The search query uses multi-CTE SQL
with array intersection filters for location, occupation, technology, and
seniority. Location/occupation IDs are expanded from parent→children before
the main query.

Geolocation headers (`x-vercel-ip-latitude/longitude`) are read from
`headers()` for distance-based sorting — free (injected by Vercel, no extra
call).

### Client-side server actions

| Action | Queries | Cache | Est. duration |
|--------|---------|-------|---------------|
| `searchJobs()` | 3-5 | Redis 5min | 30-150ms |
| `listTopCompanies()` | 3-5 | Redis 10min | 30-150ms |
| `getPostingDetail()` | 3 (sequential) | Redis 5min | 20-100ms |
| `toggleSavedJob()` | 2 (sequential) | None | 15-50ms |
| `toggleStarredCompany()` | 2 (sequential) | None | 15-50ms |

Search actions fire on every filter change — high frequency. Redis caching
absorbs repeated identical queries within the 5-min window.

## Estimated edge requests

**First visit (cold cache):** ~27 (17 base + ~10 company logos)
**Subsequent visit (warm cache):** ~2 (document always SSR + analytics)
**Per search interaction:** ~1-2 (server action + optional detail fetch)
