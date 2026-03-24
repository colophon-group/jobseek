# Sign In Page (`/:lang/sign-in`)

**Route group:** `(auth)` | **Rendering:** Dynamic (checks session server-side, redirects if logged in)

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/sign-in` HTML document | SSR | Auth layout calls `auth.api.getSession()` to check if already logged in |
| 2 | Middleware redirect | Edge function | Only if visiting `/sign-in` without locale prefix |
| 3-6 | JS chunks | Static (CDN) | Framework + AuthForm + AuthShell + OAuthButtons client components |
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
| `POST /api/auth/sign-in/email` | Serverless function | Email/password sign-in via Better Auth |
| `GET /api/auth/sign-in/social?provider=github` | Serverless function | OAuth redirect (GitHub, Google, LinkedIn) |

## Notes

- Auth layout is dynamic (`auth.api.getSession()` requires `headers()`), so document is SSR on every request.
- If user is already logged in, the layout redirects to `/:lang/explore` (302) — no page content rendered.
- AuthShell renders the wide logo via `ThemedImage` (both light/dark variants).
- No public domain artwork on this page.

## Estimated edge requests

**First visit (cold cache):** ~13
**Subsequent visit (warm cache):** ~2 (document is always SSR + analytics)
