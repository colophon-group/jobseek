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

Three flavors of the directive — pick deliberately:

| Directive | Storage | Runtime APIs allowed? | Use when |
|---|---|---|---|
| `'use cache'` | Per-region runtime cache (default) | No | Default. Same data per (URL, args), no per-viewer reads inside |
| `'use cache: remote'` | Platform-provided shared cache (Redis/KV on Vercel) | No | Cross-region or cross-instance shared state — sitemap rows, taxonomy resolutions, anything where every replica should hit the same entry |
| `'use cache: private'` | Per-request memoization (does NOT persist) | **Yes** — `cookies()`/`headers()` allowed inside | Compliance escape hatch: GDPR-mandated per-user data, or any case where you can't refactor to extract-then-pass. The "private" name reflects that the result is keyed per-viewer and never shared |

Default to plain `'use cache'`. Reach for `: remote` only when shared state across instances is the load-bearing requirement; reach for `: private` only when the runtime-API restriction is genuinely blocking.

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
   The canonical "tainted helpers" — server functions that internally read request state — are: `getSession`, `getSessionUserId`, `getViewerLanguages`, `getGeoFromHeaders`, `getPreferences`, `fetchExploreData`, `listTopCompanies`. Extend the list (and grep callers in PRs) whenever you add another helper that reads `headers()` / `cookies()` / `getSession()` under the hood.
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

## How cache keys are derived

A `'use cache'` function's cache key is automatically computed from:

1. **Build ID** — invalidates on every deploy.
2. **Function identity** — a hash of the function's location, so two distinct `'use cache'` functions never collide.
3. **Serializable arguments** — every argument passed to the function. Pass primitives or plain objects; non-serializable values (functions, class instances, `Map`/`Set`) will fail.
4. **Closure variables** — values captured from the enclosing scope at call time also become part of the key.

The closure-capture rule is the load-bearing one for jseek: it means a `'use cache'` function defined inside another component picks up the outer component's props automatically. Useful when it works as intended; a footgun when an unintended closure variable inflates the key space.

```tsx
async function CompanyPage({ slug, viewerId }: { slug: string; viewerId: string }) {
  // BAD: viewerId is captured from props and becomes part of the cache key
  // for every (slug, viewerId) pair — defeats sharing across viewers.
  const getData = async () => {
    'use cache'
    return db.company.findUnique({ where: { slug } });
  };
  return <Body data={await getData()} />;
}
```

```tsx
// GOOD: lift the cache function above the component so the only key
// inputs are the explicit args.
async function getCompanyData(slug: string) {
  'use cache'
  return db.company.findUnique({ where: { slug } });
}

async function CompanyPage({ slug }: { slug: string }) {
  const data = await getCompanyData(slug);
  return <Body data={data} />;
}
```

Lift `'use cache'` functions to module scope unless you have a specific reason to want closure-capture (e.g., locale baked into a per-(slug, locale) variant). When in doubt, hoist.

## Recipes

### Per-locale `<html lang>` (the #2826 use case)

Every HTML route in jseek lives under `/<locale>/...`. So `app/[lang]/layout.tsx` is the de-facto root layout — it owns `<html>`/`<body>` and reads `locale` from the route param. There is no top-level `app/layout.tsx`; routes outside `[lang]/` are route handlers (sitemap, robots, OG images, `/api/*`) that don't render an HTML shell.

```tsx
// app/[lang]/layout.tsx
export function generateStaticParams() {
  return locales.map((lang) => ({ lang }));   // en/de/fr/it
}

export default async function LocaleLayout({ children, params }) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();
  return (
    <html lang={lang} suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}
```

`await params` is **not** a runtime API access here — the cache-components rule that `params` requires a Suspense ancestor only applies when `generateStaticParams` is absent. Since every locale is enumerated, the build prerenders one shell per locale and the `<html lang>` attribute is baked into the static output. Zero dynamic holes, zero per-request function invocations for the language attribute.

If you ever need a non-locale route with an HTML shell (rare), add a sibling `app/<segment>/layout.tsx` that defines `<html>/<body>` for that segment — Next.js supports multiple route-group root layouts as long as no two layouts up the chain both render `<html>`.

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

### `generateMetadata` for a dynamic route

Page metadata follows the same model: synchronous fields (title template, fixed OG image) prerender; data-derived fields (a company-specific title) need to come from a `'use cache'` lookup, not from runtime APIs.

```tsx
// app/[lang]/company/[slug]/page.tsx
export async function generateMetadata({ params }: { params: Promise<{ slug: string; lang: Locale }> }) {
  const { slug, lang } = await params;
  const company = await getCompanyBySlug(slug, lang); // 'use cache'-wrapped
  return {
    title: company ? `${company.name} jobs` : 'Company',
    description: company?.description ?? siteConfig.defaultDescription,
  };
}
```

`getCompanyBySlug` here is the same cached function the page body uses — no need to read it twice. If the metadata helper reads `headers()`/`cookies()`, it taints the route the same way a body read would; keep it on the cache path.

### Mutation that invalidates cached data

Two invalidation primitives with different semantics — pick by what the *next read* needs:

- **`updateTag(tag)`** — *immediate*. Within the same request that called it, subsequent reads of any `'use cache'` function tagged with `tag` see fresh data. Use after a mutation when the very next render in the same flow must reflect the new state (e.g., a server action that mutates and then renders a confirmation).
- **`revalidateTag(tag)`** — *background, stale-while-revalidate*. The current request still sees the cached entry; subsequent requests trigger a background refresh. Use when fresh-on-the-next-request is good enough — most product flows.

```ts
'use server'

import { revalidateTag, updateTag } from 'next/cache';

export async function updateCompanyDescription(slug: string, body: string) {
  await db.companyDescription.update({ where: { slug }, data: { body } });
  // Background: next request sees fresh data; current response stays fast.
  revalidateTag(`company:${slug}`);
}

export async function publishCompanyDescription(slug: string, body: string) {
  await db.companyDescription.update({ where: { slug }, data: { body } });
  // Same-request: a redirect → render that follows must show the new body.
  updateTag(`company:${slug}`);
}
```

When in doubt, use `revalidateTag()` — it's cheaper and the same-request guarantee of `updateTag()` is rarely actually needed.

### Custom Redis `cached()` helper (`src/lib/cache.ts`)

We keep our own Redis-backed `cached(key, fetcher, { ttl, skipIf })` helper for cross-instance shared state. It coexists with `'use cache'`:

| Property | `'use cache'` / `'use cache: remote'` (Next.js) | `cached()` (jseek Redis) |
|---|---|---|
| Storage | Per-region runtime cache (default) or platform shared cache (`: remote`) | Redis (Upstash), shared across regions and instances |
| Cache key | Auto-derived from function args + closures | Manual string key |
| Empty-result handling | Stored as the cached value | `skipIf` predicate can skip caching empty results (#2245) |
| Invalidation | `revalidateTag()` / `updateTag()` by tag | Manual `redis.del(key)` or TTL expiry |
| When to use | Per-render dedup; viewer-scoped variants; dependency on Next.js render lifecycle; cross-instance sharing via `: remote` | Empty-result skipping (the `skipIf` predicate has no `'use cache'` equivalent), or when manual key construction is genuinely load-bearing (e.g., keys derived from external systems) |

New code should default to `'use cache'`. Reach for `'use cache: remote'` when cross-instance sharing matters. Reach for `cached()` only when `skipIf` empty-skipping is the load-bearing requirement, or when cross-`'use cache'`-boundary dedup is needed (see below).

#### Cross-`'use cache'`-boundary dedup

Each `'use cache'` function runs in a clean AsyncLocalStorage snapshot
(`runInCleanSnapshot`), so React's `cache()` wrapper does NOT dedupe
calls across two `'use cache'` boundaries within the same request. The
canonical case: a page's `generateMetadata` and its body both call the
same data fetcher — under cacheComponents these are separate boundaries,
so each runs the fetcher once on a cold cache fill (2× upstream load).

Fix: wrap the data fetcher in Redis `cached()` (or `'use cache: remote'`)
one level below the page. The shared cache layer dedupes across boundaries
because both `'use cache'` calls hit the same Redis key. Used today by
`getCompanyBySlug` (`apps/web/src/lib/actions/company.ts`) and
`getPublicWatchlistByUserAndSlug` (`apps/web/src/lib/actions/watchlists.ts`).

#### Layered TTL: observable staleness can be 2×

When a `'use cache'` boundary calls a function wrapped in `cached()`,
both layers age independently. A mutation that invalidates the Redis
cache (or expires it) can still be served from the per-region `'use cache'`
layer for up to its own revalidate window. Worst-case observable staleness
≈ Redis TTL + `cacheLife.revalidate`.

Example: `/company/[slug]` has `cacheLife({ revalidate: 600 })` (10 min)
calling `getCompanyBySlug` with Redis `cached(..., { ttl: 600 })` (10 min).
A company description edit propagates within 10 min if Redis is invalidated
on the mutation, but worst-case 20 min if the per-region cache happened to
populate just before the invalidation. For most flows this is fine; surface
in the page's revalidate-budget when stricter freshness is needed.

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
4. **Build-output classifier** as a CI guard — proposed in #2885. The legacy `app/__tests__/isr-routes.test.ts` was retired in #2835 because the line-by-line scan it performed is now enforced by `pnpm build` itself. Until #2885 lands, regressions are caught at Vercel deploy rather than at PR time — keep an eye on Vercel preview build status before merging.

## Anti-patterns

- ❌ `await headers()` directly in `app/layout.tsx` without a `<Suspense>` boundary downstream of the read. The dynamic API access taints the layout's output; if you need it (e.g., for `<html lang>`), keep the read narrow and let everything else stay static.
- ❌ `await searchParams` in a page that should be cacheable. Move the read into a client subtree via `useSearchParams()`.
- ❌ Wrapping an entire async page body in `'use cache'` without thinking about which subtrees are actually deterministic. The cache key includes everything, including arguments and closures — over-broad caching means rare hit rates.
- ❌ Using `'use cache: private'` to dodge the work of refactoring a runtime-API read out of a cached function. The `: private` flavor exists for compliance escape hatches (per-user data paths that genuinely cannot be hoisted), not for "I don't want to plumb the value through as an argument." Do the refactor.
- ❌ Route-segment configs `revalidate`, `dynamic`, `dynamicParams`, `runtime`, `fetchCache` on pages or route handlers. The build rejects them outright when `cacheComponents: true` is enabled. Migrate to the directive equivalents in the cheat sheet below.
- ❌ Putting `Math.random()`, `Date.now()`, `crypto.randomUUID()` inside `'use cache'` and expecting different values per render. They freeze at build time inside the cache boundary.
- ❌ Reading session state (via `getSessionUserId`, `getViewerLanguages`, etc.) inside a route's render path **without Suspense**. Tainted helpers are now expected — but they have to be in dynamic subtrees, not in the static shell.

## Migration cheat sheet

When moving an existing component or route from the legacy model to cacheComponents:

| Legacy pattern | Cache Components replacement |
|---|---|
| `export const revalidate = 3600` on a page | **Remove the export** (build rejects it). Add `'use cache'` + `cacheLife({ revalidate: 3600 })` inside the page function — and inside `generateMetadata` if it's also data-derived |
| `export const dynamic = 'force-static'` | Remove. Wrap the page body in `'use cache'` + `cacheLife('max')` |
| `export const dynamic = 'force-dynamic'` | Remove (route handlers default to dynamic execution; pages stay static unless they read runtime APIs) |
| `export const dynamicParams = false` | Remove (not compatible with cacheComponents). Non-prerendered slugs fall through to the page function — call `notFound()` on missing data instead |
| `export const runtime = 'nodejs'` | Remove (Fluid Compute Node.js is the default; the segment config is rejected) |
| `export const runtime = 'edge'` | Edge runtime is not supported under cacheComponents — leave on Node.js (Fluid Compute) |
| `cookies()` / `headers()` in a server component | Move into a `<Suspense>`-wrapped subtree, OR extract the value to a client read, OR pass as an argument into a `'use cache'` function |
| `new Date()` / `Date.now()` / `Math.random()` in a server-render path | Pre-compute at module scope (build-time deploy refresh), OR move into a `<Suspense>` subtree that calls `connection()` first, OR put inside `'use cache'` if "value at cache build time" is acceptable |
| `unstable_cache(fn, key, opts)` | `'use cache'` directive inside `fn` body; replace `opts.tags` with `cacheTag()` calls; replace `opts.revalidate` with `cacheLife({ revalidate: N })`. Drop the manual `key` array — args + closures become the key automatically. See "How cache keys are derived" above |
| OpenGraph image function with `revalidate = N` | Remove the export — `next/og` `ImageResponse` is a class instance and isn't serializable for `'use cache'`. The framework caches OG images via HTTP `Cache-Control` headers automatically |

## References

- Cache Components official guide: <https://nextjs.org/docs/app/getting-started/cache-components>
- `'use cache'` directive: <https://nextjs.org/docs/app/api-reference/directives/use-cache>
- `unstable_cache` legacy reference (for migrating existing code): <https://nextjs.org/docs/app/api-reference/functions/unstable_cache>
- jseek tracking issues: #2835 (migration), #2826 (the `<html lang>` use case that's the first concrete consumer of this model)
- jseek incident referenced throughout: #2243 (the original ISR-leakage CPU-quota incident — the reason the now-retired `app/__tests__/isr-routes.test.ts` line-scanner existed; the pattern is replaced with build-time enforcement under cacheComponents)
