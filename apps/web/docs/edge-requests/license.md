# License Page (`/:lang/license`)

**Route group:** `(public)` | **Rendering:** Static (pre-rendered at build time per locale)

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/license` HTML document | Static (CDN) | Pre-rendered |
| 2 | Middleware redirect | Edge function | Only if visiting `/license` without locale prefix |
| 3-6 | JS chunks | Static (CDN) | Framework + page + LicenseContent component |
| 7 | CSS bundle | Static (CDN) | Tailwind |
| 8 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font |
| 9 | `/js_wide_logo_black.svg` or `_white.svg` | Static (CDN) | Header logo |
| 10 | `/publicdomain/the_judge_light.png` or `_dark.png` | Static (CDN) | Hero artwork (from `siteConfig.license.hero.art`) |
| 11 | `/favicon.ico` | Static (CDN) | Browser |
| 12 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` |
| 13 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` |
| 14 | Analytics beacon POST | Edge | Post-load telemetry |

## Notes

- Text page with one public domain artwork in the hero.
- Art rendered via `next/image` (`/_next/image?url=...` optimization endpoint).
- No API calls, no server actions.

## Estimated edge requests

**First visit (cold cache):** ~14
**Subsequent visit (warm cache):** ~2
