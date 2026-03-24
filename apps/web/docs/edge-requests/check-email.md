# Check Email Page (`/:lang/check-email`)

**Route group:** `(auth)` | **Rendering:** Dynamic (auth layout checks session)

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/check-email` HTML document | SSR | Auth layout checks session |
| 2 | Middleware redirect | Edge function | Only if visiting `/check-email` without locale prefix |
| 3-5 | JS chunks | Static (CDN) | Framework + page (lightweight client component) |
| 6 | CSS bundle | Static (CDN) | Tailwind |
| 7 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font |
| 8 | `/js_wide_logo_black.svg` or `_white.svg` | Static (CDN) | AuthShell logo |
| 9 | `/favicon.ico` | Static (CDN) | Browser |
| 10 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` |
| 11 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` |
| 12 | Analytics beacon POST | Edge | Post-load telemetry |

## User-triggered requests

On "Resend verification email" click:
| Request | Type | Source |
|---------|------|--------|
| `POST /api/auth/send-verification-email` | Serverless function | Resend via Better Auth (60s cooldown) |

## Notes

- Page reads email from `sessionStorage` (set by sign-up form). If missing, redirects to sign-in.
- No images beyond header logo. Very lightweight page.
- Does not use `AuthShell` wrapper directly — uses it from auth layout.

## Estimated edge requests

**First visit (cold cache):** ~12
**Subsequent visit (warm cache):** ~2
