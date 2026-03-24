# My Jobs Page (`/:lang/my-jobs`)

**Route group:** `(app)` | **Rendering:** Dynamic (`force-dynamic` on app layout)

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/my-jobs` HTML document | SSR | Serverless function — fetches saved jobs |
| 2 | Middleware redirect | Edge function | Only if visiting `/my-jobs` without locale prefix |
| 3-8 | JS chunks | Static (CDN) | Framework + MyJobsPage + job cards + detail panel |
| 9 | CSS bundle | Static (CDN) | Tailwind |
| 10 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font |
| 11 | `/js_wide_logo_black.svg` or `_white.svg` | Static (CDN) | AppHeader logo |
| 12 | `/favicon.ico` | Static (CDN) | Browser |
| 13 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` |
| 14 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` |
| 15 | Analytics beacon POST | Edge | Post-load telemetry |
| 16-N | `/_next/image?url=...` (company logos) | Edge | Logos for companies of saved jobs |

## Server-side data fetching (during SSR)

- App layout: `getSession()`, `getPreferences()`, `getSavedJobStatuses()`, `getStarredCompanyIds()`
- `getMyJobs({ offset: 0, limit: 20 })` — paginated list of saved jobs

## Client-side requests (user interaction)

| Request | Type | Trigger |
|---------|------|---------|
| Server action: `getMyJobs()` | Serverless function | Paginate / filter saved jobs |
| Server action: `getPostingDetail()` | Serverless function | Click on a saved job |
| Server action: `updateSavedJobStatus()` | Serverless function | Change status (saved/applied/interviewing/etc.) |
| Server action: `addInterview()` | Serverless function | Add interview entry |
| `fetch(posting.descriptionUrl)` | External | View job description HTML |

## Notes

- Number of company logo requests depends on how many distinct companies appear in saved jobs.
- Job cards show company icons — each unique company triggers 1 `/_next/image` request.
- Status changes and interview additions are server actions (1 edge request each).

## Estimated edge requests

**First visit (cold cache):** ~20 (15 base + ~5 company logos)
**Subsequent visit (warm cache):** ~2
**Per interaction:** ~1
