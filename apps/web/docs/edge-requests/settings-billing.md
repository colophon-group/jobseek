# Billing Settings Page (`/:lang/settings/billing`)

**Route group:** `(app)` | **Rendering:** Dynamic (`force-dynamic` on app layout)

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/settings/billing` HTML document | SSR | Serverless function — fetches plan info |
| 2 | Middleware redirect | Edge function | Only if visiting without locale prefix |
| 3-6 | JS chunks | Static (CDN) | Framework + BillingSettings + settings layout |
| 7 | CSS bundle | Static (CDN) | Tailwind |
| 8 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font |
| 9 | `/js_wide_logo_black.svg` or `_white.svg` | Static (CDN) | AppHeader logo |
| 10 | `/favicon.ico` | Static (CDN) | Browser |
| 11 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` |
| 12 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` |
| 13 | Analytics beacon POST | Edge | Post-load telemetry |

## Server-side data fetching (during SSR)

- App layout: `getSession()`, `getPreferences()`, `getSavedJobStatuses()`, `getStarredCompanyIds()`
- `getPlanInfo()` — current plan, subscription status, Stripe customer info

## Client-side requests (user interaction)

| Request | Type | Trigger |
|---------|------|---------|
| Server action: create Stripe checkout session | Serverless function | Upgrade to Pro |
| Server action: create Stripe portal session | Serverless function | Manage subscription |
| Redirect to `checkout.stripe.com` | External | Stripe-hosted checkout page |

## Notes

- Form-based page. Stripe checkout happens via external redirect, not embedded.
- No images beyond header logo.

## Fluid compute (serverless function duration)

### SSR render

| Step | Queries | Pattern | Cache | Est. duration |
|------|---------|---------|-------|---------------|
| `getSession()` | 1 | — | Redis 5min | 5-90ms |
| `getPreferences()` | 1 | parallel | None | 10-30ms |
| `getSavedJobStatuses()` | 1 | parallel | None | 10-30ms |
| `getStarredCompanyIds()` | 1 | parallel | None | 10-30ms |
| `getPlanInfo()` | 1 | — | None | 10-25ms |

**Total DB queries:** 5
**Estimated function duration:** 40-100ms (warm instance)

Lightweight. Single extra query for subscription/plan data.

## Estimated edge requests

**First visit (cold cache):** ~13
**Subsequent visit (warm cache):** ~2
