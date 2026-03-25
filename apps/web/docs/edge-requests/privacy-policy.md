# Privacy Policy Page (`/:lang/privacy-policy`)

**Route group:** `(public)` | **Rendering:** Static (pre-rendered at build time per locale)

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/privacy-policy` HTML document | Static (CDN) | Pre-rendered |
| 2 | Middleware redirect | Edge function | Only if visiting `/privacy-policy` without locale prefix |
| 3-6 | JS chunks | Static (CDN) | Framework + page + PrivacyPolicyContent component |
| 7 | CSS bundle | Static (CDN) | Tailwind |
| 8 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font |
| 9 | `/js_wide_logo_black.svg` or `_white.svg` | Static (CDN) | Header logo |
| 10 | `/publicdomain/the_advocate_light.png` or `_dark.png` | Static (CDN) | Hero artwork (from `siteConfig.privacy.hero.art`) |
| 11 | `/favicon.ico` | Static (CDN) | Browser |
| 12 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` |
| 13 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` |
| 14 | Analytics beacon POST | Edge | Post-load telemetry |

## Notes

- Text page with one public domain artwork in the hero.
- No API calls, no server actions.

## Fluid compute (serverless function duration)

**Zero function compute.** Pre-rendered at build time, CDN-served.

## Estimated edge requests

**First visit (cold cache):** ~14
**Subsequent visit (warm cache):** ~2
