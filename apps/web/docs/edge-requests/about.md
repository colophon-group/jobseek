# About Page (`/:lang/about`)

**Route group:** `(public)` | **Rendering:** Static (pre-rendered at build time per locale)

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/about` HTML document | Static (CDN) | Pre-rendered |
| 2 | Middleware redirect | Edge function | Only if visiting `/about` without locale prefix |
| 3-6 | JS chunks | Static (CDN) | Framework + page + AboutContent component |
| 7 | CSS bundle | Static (CDN) | Tailwind |
| 8 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font |
| 9 | `/js_wide_logo_black.svg` or `_white.svg` | Static (CDN) | Header logo |
| 10 | `/publicdomain/adam_tills_the_soil_light.png` or `_dark.png` | Static (CDN) | Hero artwork (theme-dependent, from `siteConfig.about.hero.art`) |
| 11 | `/favicon.ico` | Static (CDN) | Browser |
| 12 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` |
| 13 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` |
| 14 | Analytics beacon POST | Edge | Post-load telemetry |

## OG image

Inherits from `(public)` layout OG image: `/:lang/opengraph-image` (dynamic PNG generation on first social share).

## Notes

- Text-heavy page. No API calls, no server actions.
- Contains JSON-LD structured data (WebPage schema) — inlined in HTML, no extra request.
- Public domain art rendered via `next/image` optimization (`/_next/image?url=...`).

## Fluid compute (serverless function duration)

**Zero function compute.** Pre-rendered at build time. Same as all `(public)`
pages — CDN-served, no serverless invocation.

## Estimated edge requests

**First visit (cold cache):** ~14
**Subsequent visit (warm cache):** ~2
