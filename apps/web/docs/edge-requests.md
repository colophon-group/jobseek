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
