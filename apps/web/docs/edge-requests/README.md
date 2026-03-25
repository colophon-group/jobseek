# Edge Request & Fluid Compute Reports

Per-page breakdown of Vercel edge requests and serverless function compute for
the Job Seek web app.

Every HTTP request that reaches the Vercel deployment counts as an **edge
request**, including static assets served from CDN, middleware invocations,
serverless function routing, and dynamically generated resources.

Every dynamic page render, server action, or API route call triggers a
**serverless function invocation**, billed by GB-seconds (memory × wall-clock
duration). Each page report includes a "Fluid compute" section showing DB
query count, execution pattern (sequential vs parallel), Redis caching, and
estimated function duration. See the [main doc](../edge-requests.md) for the
full compute model, connection config, and optimization rules.

## Common baseline (all pages)

These edge requests are made on **every** page load (first visit, cold cache):

| # | Request | Type | Notes |
|---|---------|------|-------|
| 1 | HTML document | SSR or static | The page itself |
| 2 | Middleware redirect | Edge function | Only if path has no locale prefix (e.g. `/about` -> `/en/about`). Skipped for locale-prefixed paths, `_next`, `api`, `flags`, `fonts`, `publicdomain`, `favicon.ico` |
| 3-6 | `/_next/static/chunks/*.js` | Static (CDN) | Framework + page JS bundles (varies per page, typically 3-8 chunks) |
| 7 | `/_next/static/css/*.css` | Static (CDN) | Compiled Tailwind CSS |
| 8 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font (others loaded on demand: Medium, SemiBold, Bold) |
| 9 | `/favicon.ico` | Static (CDN) | Browser-initiated |
| 10 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` beacon |
| 11 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` beacon |
| 12 | Analytics beacon POST | Edge | Sent by Vercel Analytics after page load |

**Cache behavior:** Fonts cache for 1 year (immutable). JS/CSS chunks use Next.js content-hash URLs and cache indefinitely. Subsequent visits within the same session reuse cached assets, reducing edge requests to just the document + analytics.

## Page index

### Public pages (pre-rendered at build time)
- [Landing page (`/`)](landing.md)
- [About (`/about`)](about.md)
- [FAQ (`/faq`)](faq.md)
- [How We Index (`/how-we-index`)](how-we-index.md)
- [License (`/license`)](license.md)
- [Privacy Policy (`/privacy-policy`)](privacy-policy.md)
- [Terms (`/terms`)](terms.md)

### Auth pages (pre-rendered at build time)
- [Sign In (`/sign-in`)](sign-in.md)
- [Sign Up (`/sign-up`)](sign-up.md)
- [Check Email (`/check-email`)](check-email.md)
- [Forgot Password (`/forgot-password`)](forgot-password.md)
- [Verify Email (`/verify-email`)](verify-email.md)
- [Reset Password (`/reset-password`)](reset-password.md)

### App pages (dynamic, SSR per request)
- [Explore (`/explore`)](explore.md)
- [Company (`/company/:slug`)](company.md)
- [My Jobs (`/my-jobs`)](my-jobs.md)
- [My Jobs Stats (`/my-jobs/stats`)](my-jobs-stats.md)
- [Progress (`/progress`)](progress.md)
- [Watchlists (`/watchlists`)](watchlists.md)
- [Shared Watchlist (`/:user/:watchlist`)](shared-watchlist.md)
- [Settings (`/settings`)](settings.md)
- [Account Settings (`/settings/account`)](settings-account.md)
- [Billing Settings (`/settings/billing`)](settings-billing.md)

### Special routes (non-page)
- [Sitemap, Robots, OG Images, API](special-routes.md)
