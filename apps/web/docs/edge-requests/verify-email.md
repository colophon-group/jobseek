# Verify Email Page (`/:lang/verify-email`)

**Route group:** `[lang]` (outside route groups) | **Rendering:** Client-side only

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/verify-email?token=...` HTML document | Static/SSR | Page is `"use client"` — minimal server render |
| 2 | Middleware redirect | Edge function | Only if visiting `/verify-email` without locale prefix |
| 3-5 | JS chunks | Static (CDN) | Framework + page (AuthShell, authClient) |
| 6 | CSS bundle | Static (CDN) | Tailwind |
| 7 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font |
| 8 | `/js_wide_logo_black.svg` or `_white.svg` | Static (CDN) | AuthShell logo |
| 9 | `/favicon.ico` | Static (CDN) | Browser |
| 10 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` |
| 11 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` |
| 12 | Analytics beacon POST | Edge | Post-load telemetry |

## Automatic client-side requests

On mount (triggered by `useEffect`):
| Request | Type | Source |
|---------|------|--------|
| `POST /api/auth/verify-email` | Serverless function | `authClient.verifyEmail({ query: { token } })` |

## Notes

- This page is outside the `(auth)` route group — it doesn't check/redirect based on session.
- On page load, it immediately calls `authClient.verifyEmail` with the token from the URL.
- Shows loading -> success/error states. No images beyond AuthShell logo.

## Fluid compute (serverless function duration)

### SSR render

**Zero function compute.** This page is `"use client"` outside the `(auth)`
route group — no session check, no server-side queries. The HTML document is
served as a minimal SSR shell with no data fetching.

### Client-triggered

| Action | Queries | Est. duration |
|--------|---------|---------------|
| `POST /api/auth/verify-email` | 2-3 (token lookup + user update) | 20-80ms |

One function invocation per page load (the auto-verify on mount).

## Estimated edge requests

**First visit (cold cache):** ~13 (12 + 1 verify API call)
**Subsequent visit (warm cache):** ~3 (document + API call + analytics)
