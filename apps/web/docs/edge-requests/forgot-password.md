# Forgot Password Page (`/:lang/forgot-password`)

**Route group:** `(auth)` | **Rendering:** Dynamic (auth layout checks session)

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/forgot-password` HTML document | SSR | Auth layout checks session |
| 2 | Middleware redirect | Edge function | Only if visiting `/forgot-password` without locale prefix |
| 3-5 | JS chunks | Static (CDN) | Framework + page client component |
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
| `POST /api/auth/request-password-reset` | Serverless function | Better Auth password reset email |

## Notes

- Simple form with single email field. No images beyond header logo.

## Estimated edge requests

**First visit (cold cache):** ~12
**Subsequent visit (warm cache):** ~2
