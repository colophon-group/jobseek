# Account Settings Page (`/:lang/settings/account`)

**Route group:** `(app)` | **Rendering:** Dynamic (`force-dynamic` on app layout)

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/settings/account` HTML document | SSR | Serverless function — fetches account data |
| 2 | Middleware redirect | Edge function | Only if visiting without locale prefix |
| 3-6 | JS chunks | Static (CDN) | Framework + AccountSettings + settings layout |
| 7 | CSS bundle | Static (CDN) | Tailwind |
| 8 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font |
| 9 | `/js_wide_logo_black.svg` or `_white.svg` | Static (CDN) | AppHeader logo |
| 10 | `/favicon.ico` | Static (CDN) | Browser |
| 11 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` |
| 12 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` |
| 13 | Analytics beacon POST | Edge | Post-load telemetry |

## Server-side data fetching (during SSR)

- App layout: `getSession()`, `getPreferences()`, `getSavedJobStatuses()`, `getStarredCompanyIds()`
- `getAccountPageData()` — user profile, linked accounts, etc.

## Client-side requests (user interaction)

| Request | Type | Trigger |
|---------|------|---------|
| Server action: update profile | Serverless function | Save name/username changes |
| `POST /api/auth/change-password` | Serverless function | Change password via Better Auth |
| `POST /api/auth/delete-user` | Serverless function | Delete account |

## Notes

- Form-based page. No images beyond header logo.
- Shares settings layout sidebar with General and Billing settings.

## Estimated edge requests

**First visit (cold cache):** ~13
**Subsequent visit (warm cache):** ~2
