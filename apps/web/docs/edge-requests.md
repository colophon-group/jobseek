# Edge Requests, Rendering Strategy & Fluid Compute

## How it works

The app splits pages into **static** (served from CDN, zero edge cost) and **dynamic** (rendered per-request on edge functions) using Next.js App Router conventions.

### Static pages (public, auth)

Public marketing pages and auth pages contain no user-specific data. They are **pre-rendered at build time** for every locale via `generateStaticParams` in `app/[lang]/layout.tsx`:

```
/en/              — static
/de/              — static
/en/how-we-index  — static
/en/terms         — static
/en/privacy-policy — static
/en/license       — static
/en/sign-in       — static (client component layout)
/en/sign-up       — static
/en/check-email   — static (client component, reads sessionStorage)
/en/verify-email  — static (client component, reads ?token query param)
```

These pages produce **zero edge function invocations** — Vercel serves them directly from the CDN.

### Dynamic pages (app, API)

Pages that check authentication or read cookies are rendered on every request:

```
/en/app/*         — dynamic (reads headers() for session)
/api/auth/*       — dynamic (Better Auth handler)
```

This is correct — these pages genuinely need per-request data.

### Middleware

The middleware only runs for paths **without** a locale prefix (e.g. bare `/`, `/how-we-index`). It redirects to the locale-prefixed version based on `Accept-Language`.

Paths that already have a locale prefix (`/en/...`, `/de/...`) **skip the middleware entirely** — no edge invocation.

### Theme

Theme is handled entirely client-side by `next-themes`. It injects an inline script that sets the `class` attribute on `<html>` before first paint. No server-side cookie reading needed.

### Locale on `<html lang>`

The root layout sets `lang="en"` as default. An inline script reads the locale from the URL pathname and updates `document.documentElement.lang` before first paint. This avoids reading `cookies()` in the root layout, which would force all pages to be dynamic.

## Rules to avoid unnecessary edge requests

### Never use `cookies()` or `headers()` in shared layouts

The root layout (`app/layout.tsx`) and the locale layout (`app/[lang]/layout.tsx`) must **never** call `cookies()`, `headers()`, or any function that reads request data. These layouts are shared by every page — a single dynamic call here forces the **entire app** into per-request rendering.

If you need request data, put it in a **route-group layout** that only wraps the pages that need it (e.g. `(app)/layout.tsx`).

### Keep `generateStaticParams` in `[lang]/layout.tsx`

This tells Next.js which locale variants to pre-render. Without it, `[lang]` is treated as a fully dynamic segment and every page requires an edge invocation.

### Keep the middleware matcher narrow

The middleware matcher explicitly excludes locale-prefixed paths. When adding a new locale, **add it to the matcher exclusion list** in `middleware.ts` — otherwise every request to that locale will unnecessarily invoke the middleware.

### Use route groups to separate static from dynamic

```
app/[lang]/
  (public)/    ← no cookies/headers in layout → static
  (auth)/      ← client component layout → static
  (app)/       ← reads headers() for auth → dynamic (intentional)
```

If you add a new section, choose the right route group. Don't put static content pages inside `(app)/`.

### Don't read cookies/headers for cosmetic data

For things like theme preference, locale, or UI state — handle client-side. Only use `cookies()`/`headers()` for data that genuinely requires server involvement (authentication, authorization, signed tokens).

### Check build output for static vs dynamic

After `pnpm build`, Next.js prints a summary showing which routes are static (`○`) and which are dynamic (`λ` or `ƒ`). Verify that public pages show as static. If a page unexpectedly shows as dynamic, check its component tree for `cookies()`, `headers()`, `searchParams`, or `fetch` with `cache: 'no-store'`.

### Never call useAuth() / useSession() on public pages

This is one of the most impactful rules for edge request cost.

Better Auth's `useSession()` hook fires a `GET /api/auth/get-session` request
on mount. This is both an **edge request** and a **serverless function
invocation** — the two billable Vercel metrics. It also re-fires on every
**window focus** event (rate-limited to 5 seconds), so tab-switching during a
single visit generates repeated requests.

**Current rule**: public pages always render the anonymous CTA state. Auth
checks only happen inside the `(app)` route group, where the user is already
authenticated and the session check is genuinely needed.

If a future feature needs auth-dependent UI on a public page (e.g. showing
a user avatar in the header), consider one of these alternatives instead of
re-adding `useSession()`:

- **Non-httpOnly cookie hint**: Set a lightweight `logged_in=1` cookie
  (non-httpOnly) at sign-in. Client JS can check `document.cookie` without
  a network request. Clear it on sign-out.
  Already implemented — see `src/lib/auth.ts` `after` hook and
  `src/lib/client-cookies.ts`. `AppBootstrapProvider` uses this to skip
  `fetchAppBootstrap()` entirely for anonymous users (issue #2246).
- **Deferred component**: Use `next/dynamic` with `ssr: false` to load the
  auth-dependent fragment only after the main page renders, so it doesn't
  block or slow initial paint.

## SessionProvider — zero-cost client auth

The `(app)/layout.tsx` already fetches the session server-side via
`getSession()`. Instead of having client components re-fetch it with
`authClient.useSession()` (which triggers `GET /api/auth/get-session`),
we pass the session through React context:

```
Server: (app)/layout.tsx → getSession() → <SessionProvider user={session.user}>
Client: useAuth() → reads from SessionProvider context (zero network requests)
```

**What this eliminates:**

- `GET /api/auth/get-session` on every app page mount
- Re-fetch on every window focus event (tab switching)
- localStorage caching workaround (no longer needed)

**Trade-off:** If the session changes externally (e.g. user changes name on
another tab), the data won't refresh until the next page navigation. This is
acceptable because account changes already redirect the user, causing a fresh
server render.

The `useAuth()` hook in `src/lib/useAuth.ts` is a thin wrapper over
`useSession()` from `SessionProvider.tsx`.

## Cached session deduplication

`src/lib/sessionCache.ts` wraps `auth.api.getSession()` with React's
`cache()`. This deduplicates session lookups within a single server render:

```
(app)/layout.tsx  → getSession()  → DB query #1
getPreferences()  → getSession()  → cache hit (no query)
getAccountPageData() → getSession() → cache hit (no query)
```

Without caching, each call independently queries the database for the same
session token. With `cache()`, only one query runs per request.

**Note:** `cache()` scopes to a single request. Separate HTTP requests
(e.g. client-triggered server actions) each get their own cache scope.

## Server-side data fetching for app pages

App pages fetch data in their server components and pass it as props to client
components. The data rides along with the RSC navigation payload — no extra
network request after mount.

**Key principle:** Avoid `useEffect` → server action fetch patterns on page
load. These create a sequential waterfall: the browser first downloads the RSC
payload (page shell), then mounts the component, then fires a second request
for data. Fetching in the server component collapses this into a single request.

### Account settings page

`getAccountPageData()` returns connected accounts in the page server component
and passes them as `initialData` to `<AccountSettings>`:

```
Before: RSC navigation (60ms) → mount → server action (280ms) = 2 requests, 340ms
After:  RSC navigation (~80ms, includes DB query)             = 1 request,  80ms
```

The server action uses the neon HTTP driver (stateless, fast) to query the
`account` table. It does NOT use `withRLS` — the `account` table has no RLS
policies, and the query filters by `userId` from the validated session.

Client-side re-fetching (`refreshAccounts`) is only used after user-initiated
mutations (e.g. setting a password), not on page load.

### General settings page

No server-side data fetching needed. Theme comes from `next-themes`
(`useTheme()`), locale comes from the URL. Both are available client-side
without a DB query.

### When adding new app pages

Follow this pattern: fetch data in the page server component, pass as props.
Only use client-side fetches for data that changes after user interaction
(e.g. refreshing account list after linking a social provider).

### Why no RLS on user_preferences

Row-Level Security was removed from `user_preferences`. While RLS provides
defense-in-depth, it required the WebSocket Pool driver (`@neondatabase/serverless`
Pool) for transactions (`set_config` + query), which added ~1-1.5s per write
on Vercel serverless due to cold WebSocket connections and 4 round-trips
(BEGIN → set_config → query → COMMIT).

All `user_preferences` queries already go through server actions that validate
the session and filter by the authenticated `userId`. The auth check in the
server action IS the security boundary. Use the stateless HTTP neon driver
(`db`) for all preference queries.

## Link prefetch strategy

Next.js `<Link>` prefetches the RSC payload for every linked route when the
link enters the viewport. Each prefetch of a **dynamic** route triggers a full
serverless function invocation with all DB queries from the layout — this is
both an edge request and a function invocation, billed twice.

**Policy: all links use `prefetch={false}`.** No exceptions.

### Why no "hot paths"

A previous version of this doc designated certain links as "hot paths" with
prefetch enabled (CTA buttons to `/explore`, `/sign-in`, `/sign-up`). This was
wrong — those pages are all **dynamic** (the `(app)` layout runs 4+ DB queries,
the `(auth)` layout checks session). Each prefetch triggered full SSR for
nothing. On the explore page, 10 company card links could prefetch 10 company
detail pages — 10 phantom SSR invocations per page view.

Even static pages (About, FAQ, etc.) count toward Vercel's edge request quota
when prefetched. The navigation speed benefit from prefetch is marginal (~100-
300ms) and doesn't justify the cost.

### Implementation

- **`Button` component** (`ui/Button.tsx`): Renders `<Link>` with
  `prefetch={false}` by default. Callers can override with `prefetch={true}`
  via props (spread after the default).
- **All `<Link>` elements** in `src/components/`: Explicitly set
  `prefetch={false}`.
- Navigation still works normally — Next.js fetches the route on click.

### When adding new links

- **Always** add `prefetch={false}` to `<Link>` elements.
- **Using `Button` with `href`?** Already handled — Button defaults to
  `prefetch={false}`.
- **Exception:** If you measure that a specific link targets a **static** page
  AND the conversion lift from prefetch justifies the edge request cost, you
  may pass `prefetch={true}` explicitly. Document the rationale in a comment.

## Font loading

Fonts are loaded via `@font-face` declarations in `globals.css` with
`font-display: swap`. The browser only downloads a font file when it
encounters text using that specific weight.

**Do not add `<link rel="preload">` for fonts.** Preload hints combined with
`@font-face` cause browsers to download each font **twice** — once from the
preload and once from the CSS. This was previously doubling font bandwidth
(~376 kB × 2 = ~752 kB wasted per page load).

Fonts are cached for 1 year with `immutable` via `Cache-Control` headers in
`next.config.ts`. After the first visit, fonts are served from browser cache.

## Asset caching

Static assets use `Cache-Control` headers (configured in `next.config.ts`) to
reduce repeat edge requests.

| Asset | Cache TTL | Rationale |
|-------|-----------|-----------|
| Fonts (`/fonts/*`) | 1 year, `immutable` | Never change between deploys |
| `next/image` responses (`/_next/image`) | 1 week (`minimumCacheTTL`) | Default is 60s — far too short |
| SVGs, PNGs in `public/` | 1 week | Rarely change; no content hash in URL |
| JS/CSS chunks (`/_next/static/*`) | 1 year, `immutable` | Content-hashed filenames (Next.js default) |
| Favicon, manifest, icons | 1 week | Rarely change |

Vercel purges its CDN cache on every deploy. Browser caches persist for the
configured TTL — this is acceptable because these assets rarely change.
If a `public/` asset does change, browsers will pick up the new version
within a week.

## ThemedImage — single-image strategy

`ThemedImage` renders a **single** `<Image>` tag for the active theme instead
of rendering both light and dark variants with CSS `display: none` toggling.

The previous dual-image approach caused browsers to download **both** images
for every themed image on every page (logos, screenshots, artwork), doubling
edge requests. On Vercel each request is a billed edge request.

The current client-component approach uses `useTheme()` to pick the right
source. It defaults to dark during SSR (matching `defaultTheme="dark"`) and
swaps after hydration if the user is in light mode. `next-themes` injects a
blocking script that resolves the theme before first paint, so there is no
visible flash.

---

## Fluid compute

Vercel's fluid compute model bills serverless function usage by **GB-seconds**
(allocated memory × wall-clock execution time). Every millisecond a function
spends waiting on DB queries, Redis lookups, or CPU work counts toward the
bill. Reducing function duration is as important as reducing the number of
invocations.

### What triggers a serverless function invocation

| Trigger | Runtime | Notes |
|---------|---------|-------|
| Dynamic page render (SSR) | Node.js | All `(app)` pages (`force-dynamic`) |
| Auth page render | Node.js | `(auth)` layout calls `getSession()` |
| Server action call | Node.js | Each client-triggered `.bind()` or `useActionState` |
| API route request | Node.js | `/api/v1/*`, `/api/auth/*`, `/api/stripe/*` |
| OG image generation | Node.js | `opengraph-image.tsx` routes |
| `sitemap.xml` / `robots.txt` | Node.js | Generated dynamically per request |
| Middleware | Edge | Lightweight locale redirect only |

Static pages (`(public)`) and cached CDN assets do **not** invoke functions.

### Database driver and connection model

`src/db/index.ts` uses **postgres.js** via Drizzle ORM with a lazy-init
singleton:

```
postgres(DATABASE_URL, { max: 10, idle_timeout: 20, max_lifetime: 300, prepare: false })
```

- **`max: 10`** — up to 10 connections per serverless instance. Each Vercel
  autoscale instance gets its own pool.
- **`idle_timeout: 20`** — connections close after 20s idle.
- **`max_lifetime: 300`** — connections recycled every 5 minutes.
- **`prepare: false`** — no prepared statements (required for connection
  poolers like PgBouncer/Supavisor).

**Cold start impact:** The first request to a new serverless instance must
establish a TCP+TLS connection to the database. This typically adds 50-150ms
(same-region) or 200-400ms (cross-region) to function duration. Subsequent
requests on the same instance reuse the pooled connection.

### Session resolution chain

Every `(app)` page calls `getSession()` from `sessionCache.ts`. The chain:

```
headers() → extract cookie token
  → Redis GET session:{token}       (cache hit: ~5ms, miss: continue)
  → auth.api.getSession(headers)    (DB query: ~20-80ms)
  → Redis SET session:{token}       (write-back: ~5ms)
```

- **Redis hit:** ~5ms (best case, most requests after first)
- **Redis miss + DB:** ~30-90ms (cold session or expired 5-min TTL)
- **Redis down + DB:** ~25-85ms (Redis errors are caught, falls through)

React's `cache()` deduplicates within a single render — the session is
fetched once regardless of how many server components call `getSession()`.

### The app layout tax

Every `(app)` page pays a fixed compute cost from the shared layout
(`app/[lang]/(app)/layout.tsx`). This runs **before** any page-specific
queries:

| Query | Condition | Approx duration |
|-------|-----------|-----------------|
| `getSession()` | Always | 5-90ms (Redis hit vs DB) |
| `getPreferences()` | If authenticated | 10-30ms (1 SELECT) |
| `getSavedJobStatuses()` | If authenticated | 10-30ms (1 SELECT) |
| `getStarredCompanyIds()` | If authenticated | 10-30ms (1 SELECT) |

The last three run in **`Promise.all()`** — they execute in parallel, so the
cost is the slowest of the three, not the sum.

**Authenticated layout cost:** ~15-120ms (session + max(prefs, saved, starred))
**Unauthenticated layout cost:** ~5-90ms (session only, other queries skipped)

This is the **floor** for every `(app)` route. Page-specific queries add on
top.

### Per-route compute profile

Estimated serverless function duration per route (SSR render, wall-clock).
Durations assume warm instance (no cold start). Cold start adds 50-400ms.

| Route | Layout queries | Page queries | Total DB queries | Pattern | Redis cache | Est. duration |
|-------|---------------|-------------|-----------------|---------|-------------|---------------|
| **Explore** | 4 (parallel) | 3-8 (search + filters) | 7-12 | Mixed | Search: 5min | 80-250ms |
| **Company** | 4 (parallel) | 3-5 (company + postings) | 7-9 | Mixed | Company: 5min | 60-200ms |
| **Shared watchlist** | 4 (parallel) | 6-10 (watchlist + filters + postings) | 10-14 | Sequential + parallel | None | 100-350ms |
| **My Jobs** | 4 (parallel) | 2 (count + list) | 6 | Sequential | None | 50-150ms |
| **My Jobs Stats** | 4 (parallel) | 2 (funnel + activity) | 6 | Sequential | None | 50-150ms |
| **Watchlists** | 4 (parallel) | 1 + 5N (list + per-watchlist counts) | 5 + 5N | N+1 problem | None | 60-300ms+ |
| **Settings** | 4 (parallel) | 3 (prefs + languages + currencies) | 7 | Sequential | Languages: 1h | 40-120ms |
| **Account** | 4 (parallel) | 1 (accounts) | 5 | Sequential | None | 40-100ms |
| **Billing** | 4 (parallel) | 1 (plan info) | 5 | Sequential | None | 40-100ms |
| **Progress** | 4 (parallel) | 2 (stats, parallel) | 6 | Parallel | Stats: 6h | 30-80ms |
| **Sign-in / Sign-up** | 1 (session) | 0 | 1 | — | Session: 5min | 10-90ms |

**N** = number of user's watchlists. A user with 10 watchlists triggers
~51 queries on the watchlists page.

### Server action compute

Each client-triggered server action is a separate function invocation.
These are the most common:

| Action | DB queries | Redis cache | Est. duration |
|--------|-----------|-------------|---------------|
| `searchJobs()` | 3-5 | 5min | 30-150ms |
| `listTopCompanies()` | 3-5 | 10min | 30-150ms |
| `getPostingDetail()` | 3 (sequential) | 5min | 20-100ms |
| `toggleSavedJob()` | 2 (sequential) | None | 15-50ms |
| `toggleStarredCompany()` | 2 (sequential) | None | 15-50ms |
| `getMyJobs()` | 2 (sequential) | None | 20-80ms |
| `getMyJobDetail()` | 2 (sequential) | None | 15-60ms |
| `updateJobStatus()` | 3 (sequential) | None | 20-80ms |
| `getWatchlistPostings()` | 4 (mixed) | None | 30-120ms |
| `updatePreferences()` | 2 (sequential) | None | 15-50ms |

### API route compute

External API routes run the same query logic as server actions but add
rate-limit checks (1 Redis call) and response serialization:

| Route | DB queries | Redis cache | Cache-Control | Est. duration |
|-------|-----------|-------------|---------------|---------------|
| `GET /api/v1/search` | 3-5 | 5min | `s-maxage=300` | 40-180ms |
| `GET /api/v1/job` | 3 | 5min | `s-maxage=300` | 30-120ms |
| `GET /api/v1/companies` | 1 | 10min | `max-age=600` | 15-60ms |
| `GET /api/v1/taxonomies` | 1 | 1h | `max-age=3600` | 15-50ms |
| `GET /api/v1/watchlists` | 2 + 2N | None | `max-age=300` | 40-200ms+ |
| `POST /api/auth/*` | 1-5 | Session: 5min | None | 20-150ms |
| `POST /api/stripe/webhook` | 1-2 | None | None | 15-60ms |
| `POST /api/admin/.../apify-import` | 100+ | None | None | 5-30s |

The admin import route explicitly sets `runtime = "nodejs"` and is the only
long-running function. All others complete in under 500ms.

### OG image compute

OG image routes (`opengraph-image.tsx`) use Satori + sharp to render PNGs.
Each invocation:
1. Reads font TTF + logo PNG from filesystem (~5ms)
2. Renders JSX to SVG via Satori (~20-50ms)
3. Encodes SVG to PNG via sharp (~30-80ms)

**Estimated duration:** 60-140ms per OG image. These are only requested by
social media crawlers when links are shared — low volume but relatively
CPU-heavy per invocation.

### Compute hotspots

Ranked by total GB-seconds impact (frequency × duration):

1. **Explore page SSR** — highest traffic + heaviest queries (search with
   multi-CTE SQL, location/occupation expansion, geolocation sorting). Each
   render: 7-12 DB queries, 80-250ms.

2. **Server action: searchJobs** — fires on every search input change and
   filter toggle. High frequency. 3-5 queries, 30-150ms each.

3. **Shared watchlist SSR** — heaviest single render (10-14 queries). Lower
   traffic than explore but 100-350ms per render with no Redis caching.

4. **Watchlists page SSR** — N+1 query pattern. For a user with N watchlists,
   runs 1 + 5N queries. 10 watchlists = ~51 queries, 200-300ms+.

5. **Company page SSR** — second-highest traffic dynamic page. 7-9 queries,
   60-200ms.

6. **Auth pages** — every sign-in/sign-up page view calls `getSession()` to
   check for redirect. Usually a Redis hit (~10ms), but frequency adds up.

### Rules to minimize fluid compute

#### Parallelize independent queries

The app layout already runs `getPreferences()`, `getSavedJobStatuses()`, and
`getStarredCompanyIds()` in `Promise.all()`. Apply the same pattern to page-
level queries. Never run independent DB calls sequentially.

#### Avoid N+1 query patterns

`getUserWatchlists()` fetches watchlists then resolves job counts per watchlist
in a loop. Each resolution triggers 4 parallel taxonomy lookups + 1 count
query. Batch these into a single SQL query that computes counts for all
watchlists at once.

#### Use Redis caching for expensive reads

Search results and posting details are already cached (5-min TTL). Extend this
to:
- `getUserWatchlists()` — user-keyed, invalidate on watchlist mutation
- `getWatchlistPostings()` — filter-keyed, short TTL
- `getStats()` (my-jobs-stats) — user-keyed, invalidate on status change

#### Keep function duration under control

- Set `maxDuration` in `vercel.json` or per-route to cap runaway functions.
  Default is 10s on Pro plans.
- The admin import route (`apify-import`) should use `maxDuration: 60` since
  it's a batch operation.
- Monitor p99 durations in Vercel's Functions tab — any route consistently
  above 500ms is worth investigating.

#### Minimize cold start overhead

- The `postgres.js` driver's first connection adds 50-150ms. Vercel keeps
  functions warm for ~5-15 minutes between requests. Low-traffic routes
  (settings, billing, stats) are more likely to cold-start.
- Keep function bundle sizes small — larger bundles take longer to initialize.
  The `output: "standalone"` config in `next.config.ts` helps by tree-shaking
  unused dependencies.
- `prepare: false` in the postgres config avoids prepared statement overhead
  on pooled connections but also means queries can't benefit from plan caching.

#### Where to look in the Vercel dashboard

| Panel | What to check |
|-------|---------------|
| **Functions** tab | Per-route invocation count, p50/p99 duration, cold start rate |
| **Usage** → Fluid Compute | Total GB-seconds, daily trend, breakdown by function |
| **Logs** → Runtime Logs | Individual invocation traces with duration + status |
| **Speed Insights** | Real-user TTFB (maps to SSR duration) per route |
| **Monitoring** → Alerts | Set alerts on p99 duration > threshold or GB-seconds budget |

The Functions tab is the single most useful view for compute optimization.
Sort by "Total Duration" (invocations × avg duration) to find the routes
consuming the most GB-seconds.
