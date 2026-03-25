# Company Page (`/:lang/company/:slug`)

**Route group:** `(app)` | **Rendering:** Dynamic (`force-dynamic` on app layout)

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/company/:slug` HTML document | SSR | Serverless function — fetches company data + postings |
| 2 | Middleware redirect | Edge function | Only if visiting `/company/:slug` without locale prefix |
| 3-9 | JS chunks | Static (CDN) | Framework + CompanyPage + job cards + filter components |
| 10 | CSS bundle | Static (CDN) | Tailwind |
| 11 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font |
| 12 | `/js_wide_logo_black.svg` or `_white.svg` | Static (CDN) | AppHeader logo |
| 13 | `/_next/image?url=...` (company logo) | Edge | Company logo from remote CDN |
| 14 | `/_next/image?url=...` (company icon) | Edge | Company icon/favicon from remote CDN |
| 15 | `/favicon.ico` | Static (CDN) | Browser |
| 16 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` |
| 17 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` |
| 18 | Analytics beacon POST | Edge | Post-load telemetry |

## Server-side data fetching (during SSR)

- `getSession()`, `getPreferences()`, `getSavedJobStatuses()`, `getStarredCompanyIds()` — app layout
- `getCompanyBySlug(slug, locale)` — company details
- `getCompanyPostings(...)` — paginated job listings with filters
- `parseSearchFilters()` — resolve filter params

## Client-side requests (user interaction)

| Request | Type | Trigger |
|---------|------|---------|
| Server action: `getCompanyPostings()` | Serverless function | Filter/paginate postings |
| Server action: `getPostingDetail()` | Serverless function | Click on a job posting |
| `fetch(posting.descriptionUrl)` | External | View job description HTML |
| Server action: `toggleSavedJob()` | Serverless function | Save/unsave a job |
| Server action: `toggleStarredCompany()` | Serverless function | Star/unstar company |

## OG image

When shared on social media:
- `/:lang/company/:slug/opengraph-image` — dynamically generated PNG with company name/logo (1 edge + 1 serverless function invocation)

## Notes

- Contains Organization + BreadcrumbList JSON-LD structured data (inlined in HTML).
- Geolocation headers (`x-vercel-ip-latitude`, `x-vercel-ip-longitude`) read during SSR for location-aware sorting — no extra request, Vercel injects these headers automatically.
- The page uses the same search filter components as Explore, sharing JS chunks.

## Fluid compute (serverless function duration)

### SSR render

| Step | Queries | Pattern | Cache | Est. duration |
|------|---------|---------|-------|---------------|
| `getSession()` | 1 | — | Redis 5min | 5-90ms |
| `getPreferences()` | 1 | parallel | None | 10-30ms |
| `getSavedJobStatuses()` | 1 | parallel | None | 10-30ms |
| `getStarredCompanyIds()` | 1 | parallel | None | 10-30ms |
| `getCompanyBySlug()` | 1 | — | Redis 5min | 10-30ms |
| `parseSearchFilters()` | 0-4 | parallel | None | 5-20ms |
| `getCompanyPostings()` | 2-3 | mixed | Redis 5min | 20-80ms |

**Total DB queries:** 7-9
**Estimated function duration:** 60-200ms (warm instance)

Second-highest traffic dynamic page. Company detail is cached in Redis, so
repeat views of the same company are fast. Posting queries apply the same
multi-CTE search as explore but scoped to a single company.

### Client-side server actions

| Action | Queries | Cache | Est. duration |
|--------|---------|-------|---------------|
| `getCompanyPostings()` | 2-3 | Redis 5min | 20-80ms |
| `getPostingDetail()` | 3 (sequential) | Redis 5min | 20-100ms |
| `toggleSavedJob()` | 2 | None | 15-50ms |
| `toggleStarredCompany()` | 2 | None | 15-50ms |

## Estimated edge requests

**First visit (cold cache):** ~18
**Subsequent visit (warm cache):** ~2 (document always SSR + analytics)
**Per interaction:** ~1-2
