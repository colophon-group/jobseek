# Cache Components conventions

This document defines the rules and recipes for writing server components in `apps/web/` once `cacheComponents: true` is enabled in `next.config.ts` (planned in #2835). Read this **before** writing or modifying any server component, layout, or server action.

The Next.js upstream guide is at <https://nextjs.org/docs/app/getting-started/cache-components> — this doc layers in jseek-specific patterns and pitfalls. When the two diverge, this doc wins for our codebase.

> **Status:** the migration to `cacheComponents` is tracked in #2835. Until that lands, the project still operates under the legacy "static-by-default + `revalidate` opt-in + `headers()`/`cookies()` taint everything" model. The conventions below describe the *post-migration* state — write new code to them so when the flag flips, nothing has to change.

## The three content types

Every piece of rendered output in a route belongs to exactly one bucket. The build fails when something violates the contract.

### 1. Static (auto-prerendered)

Synchronous code. Imports. Pure computations. No async data access.

Output: prerendered at build time, served from the CDN.

```tsx
export default function Page() {
  return (
    <header>
      <h1>Explore</h1>
      <nav>...</nav>
    </header>
  )
}
```

### 2. Cached (`'use cache'`)

Async data that doesn't have to be fresh on every request. Adds the `'use cache'` directive at file, component, or function level. Use `cacheLife()` to control freshness and `cacheTag()` to enable targeted invalidation.

```tsx
async function CompanyPage({ slug }: { slug: string }) {
  'use cache'
  cacheLife('hours')
  cacheTag(`company:${slug}`)

  const company = await getCompanyBySlug(slug)
  return <CompanyHead company={company} />
}
```

### 3. Dynamic (Suspense-wrapped)

Anything that reads runtime state — `cookies()`, `headers()`, `searchParams`, `connection()`, `draftMode()` — or fetches data that has to be fresh per request. Must be inside a `<Suspense>` boundary at render time. Build fails otherwise.

```tsx
import { Suspense } from 'react'

export default function Page() {
  return (
    <>
      <CompanyShell />
      <Suspense fallback={<NavSkeleton />}>
        <AuthAwareNav />
      </Suspense>
    </>
  )
}

async function AuthAwareNav() {
  const session = await getSessionUserId()
  return <Nav loggedIn={!!session} />
}
```

## Decision tree for new server components

When you're writing a new server component (or porting an existing one), walk this in order and stop at the first match.

1. **Does it read `cookies()`, `headers()`, `searchParams`, `connection()`, `draftMode()`, or call any helper that internally does?**
   See `app/__tests__/isr-routes.test.ts::TAINTED_HELPERS` for the canonical list (`getSession`, `getSessionUserId`, `getViewerLanguages`, `getGeoFromHeaders`, `getPreferences`, `fetchExploreData`, `listTopCompanies` — extend that list when adding a new tainted helper).
   - **Yes** → it's dynamic. Wrap the component (or its parent) in `<Suspense>`. Provide a meaningful fallback (we have `SearchSkeleton`, `WatchlistSkeleton`, `CompanySkeleton` already; reuse before inventing).
   - **No** → continue.

2. **Does it fetch data that's the same for every viewer at this URL?**
   "Same data per (URL, args)" means deterministic given the function arguments — e.g., `getCompanyBySlug(slug, locale)`.
   - **Yes** → mark with `'use cache'` and set `cacheLife({...})` to a sensible window. Add a `cacheTag()` so the data can be selectively invalidated by a server action (e.g., `cacheTag(\`company:${slug}\`)`).
   - **No** → continue.

3. **Does it vary per viewer in a way the cache key can capture?**
   Example: locale-scoped data — same per `(slug, locale)` pair. Read the per-viewer dimension *outside* the cached function (the runtime API), pass it in as an argument. The argument becomes part of the cache key automatically.
   - **Yes** → cached function takes the per-viewer dimension as an argument. The caller does the `cookies()`/`headers()` read and passes the relevant slice through.

4. **Pure synchronous code?** It's static. Renders at build time.
   - **Don't** use `Math.random()`, `Date.now()`, `crypto.randomUUID()` here — they freeze at build. If you need request-time non-determinism, move to a dynamic subtree and call `await connection()` first.

## Recipes

### Per-locale `<html lang>` (the #2826 use case)

Root layout reads `headers()` to find the locale stamped by the middleware (`x-jseek-locale`). The static parts of the root layout are still cached; only the `<html lang>` attribute reads from the dynamic shell.

```tsx
// app/layout.tsx
export default async function RootLayout({ children }) {
  const h = await headers();
  const locale = isLocale(h.get('x-jseek-locale') ?? '') ? h.get('x-jseek-locale') : defaultLocale;
  return (
    <html lang={locale} suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}
```

The middleware-set header is per-request, so this layout is dynamic. Child page `revalidate=N` (or `'use cache'`) is unaffected — pages are cached independently.

### Cached DB lookup with viewer-language scoping

```ts
async function getCompanyBySlug(slug: string, locale: Locale) {
  'use cache'
  cacheLife('hours')
  cacheTag(`company:${slug}`)
  // (slug, locale) is the cache key — no manual key construction.
  return db.query(...);
}
```

Calling site does the locale resolution (from URL params or middleware-set header) and passes it in. Don't read `headers()` inside `'use cache'` — the build will error.

### Auth-aware nav chip on a static page

```tsx
export default function ExplorePage() {
  return (
    <>
      <ExploreShell />
      <Suspense fallback={<NavChipSkeleton />}>
        <AuthChip />
      </Suspense>
    </>
  );
}

async function AuthChip() {
  const userId = await getSessionUserId();
  return userId ? <Profile userId={userId} /> : <SignInLink />;
}
```

`<ExploreShell>` stays cached. `<AuthChip>` streams in dynamically.

### Mutation that invalidates cached data

```ts
'use server'

import { revalidateTag, updateTag } from 'next/cache';

export async function updateCompanyDescription(slug: string, body: string) {
  await db.companyDescription.update({ where: { slug }, data: { body } });
  // Background invalidation: next request after this one sees fresh data.
  revalidateTag(`company:${slug}`);
}
```

Use `updateTag()` instead when the *current* request must see fresh data (e.g., a redirect-after-mutation that needs the new state).

### Custom Redis `cached()` helper (`src/lib/cache.ts`)

We keep our own Redis-backed `cached(key, fetcher, { ttl, skipIf })` helper for cross-instance shared state. It coexists with `'use cache'`:

| Property | `'use cache'` (Next.js) | `cached()` (jseek Redis) |
|---|---|---|
| Storage | Per-region runtime cache (default) or `'use cache: remote'` | Redis (Upstash), shared across regions and instances |
| Cache key | Auto-derived from function args + closures | Manual string key |
| Empty-result handling | Stored | `skipIf` predicate can skip caching empty results (#2245) |
| Invalidation | `revalidateTag()` / `updateTag()` by tag | Manual `redis.del(key)` or expiry |
| When to use | Per-render dedup; viewer-scoped variants; dependency on Next.js render lifecycle | Cross-instance shared (sitemap rows, taxonomy resolutions) where the empty-skip + manual key are load-bearing |

New code should default to `'use cache'`. Reach for `cached()` when you need cross-instance sharing AND `skipIf`-style empty-result handling that the Next directive doesn't replicate.

## What will break (and how to fix it)

The build itself enforces most of this. Common errors and the one-line fix:

| Build error | Cause | Fix |
|---|---|---|
| `Dynamic API used outside Suspense` | `await headers()` / `await cookies()` / `await searchParams` in a server component without a `<Suspense>` ancestor | Wrap the component in `<Suspense>`, OR move the read into a `'use cache'` function with the value passed as an argument, OR push the read into a client subtree |
| `Closure references runtime API` | A `'use cache'` function captures `cookies()` / `headers()` from its enclosing scope | Extract the value before the cache boundary, pass as an argument: `getX(slug, viewerId)` instead of letting the function read the cookie itself |
| `Math.random() in cache` | Non-deterministic value inside a `'use cache'` block | If you need request-time randomness, move out of cache. Use `await connection()` first to defer to request time |
| Cache key collision | Two callers with different intended results sharing the same args | Add a discriminator argument; `'use cache'` derives the key from args + closures, so explicit args beat magic |
| Layout becomes dynamic, child pages stop revalidating | Layout reads runtime API outside Suspense | This is correct under cacheComponents — child pages with their own caching are unaffected. If a child page is unexpectedly dynamic, check its own `'use cache'` / Suspense placement, not the layout's |

## How to test

1. **Build is the contract.** `pnpm --dir apps/web build` fails on any violation. CI must run it.
2. **Per-route inspection.** Read the build output's per-route classification — Next 16 prints the static / cached / dynamic / mixed marker for each route. A route flagged dynamic that you expected to be cached or mostly-static is the signal.
3. **Production observability.** Vercel function invocation count for a "should-be-cached" route — if invocations spike post-deploy, dynamic leakage occurred.
4. **`apps/web/app/__tests__/isr-routes.test.ts`** (legacy guard) — under cacheComponents this becomes obsolete in its current form (the build enforces what the static scan was checking). Migration plan: rewrite to assert each listed route still has a meaningful static shell (parse the build output, verify the route is not 100% dynamic), or retire the test entirely. See #2835 acceptance.

## Anti-patterns

- ❌ `await headers()` directly in `app/layout.tsx` without a `<Suspense>` boundary downstream of the read. The dynamic API access taints the layout's output; if you need it (e.g., for `<html lang>`), keep the read narrow and let everything else stay static.
- ❌ `await searchParams` in a page that should be cacheable. Move the read into a client subtree via `useSearchParams()`.
- ❌ Wrapping an entire async page body in `'use cache'` without thinking about which subtrees are actually deterministic. The cache key includes everything, including arguments and closures — over-broad caching means rare hit rates.
- ❌ Using `'use cache: private'` to bypass the runtime-API restriction. It works but defeats the point. Only acceptable for compliance edge cases (e.g., hard-mandated GDPR per-user data path).
- ❌ Adding `dynamic = 'force-static'` or `dynamic = 'force-dynamic'` route-segment exports. These bypass the new model. The migration table maps the old flags to directive equivalents.
- ❌ Putting `Math.random()`, `Date.now()`, `crypto.randomUUID()` inside `'use cache'` and expecting different values per render. They freeze at build time inside the cache boundary.
- ❌ Reading session state (via `getSessionUserId`, `getViewerLanguages`, etc.) inside a route's render path **without Suspense**. Tainted helpers are now expected — but they have to be in dynamic subtrees, not in the static shell.

## Migration cheat sheet

When moving an existing component or route from the legacy model to cacheComponents:

| Legacy pattern | Cache Components replacement |
|---|---|
| `export const revalidate = 3600` on a page | Either keep (still works for the *page* slot) OR move data fetches into `'use cache'` functions with `cacheLife({ revalidate: 3600 })` on each |
| `unstable_cache(fn, key, { revalidate, tags })` | Convert to `'use cache'` + `cacheTag()` + `cacheLife({ revalidate })`. Drop the manual `key` array (auto-derived) |
| `dynamic = 'force-static'` | `'use cache'` + `cacheLife('max')` (for genuinely never-changing routes) |
| `dynamic = 'force-dynamic'` | Remove the directive (default behavior under cacheComponents); ensure the route uses runtime APIs inside Suspense as needed |
| `cookies()` / `headers()` in a server component | Move into a `<Suspense>`-wrapped subtree, OR extract the value to a client read, OR pass as an argument into a `'use cache'` function |

## References

- Cache Components official guide: <https://nextjs.org/docs/app/getting-started/cache-components>
- `'use cache'` directive: <https://nextjs.org/docs/app/api-reference/directives/use-cache>
- `unstable_cache` legacy reference (for migrating existing code): <https://nextjs.org/docs/app/api-reference/functions/unstable_cache>
- jseek tracking issues: #2835 (migration), #2826 (the `<html lang>` use case that's the first concrete consumer of this model)
- jseek incident referenced throughout: #2243 (the original ISR-leakage CPU-quota incident — the reason the legacy guard at `app/__tests__/isr-routes.test.ts` exists; also the pattern this doc replaces with build-time enforcement)
