# How We Index Page (`/:lang/how-we-index`)

**Route group:** `(public)` | **Rendering:** Static (pre-rendered at build time per locale)

## Edge requests on first visit

| # | Request | Type | Source |
|---|---------|------|--------|
| 1 | `/:lang/how-we-index` HTML document | Static (CDN) | Pre-rendered |
| 2 | Middleware redirect | Edge function | Only if visiting `/how-we-index` without locale prefix |
| 3-6 | JS chunks | Static (CDN) | Framework + page + HowWeIndexContent component |
| 7 | CSS bundle | Static (CDN) | Tailwind |
| 8 | `/fonts/JetBrainsMono-Regular.woff2` | Static (CDN) | Primary font |
| 9 | `/js_wide_logo_black.svg` or `_white.svg` | Static (CDN) | Header logo |
| 10 | `/favicon.ico` | Static (CDN) | Browser |
| 11 | Vercel Analytics script | Static (CDN) | `@vercel/analytics` |
| 12 | Vercel Speed Insights script | Static (CDN) | `@vercel/speed-insights` |
| 13 | Analytics beacon POST | Edge | Post-load telemetry |

## OG image

Has its own dedicated OG image: `/:lang/how-we-index/opengraph-image` (dynamic PNG generation).

## Notes

- Text-heavy informational page. No user-specific data.
- Contains WebPage JSON-LD structured data (inlined in HTML).
- No API calls, no server actions.

## Estimated edge requests

**First visit (cold cache):** ~13
**Subsequent visit (warm cache):** ~2
