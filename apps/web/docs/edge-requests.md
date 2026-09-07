# Edge Requests, Rendering Strategy & Fluid Compute

## How it works

The app splits work into **static shells** (served from CDN, zero function
compute) and **dynamic work** (server actions, API routes, selected dynamic
subtrees, and generated assets). See `data-fetching.md` for the full UI data
flow; this document focuses on the cost and rendering implications.

### Static pages and shells

Public marketing pages, most auth page shells, and the `(app)` layout shell are
pre-rendered for every locale via `generateStaticParams` in
`app/[lang]/layout.tsx`:

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
/en/explore       — cached shell with anonymous default data
/en/company/*     — cached shell with anonymous default data
/en/my-jobs       — static shell; data loads through a server action
```

The shell itself is intended to produce no page Function invocation when
served from cache. Company documents have an additional routing constraint
described below; do not call that surface zero-Function until a production
canary confirms both a page-cache `HIT` and a non-Function request source.
Personalized data is fetched later by client components through server actions,
or by page-specific dynamic subtrees wrapped in `<Suspense>`.

### Dynamic work

Dynamic work now lives at narrower boundaries:

```
server actions    — UI data fetches, mutations, session-aware reads
/api/auth/*       — Better Auth handler
/api/v1/*         — external API routes
/api/stripe/*     — Stripe webhook
dynamic subtrees   — e.g. settings page data under Suspense
generated assets   — OG images, sitemap, robots
```

Keep the shared layouts static; move request-specific reads into one of these
narrow dynamic surfaces.

### Proxy (formerly Middleware)

The proxy normally runs for paths **without** a locale prefix (e.g. bare `/`,
`/how-we-index`) and redirects to the locale-prefixed version based on
`Accept-Language`. A narrow set of localized boundaries also opt in: company
request auth, Explore document normalization, scanner rejection, unknown
company candidates, public-watchlist document status checks, and Server
Action abuse controls for the public browse surfaces.

The company matcher is generated from `apps/crawler/data/companies.csv`.
Registered canonical slugs do not match Proxy at all, so a warm company page
can reach Vercel's page cache without first consuming a middleware invocation.
Only slug candidates absent from that deployment's registry enter the bounded
Typesense status check. That includes newly added companies until the next
genuine web release, so company data can become visible immediately without
replacing the current deployment. `pnpm dev` and `pnpm build` refresh the
generated block; `pnpm proxy-matchers:check` is available as a read-only
freshness gate. The production deploy classifier deliberately does not treat
the company registry as a web input: replacing the Next.js build for every
company addition changes the build-keyed Cache Components namespace and
cold-starts the entire page cache. The trusted production dispatcher instead
waits for the exact revision's OG prewarm, then publishes its Typesense data and
invalidates the company CSV cache tag. The registry remains an explicit input
to the `@jobseek/web#build` Turbo task so every unrelated, genuine web build
regenerates the fast bypass matcher from the current registry rather than
restoring an older `.next` artifact.

The same generator excludes every reserved username prefix from the generic
two-segment watchlist matcher. This lets explicit multi-segment application
routes such as `/en/my-jobs/stats` and `/en/settings/billing` bypass Proxy
instead of being mistaken for public watchlist documents. The exclusions come
from `src/lib/username.ts`, which remains the single source of truth for route
and account-name collisions.

The unknown-company and watchlist checks remain necessary because Cache
Components can begin streaming the static app shell before a page-level
`notFound()` resolves. Once headers have been sent, Next.js can only return a
noindex soft 404 with HTTP 200. For full GET/HEAD candidates, `proxy.ts`
therefore performs a bounded 60-second-cached existence check and returns a
small localized recovery document with HTTP 404.
The document is constructed only from fixed translations and URL-encoded path
data; it never fetches, parses, or filters App Router HTML. Recovery controls
are ordinary links and its CSP denies all script execution, preventing a PPR
resume against the original missing URL and a duplicate app shell. RSC
navigation and Server Action traffic bypass the existence check. Protected
public-read Server Actions still pass through the shared request rate limit
described in `public-read-abuse.md`. Upstream failures fail open to the
existing noindex page fallback; an outage must never turn every resource into
a false 404.

Anonymous watchlist checks query only `is_public = true`, so absent and private
resources are indistinguishable. Requests carrying a session cookie are
verified through Better Auth. A valid session gets an uncached owner-or-public
existence check; stale, revoked, and forged cookies stay on the anonymous
public-only path. This preserves private-owner access without disclosing
private resources. Watchlist mutations and username renames evict the route-
status cache alongside the existing detail caches.

> Renamed from `middleware.ts` to `proxy.ts` for Next.js 16 (#2887).
> Same APIs, same execution model — see https://nextjs.org/docs/messages/middleware-to-proxy.

### Theme

Theme is handled entirely client-side by `next-themes`. It injects an inline script that sets the `class` attribute on `<html>` before first paint. No server-side cookie reading needed.

### Locale on `<html lang>`

The root layout sets `lang="en"` as default. An inline script reads the locale from the URL pathname and updates `document.documentElement.lang` before first paint. This avoids reading `cookies()` in the root layout, which would force all pages to be dynamic.

## Rules to avoid unnecessary edge requests

### Never use `cookies()` or `headers()` in shared layouts

The root layout (`app/layout.tsx`) and the locale layout (`app/[lang]/layout.tsx`) must **never** call `cookies()`, `headers()`, or any function that reads request data. These layouts are shared by every page — a single dynamic call here forces the **entire app** into per-request rendering.

If you need request data, put it in the smallest page, server action, API
route, or Suspense-wrapped subtree that needs it. Do not add request reads to
`(app)/layout.tsx`; it is intentionally a static shell.

### Keep `generateStaticParams` in `[lang]/layout.tsx`

This tells Next.js which locale variants to pre-render. Without it, `[lang]` is treated as a fully dynamic segment and every page requires an edge invocation.

### Keep the proxy matcher narrow

The broad matcher excludes locale-prefixed paths; each localized exception is
listed separately in `proxy.ts`. When adding a locale, update both the broad
exclusion and every localized exception. Protected Server Action matchers must
require a non-empty `Next-Action` header so ordinary cached documents continue
to bypass Proxy. After changing the company registry,
run `pnpm proxy-matchers:update`; dev, test, CI, and production builds refresh
the block as well. Do not broaden the resource-status boundary beyond full
unknown-company or watchlist documents. RSC requests stay on the normal App
Router path; only the documented public-read Server Action surfaces opt into
the rate-limit boundary.

### Use route groups to separate static from dynamic

```
app/[lang]/
  (public)/    ← no cookies/headers in layout → static
  (auth)/      ← static shell; redirect check streams in a child
  (app)/       ← static shell; data loads in actions/page subtrees
```

If you add a new section, choose the route group by navigation chrome and data
needs. Static content pages should stay under `(public)`.

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
checks belong in the `(app)` route group, auth redirects, server actions, or
API routes where the session is genuinely needed.

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

## App bootstrap and client auth

`(app)/layout.tsx` renders `AppBootstrapProvider`, but does not fetch the
session on the server. The provider runs on the client:

```
Anonymous: no logged_in hint cookie → use anonymous context, no RPC
Signed in: AppBootstrapProvider → fetchAppBootstrap() server action
  → getSession()
  → one combined bootstrap query for preferences, saved jobs, starred companies
  → SessionProvider / SavedJobsProvider / StarredCompaniesProvider
```

This keeps the shared shell static while still avoiding repeated
`GET /api/auth/get-session` calls from UI components. `useSession()` reads
`SessionProvider` context; identity mutation flows call the provider's
`refresh()` method to reload the bootstrap payload in place.

## Cached session deduplication

`src/lib/sessionCache.ts` wraps `auth.api.getSession()` with React's `cache()`.
This deduplicates session lookups within a single server action or dynamic
server subtree:

```
fetchAppBootstrap() → getSession() → Redis/DB lookup
getPreferences()    → getSession() → cache hit in same request
server action       → getSession() → separate request, separate cache scope
```

Without caching, each call independently queries the database for the same
session token. With `cache()`, only one query runs per request.

**Note:** `cache()` scopes to a single request. Separate HTTP requests
(e.g. client-triggered server actions) each get their own cache scope.

## Data fetching for app pages

`data-fetching.md` is the canonical guide for the current pattern. In short:
the `(app)` route group serves static shells from CDN, and most page data loads
through client-fired server actions on mount. A few high-traffic anonymous paths
embed cacheable defaults in the shell:

- `/explore` embeds anonymous, no-filter defaults via `fetchExplorePageDefaults()`.
- `/company/[slug]` embeds anonymous, no-filter defaults via
  `fetchCompanyPageDefaults()`.
- Watchlist and company metadata use cached server reads for SEO, then hydrate
  personalized page bodies client-side.
- Settings keeps a page-specific dynamic subtree under `<Suspense>` because it
  benefits from server-side parallel reads.

### Account settings page

`account-loader.tsx` calls `getAccountPageData()` from the client after mount.
The server action uses the shared Drizzle/postgres.js `db` client to query the
`account` table. It does NOT use `withRLS` — the `account` table has no RLS
policies, and the query filters by `userId` from the validated session.

Client-side re-fetching (`refreshAccounts`) is only used after user-initiated
mutations (e.g. setting a password), not on page load.

### General settings page

`settings-loader.tsx` is a deliberate page-specific dynamic subtree. It gates
on `getSession()`, then fetches preferences, viewer job languages, available
job languages, and currency rates in parallel. This avoids the old sequential
client waterfall without making the shared `(app)` layout dynamic.

### When adding new app pages

Default to the static shell + client server-action loader pattern described in
`data-fetching.md`. Use a server component/dynamic subtree only when it has a
measured benefit and can stay isolated from shared layouts.

### Why no RLS on user_preferences

Row-Level Security was removed from `user_preferences`. All
`user_preferences` queries go through server actions that validate the session
and filter by the authenticated `userId`. The auth check in the server action
is the security boundary. Use the shared Drizzle/postgres.js `db` client for
preference queries.

## Link prefetch strategy

Next.js `<Link>` prefetches route data when the link enters the viewport. For
dynamic routes this can trigger a serverless function invocation; for cached
or static routes it still creates extra CDN/edge traffic. The cost is easy to
multiply across card grids and navigation bars.

**Policy: all links use `prefetch={false}`.** No exceptions.

### Why no "hot paths"

A previous version of this doc designated certain links as "hot paths" with
prefetch enabled (CTA buttons to `/explore`, `/sign-in`, `/sign-up`). This was
wrong for cost control: prefetching spends requests before the user intent is
known. On list pages, many visible cards can prefetch many detail routes that
the user never opens.

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
| Remote URL through `next/image` | Upstream TTL or 1-year configured floor, whichever is larger | `images.minimumCacheTTL`; live client header was 1 year |
| String local/public URL through `next/image` | Vercel edge cache up to 31 days; live client header was 1 week | Local content-hash cache key; no blanket 1-year promise |
| Static image import | Long-lived `immutable` client header | Next content-hashes the imported asset; edge residency remains platform-managed |
| SVGs, PNGs in `public/` | 1 week | Rarely change; no content hash in URL |
| JS/CSS chunks (`/_next/static/*`) | 1 year, `immutable` | Content-hashed filenames (Next.js default) |
| Favicon, manifest, icons | 1 week | Rarely change |

### Optimized-image cache contract

There are three distinct paths:

- For a **remote URL source**, Vercel documents the optimized-image cache TTL
  as the larger of the upstream image's `Cache-Control` lifetime and
  `images.minimumCacheTTL`. This app configures that supported floor as
  `31536000` seconds.
- For a **string local/public URL**, Vercel uses a content hash in the local
  image cache key and documents edge caching for up to 31 days. "Up to" is a
  ceiling, not a residency guarantee. The downstream/client response can have
  a different lifetime, as the live seven-day response below demonstrates.
- A **static image import** is content-hashed by Next and receives a long-lived
  `immutable` client cache header. That header does not guarantee how long a
  particular Vercel edge retains the object.

A `headers()` entry for `/_next/image` is not a supported way to change any of
these generated responses: Next warns during builds and ignores the override.
Keep optimizer policy under `images`, not generated-route headers.

The client-visible header is not a single app-owned constant. Live checks on
2026-08-11 returned:

- a remote company logo: `public, max-age=31536000, must-revalidate`;
- a local `/publicdomain/*` source: `public, max-age=604800`.

Those observations verify the current Next/Vercel behavior; they do not turn a
response header or edge residency into an app-owned contract. In particular,
`minimumCacheTTL` should not be tested by copying an expected `Cache-Control`
string into app config.
See the official [Next.js image configuration](https://nextjs.org/docs/app/api-reference/components/image#minimumcachettl)
and [Vercel Image Optimization](https://vercel.com/docs/image-optimization)
references for the supported controls.

Hashing or versioning prevents a changed asset from colliding with a stale
cache entry; it does not set a TTL. If a directly served asset requires a
one-year **browser** lifetime, use a content-hashed/versioned URL and set an
explicit, supported `Cache-Control` header on that direct source route, then
verify the deployed response. A static import is the Next-managed alternative
when its long-lived `immutable` client header is appropriate. Neither approach
promises edge residency; eviction remains platform-managed. Do not add a
custom `/_next/image` header.

Deploying alone does not invalidate Vercel's optimized-image cache. Change the
source URL or use the platform's image-cache purge mechanism when immediate
invalidation is required.

Browser caches for the other public assets persist for their configured TTL.
If an unversioned `public/` asset changes, browsers will pick up the new version
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
| Dynamic server subtree | Node.js | Page-specific Suspense islands such as settings data |
| Server action call | Node.js | Each client-triggered `.bind()` or `useActionState` |
| API route request | Node.js | `/api/v1/*`, `/api/auth/*`, `/api/stripe/*` |
| Dynamic OG image generation | Node.js | Isolated `/og/*` blog, methodology, and public-watchlist routes |
| `sitemap.xml` / `robots.txt` | Node.js | Generated dynamically per request |
| Proxy (formerly Middleware) | Edge | Lightweight locale redirect only |

Static pages and CDN assets do not invoke Functions. Cached company shells are
expected to avoid both page and middleware compute after this matcher split,
but that claim remains provisional until the production canary verifies the
request source and visible CPU.

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

Any server action or dynamic server subtree that needs auth calls
`getSession()` from `sessionCache.ts`. The chain:

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

### Removed app layout tax

The old `(app)` layout fetched session, preferences, saved job statuses, and
starred companies during every server render. That is no longer true.
`app/[lang]/(app)/layout.tsx` is a cached shell that resolves the
viewer-independent currency-rate table through an hourly `use cache` service
read, then renders `AppBootstrapProvider`. The table is serialized into the
shell; `SalaryDisplayProvider` does not call a Server Action on mount.
If the database is unavailable while a shell is generated, the service embeds
the complete approximate seed from the currency-rate migration rather than an
EUR-only table; the uncached fallback preserves selectors and safe non-EUR
conversion until a later server read recovers current ECB-backed rates.

Current bootstrap behavior:

| Viewer | Work on initial shell | Follow-up function work |
|--------|-----------------------|--------------------------|
| Anonymous, no `logged_in` hint | Cached currency-rate read | None from bootstrap or salary display |
| Signed in | Cached currency-rate read | `fetchAppBootstrap()` server action: `getSession()` + one combined bootstrap query |

There is no universal per-route layout query floor. Page-specific loaders,
server actions, and dynamic subtrees define the compute cost.

### Per-route compute profile

Estimated serverless function duration for the dynamic part of each route.
Durations assume a warm instance. Cold start adds 50-400ms when a function is
invoked.

| Route | Shell behavior | Dynamic work | Pattern | Redis/cache | Est. duration |
|-------|----------------|--------------|---------|-------------|---------------|
| **Explore** | One-day cached anonymous defaults | Browser-direct filtered/preference refresh; shared bootstrap for signed-in prefs; narrow parser action only for semantic `q` | Browser-first | Shell 1d; search 5min | 0ms result compute on direct mounts |
| **Company** | Cached anonymous defaults + metadata | Personalized `fetchCompanyPageData()` when signed in, filtered, or language cookie present | Mixed | Company/detail caches | 30-200ms when invoked |
| **Shared watchlist** | Cached anonymous public snapshot + metadata | Personalized `fetchWatchlistPageData()` only when viewer/filters require it | Mixed | Watchlist lookup cached | 60-300ms when invoked |
| **My Jobs** | Static shell | `getMyJobs()` after mount | Sequential | None | 20-100ms |
| **My Jobs Stats** | Static shell | stats loader action after mount | Parallel | Stats cache | 30-100ms |
| **Watchlists** | Static shell | bootstrap context + `getUserWatchlistsWithLimit()` after mount | Constant in N | None | 30-120ms |
| **Settings** | Static shell + dynamic Suspense subtree | `getSession()` gate, then prefs/languages/currencies in parallel | Parallel | Languages/currencies cached | 40-150ms |
| **Account** | Static shell | `getAccountPageData()` after mount | Sequential | None | 40-120ms |
| **Billing** | Static shell | billing loader action after mount | Sequential | None | 40-120ms |
| **Progress** | Static shell | progress loader action after mount | Parallel | Stats cache | 30-100ms |
| **Sign-in / Sign-up** | Static form shell | Suspended redirect check if already signed in | Session check | Session cache | 10-90ms |

Watchlist counts on the listing page are now denormalized via a single
JOIN subquery (issue #3176). The watchlist detail page still runs the
filter-precise count via `getWatchlistPostingDisplayCounts()`.

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

External API routes run the same query logic as server actions. A successful
cache miss adds one origin Upstash rate-limit check and response serialization;
successful cache hits skip the Function and origin Redis entirely but remain
inside the pre-cache Vercel WAF budget:

| Route | Origin work on CDN miss | Vercel CDN TTL | Caller Cache-Control | Est. duration |
|-------|-------------------------|----------------|----------------------|---------------|
| `GET /api/v1/search` | Upstash + Typesense | 5min | revalidate | 40-180ms |
| `GET /api/v1/job` | Upstash + Supabase/Typesense | 5min | revalidate | 30-120ms |
| `GET /api/v1/companies` | Upstash + Typesense | 1h | revalidate | 15-60ms |
| `GET /api/v1/resolve` | Upstash + Typesense | 1h | revalidate | 15-60ms |
| `GET /api/v1/taxonomies` | Upstash + Typesense | 1h | revalidate | 15-50ms |
| `GET /api/v1/watchlists` | Uniform retired-contract `410` + bounded aggregate telemetry | None | `no-store` | <10ms handler |
| `GET /api/v1/watchlist/create` | Upstash + Typesense | 5min | revalidate | 40-180ms |
| `POST /api/auth/*` | 1-5 | Session: 5min | None | 20-150ms |
| `POST /api/stripe/webhook` | 1-2 | None | None | 15-60ms |

The listed routes typically complete in under 500ms. Successful public GETs
do not expose `X-RateLimit-*`, because those values are caller-specific and
would be unsafe in a shared cache. Non-cacheable origin errors and 429s retain
them.

### OG image compute

The site-wide and company OG cards are pre-rendered in GitHub Actions and served
directly from R2/Cloudflare, so they consume no Fluid CPU. The lower-volume
blog, methodology, and privacy-sensitive public-watchlist cards remain dynamic
under isolated `/og/*` route handlers. Their Satori/Sharp payload is not
included in ordinary page Function traces.

Each dynamic OG invocation:
1. Reads font TTF + logo PNG from filesystem (~5ms)
2. Renders JSX to SVG via Satori (~20-50ms)
3. Encodes SVG to PNG via sharp (~30-80ms)

**Estimated duration:** 60-140ms per dynamic OG image. These are primarily
requested by social media crawlers when links are shared — low volume but
relatively CPU-heavy per invocation.

### Compute hotspots

Ranked by total GB-seconds impact (frequency × duration):

1. **Search server actions** — `searchJobs()` and related explore filtering
   paths are high frequency and run the heaviest search queries.

2. **Personalized explore/company hydration** — signed-in viewers, filters, or
   language cookies bypass the cached anonymous defaults and call server
   actions.

3. **Shared watchlist hydration** — the public shell is cacheable, but the page
   body still fetches personalized watchlist data after mount.

4. **Settings dynamic subtree** — deliberate request-time work with several
   parallel reads. Lower traffic but useful to watch because it mixes session,
   Typesense, and Postgres reads.

5. **Mutating server actions** — saved jobs, watchlist edits, preferences, and
   billing operations. Lower frequency but more sensitive to retries and
   invalidation work.

6. **Auth redirect checks** — sign-in/sign-up shells are static, but the
   suspended redirect check still resolves session state.

### Rules to minimize fluid compute

#### Parallelize independent queries

Use `Promise.all()` for independent page-level reads. The bootstrap action goes
one step further and combines preferences, saved job statuses, and starred
companies into one SQL round-trip; prefer that shape when the data is always
needed together.

#### Avoid N+1 query patterns

Past offender — fixed in #3176: `getUserWatchlists()` used to loop over
each watchlist and run a filter-applied Typesense count. The current
shape returns the denormalized active count from a single SQL JOIN
subquery. The same lesson applies elsewhere — never fan a per-row count
query out of a list endpoint. Push it into a single SQL aggregation, or
use Typesense `multi_search` if filter precision is required.

#### Use Redis caching for expensive reads

Search results and posting details are already cached (5-min TTL). Extend this
to:
- `getWatchlistPostings()` — filter-keyed, short TTL
- `getMyJobsStats()` (my-jobs-stats) — user-keyed, invalidate on status change

`getUserWatchlists()` is fast enough post-#3176 (single SQL query, ~12-38ms)
that caching adds invalidation complexity without a meaningful win.

#### Keep function duration under control

- Set `maxDuration` in `vercel.json` or per-route to cap runaway functions.
  Default is 10s on Pro plans.
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
