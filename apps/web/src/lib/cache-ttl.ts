/**
 * Named TTL constants for the cache layer (seconds).
 *
 * Centralises the magic numbers previously scattered across `'use cache'`
 * + `cacheLife({ revalidate: N })`, Redis `cached(..., { ttl: N })`, and
 * API-route `apiResponse({ maxAge: N })` call sites. Changing a tier here
 * propagates to every consumer instead of hunting individual literals.
 *
 * Buckets reflect the values already in use across `apps/web/`:
 *
 * | Constant            | Seconds | Use cases                                    |
 * |---------------------|---------|----------------------------------------------|
 * | `CACHE_TTL_SHORT`   |   60    | Fast-moving lists (explore homepage,         |
 * |                     |         | search degraded responses, public watchlist  |
 * |                     |         | detail, new public watchlists search)        |
 * | `CACHE_TTL_POPULAR` |  120    | Popular watchlists list (curated, slower-    |
 * |                     |         | churn than freshly-created public lists)     |
 * | `CACHE_TTL_MEDIUM`  |  300    | Moderate churn (posting detail, company      |
 * |                     |         | postings, filtered watchlist counts,         |
 * |                     |         | default API `maxAge`)                        |
 * | `CACHE_TTL_DETAIL`  |  600    | Company detail page                          |
 * | `CACHE_TTL_LONG`    | 3600    | Semi-static taxonomies, locations, similar   |
 * |                     |         | companies, sitemap, watchlist matching count |
 * | `CACHE_TTL_DAY`     |86400    | Very static / rare-change (blog pages)       |
 *
 * Notes:
 * - Built-in Next.js `cacheLife()` profile names (`'hours'`, `'days'`,
 *   `'minutes'`) are already named and remain in place — these constants
 *   only replace numeric literals.
 * - `cache.test.ts` keeps literal `60` / `120` values because those are
 *   arbitrary fixtures for exercising the cache module, not policy.
 */

/** 60 seconds — fast-moving lists (explore, degraded search, new watchlists). */
export const CACHE_TTL_SHORT = 60;

/** 120 seconds — popular watchlists (curated, slower churn). */
export const CACHE_TTL_POPULAR = 120;

/** 300 seconds (5 min) — moderate churn (posting detail, company postings, default API). */
export const CACHE_TTL_MEDIUM = 300;

/** 600 seconds (10 min) — company detail page (cross-`'use cache'`-boundary dedup). */
export const CACHE_TTL_DETAIL = 600;

/** 3600 seconds (1 hour) — semi-static taxonomies, locations, sitemap, similar companies. */
export const CACHE_TTL_LONG = 3600;

/** 86400 seconds (1 day) — very static / rare-change (blog pages). */
export const CACHE_TTL_DAY = 86400;
