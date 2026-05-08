# Landing Page (`/:lang/`)

**Route group:** `(public)` | **Rendering:** Static (pre-rendered at build time per locale)

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/` HTML document | Static (CDN) | Pre-rendered via `generateStaticParams` |
| 2 | Middleware redirect | Edge function | Only if visiting `/` without locale prefix |
| 3-8 | JS chunks | Static (CDN) | Framework + page + client components (Hero, Features, Pricing, PublicDomainArt) |
| 9 | CSS bundle | Static (CDN) | Tailwind |
| 10 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font |
| 11 | `/js_wide_logo_black.svg` or `_white.svg` | Static (CDN) | Header logo (theme-dependent) |
| 12 | `/publicdomain/the_astrologer_light.png` or `_dark.png` | Static (CDN) | Hero artwork (theme-dependent) |
| 13 | `/publicdomain/the_miser_light.png` or `_dark.png` | Static (CDN) | After-pricing artwork (theme-dependent) |
| 14-15 | `/screenshots/{lang}/feature1-{light,dark}.png` | Static (CDN) | Feature section 1 screenshot (both themes preloaded by `ThemedImage`) |
| 16-17 | `/screenshots/{lang}/feature2-{light,dark}.png` | Static (CDN) | Feature section 2 screenshot |
| 18-19 | `/screenshots/{lang}/feature3-{light,dark}.png` | Static (CDN) | Feature section 3 screenshot |
| 20 | `/favicon.ico` | Static (CDN) | Browser |
| 21 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` |
| 22 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` |
| 23 | Analytics beacon POST | Edge | Post-load telemetry |

## OG image

When shared on social media, crawlers fetch:
- `/:lang/opengraph-image` — dynamically generated PNG via `ImageResponse` (1 edge + 1 serverless function invocation)

## Prefetch requests (eliminated)

Before the prefetch fix, this page generated phantom SSR invocations from
links that entered the viewport:

| Link | Target | Cost | Status |
|------|--------|------|--------|
| Header "Get started" Button | `/explore` (dynamic SSR) | 1 serverless invocation + 4 DB queries | **Fixed** — `prefetch={false}` |
| Hero "Get started" Button | `/explore` (dynamic SSR) | 1 serverless invocation + 4 DB queries | **Fixed** — `prefetch={false}` |
| Pricing CTA Buttons | `/sign-up` (dynamic SSR) | 1 serverless invocation + session check | **Fixed** — `prefetch={false}` |
| Header About/FAQ/How We Index | Static pages | CDN hit only (low cost) | **Fixed** — `prefetch={false}` |

These 3-4 phantom SSR invocations per landing page visit are now eliminated.

## Notes

- `ThemedImage` renders only the active theme variant (single `<Image>` tag, not both).
- Public domain art images use the `PublicDomainArt` component with `next/image` optimization, so they go through `/_next/image?url=...` which is an additional edge request for the image optimization endpoint (cached after first hit).
- The `Hero` and `Features` sections are client components (`"use client"`) — their JS bundles are separate chunks.
- No server actions or API calls on this page. All content is static.

## Fluid compute (serverless function duration)

**Zero function compute.** Pre-rendered at build time. Served from CDN with
no serverless invocation. Only the analytics beacon POST and proxy
redirect (if no locale prefix) touch edge/serverless infrastructure.

## Estimated edge requests

**First visit (cold cache):** ~23
**Subsequent visit (warm cache):** ~2 (document + analytics beacon)
