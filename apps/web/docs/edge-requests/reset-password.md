# Reset Password Page (`/:lang/reset-password`)

**Route group:** `[lang]` (outside route groups) | **Rendering:** Client-side only

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/reset-password?token=...` HTML document | Static/SSR | Page is `"use client"` — minimal server render |
| 2 | Middleware redirect | Edge function | Only if visiting `/reset-password` without locale prefix |
| 3-5 | JS chunks | Static (CDN) | Framework + page (AuthShell, authClient) |
| 6 | CSS bundle | Static (CDN) | Tailwind |
| 7 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font |
| 8 | `/js_wide_logo_black.svg` or `_white.svg` | Static (CDN) | AuthShell logo |
| 9 | `/favicon.ico` | Static (CDN) | Browser |
| 10 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` |
| 11 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` |
| 12 | Analytics beacon POST | Edge | Post-load telemetry |

## User-triggered requests

On form submission:
| Request | Type | Source |
|---------|------|--------|
| `POST /api/auth/reset-password` | Serverless function | `authClient.resetPassword({ newPassword, token })` |

## Notes

- Outside the `(auth)` route group — no session check on load.
- Shows password form if token present, error message if missing.

## Fluid compute (serverless function duration)

### SSR render

**Zero function compute.** Same as verify-email — `"use client"` page outside
route groups. Minimal SSR shell, no data fetching.

### Client-triggered

| Action | Queries | Est. duration |
|--------|---------|---------------|
| `POST /api/auth/reset-password` | 2-3 (token verify + password update) | 20-100ms |

Only fires on form submission (user-initiated).

## Estimated edge requests

**First visit (cold cache):** ~12
**Subsequent visit (warm cache):** ~2
