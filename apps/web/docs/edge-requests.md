# Edge Requests & Rendering Strategy

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

### Dynamic pages (dashboard, admin, API)

Pages that check authentication or read cookies are rendered on every request:

```
/en/dashboard/*   — dynamic (reads headers() for session)
/en/admin/*       — dynamic (reads headers() + cookies() + DB query)
/api/auth/*       — dynamic (Better Auth handler)
/api/admin/*      — dynamic (2FA verification)
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

If you need request data, put it in a **route-group layout** that only wraps the pages that need it (e.g. `(dashboard)/layout.tsx`).

### Keep `generateStaticParams` in `[lang]/layout.tsx`

This tells Next.js which locale variants to pre-render. Without it, `[lang]` is treated as a fully dynamic segment and every page requires an edge invocation.

### Keep the middleware matcher narrow

The middleware matcher explicitly excludes locale-prefixed paths. When adding a new locale, **add it to the matcher exclusion list** in `middleware.ts` — otherwise every request to that locale will unnecessarily invoke the middleware.

### Use route groups to separate static from dynamic

```
app/[lang]/
  (public)/    ← no cookies/headers in layout → static
  (auth)/      ← client component layout → static
  (dashboard)/ ← reads headers() for auth → dynamic (intentional)
```

If you add a new section, choose the right route group. Don't put static content pages inside `(dashboard)/`.

### Don't read cookies/headers for cosmetic data

For things like theme preference, locale, or UI state — handle client-side. Only use `cookies()`/`headers()` for data that genuinely requires server involvement (authentication, authorization, signed tokens).

### Check build output for static vs dynamic

After `pnpm build`, Next.js prints a summary showing which routes are static (`○`) and which are dynamic (`λ` or `ƒ`). Verify that public pages show as static. If a page unexpectedly shows as dynamic, check its component tree for `cookies()`, `headers()`, `searchParams`, or `fetch` with `cache: 'no-store'`.

### Never call useAuth() / useSession() on public pages

This is one of the most impactful rules for edge request cost.

Better Auth's `useSession()` hook (wrapped by our `useAuth()`) fires a
`GET /api/auth/get-session` request on mount. This is both an **edge request**
and a **serverless function invocation** — the two billable Vercel metrics.
It also re-fires on every **window focus** event (rate-limited to 5 seconds),
so tab-switching during a single visit generates repeated requests.

The public components (Header, MobileMenu, Hero, Pricing) previously used
`useAuth()` solely to toggle CTA labels ("Log in" vs "Go to dashboard").
This meant every visitor to every page — including the ~95% who are
anonymous — triggered a session API call that returned nothing useful.

**Current rule**: public pages always render the anonymous CTA state. Auth
checks only happen inside the dashboard route group, where the user is
already authenticated and the session check is genuinely needed.

If a future feature needs auth-dependent UI on a public page (e.g. showing
a user avatar in the header), consider one of these alternatives instead of
re-adding `useSession()`:

- **Non-httpOnly cookie hint**: Set a lightweight `logged_in=1` cookie
  (non-httpOnly) at sign-in. Client JS can check `document.cookie` without
  a network request. Clear it on sign-out.
- **Deferred component**: Use `next/dynamic` with `ssr: false` to load the
  auth-dependent fragment only after the main page renders, so it doesn't
  block or slow initial paint.

The `useAuth` hook is kept in `src/lib/useAuth.ts` for use in dashboard
components.

## Link prefetch strategy

Next.js `<Link>` prefetches the RSC payload for every linked route when the
link enters the viewport. Each prefetch is an edge request. With several
nav links in the header and footer, this adds **~7+ extra edge requests per
page view** — most of them wasted.

We disable prefetch on all links except high-conversion cross-page paths.

### Hot paths (prefetch **enabled** — default)

These are the links users are most likely to click next. Prefetching them
improves perceived navigation speed.

| Link | Location | Href | Why hot |
|------|----------|------|---------|
| "How do we index" | Header nav, Mobile menu | `/how-we-index` | Cross-page nav link, high interest |
| Log in CTA | Header, Mobile menu | `/sign-in` | Primary action |
| Hero primary CTA | Hero section | `/sign-in` | Main conversion funnel |
| Pricing CTAs | Pricing cards | `/sign-up` | Conversion funnel |

### Cold paths (prefetch **disabled**)

These links either point to the same page (anchor links), are rarely clicked,
or the user is already on an adjacent page.

| Link | Location | Href | Why cold |
|------|----------|------|----------|
| Product | Header nav, Mobile menu | `/` | Same-page anchor |
| Features | Header nav, Mobile menu, Hero | `/#features` | Same-page anchor |
| Pricing | Header nav, Mobile menu | `/#pricing` | Same-page anchor |
| License | Footer | `/license` | Legal — rarely clicked |
| Privacy | Footer | `/privacy-policy` | Legal — rarely clicked |
| Terms | Footer | `/terms` | Legal — rarely clicked |
| Sign in ↔ Sign up | Auth form | `/sign-in`, `/sign-up` | User is already on an auth page |
| Back to sign in | Verify email (error) | `/sign-in` | User is already on an auth page |
| Continue to dashboard | Verify email (success) | `/dashboard` | User will click immediately — no prefetch benefit |
| Logo (auth) | Auth layout | `/` | Return-to-marketing, not a hot path |

### When adding new links

- **Cross-page link likely to be clicked?** → Leave prefetch as default (enabled).
- **Same-page anchor, legal page, or low-traffic route?** → Add `prefetch={false}`.
- **Inside `Button` component?** → Pass `prefetch={false}` as a prop; it forwards to `<Link>`.

## Asset caching

Static assets use `Cache-Control` headers (configured in `next.config.ts`) to
reduce repeat edge requests.

| Asset | Cache TTL | Rationale |
|-------|-----------|-----------|
| Fonts (`/fonts/*`) | 1 year, `immutable` | Never change between deploys |
| `next/image` responses (`/_next/image`) | 1 week (`minimumCacheTTL`) | Default is 60s — far too short |
| SVGs, PNGs in `public/` | 1 week | Rarely change; no content hash in URL |
| JS/CSS chunks (`/_next/static/*`) | 1 year, `immutable` | Content-hashed filenames (Next.js default) |

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
