# Sign Up Page (`/:lang/sign-up`)

**Route group:** `(auth)` | **Rendering:** Dynamic (checks session server-side, redirects if logged in)

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/sign-up` HTML document | SSR | Auth layout calls `auth.api.getSession()` |
| 2 | Middleware redirect | Edge function | Only if visiting `/sign-up` without locale prefix |
| 3-6 | JS chunks | Static (CDN) | Framework + AuthForm + AuthShell + OAuthButtons |
| 7 | CSS bundle | Static (CDN) | Tailwind |
| 8 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font |
| 9 | `/js_wide_logo_black.svg` or `_white.svg` | Static (CDN) | AuthShell logo |
| 10 | `/favicon.ico` | Static (CDN) | Browser |
| 11 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` |
| 12 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` |
| 13 | Analytics beacon POST | Edge | Post-load telemetry |

## User-triggered requests

On form submission or OAuth click:
| Request | Type | Source |
|---------|------|--------|
| `POST /api/auth/sign-up/email` | Serverless function | Email/password registration via Better Auth |
| `GET /api/auth/sign-in/social?provider=github` | Serverless function | OAuth redirect |

## Notes

- Same component as sign-in (`AuthForm` with `mode="sign-up"`), identical asset profile.
- Sign-up form has an additional "Name" field but same JS bundle.
- On success, redirects to `/check-email` for email verification.

## Fluid compute (serverless function duration)

### SSR render

| Step | Queries | Cache | Est. duration |
|------|---------|-------|---------------|
| `getSession()` | 1 | Redis 5min | 5-90ms |

**Total DB queries:** 1
**Estimated function duration:** 10-90ms (warm instance)

Same as sign-in — session check only. Unauthenticated users get a Redis null
in ~5ms.

## Estimated edge requests

**First visit (cold cache):** ~13
**Subsequent visit (warm cache):** ~2
