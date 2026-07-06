/**
 * Centralized `cacheTag` namers for `'use cache'` boundaries that need
 * targeted invalidation. Mutation server actions call `updateTag()`
 * (immediate, not stale-while-revalidate) with the same string to bust
 * the corresponding cached pages on Vercel's per-region runtime cache.
 *
 * Tag naming convention: `<resource>:<identifier-parts>`. Keep prefixes
 * stable across deploys — tags persist across builds (only build-id keys
 * inside `'use cache'` invalidate on deploy).
 *
 * Currently `watchlistCacheTag` is the only tag actively invalidated
 * by mutations. The `companyCacheTag` / `blogPostCacheTag` /
 * `blogIndexCacheTag` namers are forward-compatible scaffolding —
 * companies are mutated by the crawler (out-of-process; would need an
 * API endpoint hooked to `updateTag` to invalidate) and blog content
 * is static-from-disk (deploy busts via build-id cache key change).
 */

export function watchlistCacheTag(userSlug: string, watchlistSlug: string): string {
  return `watchlist:${userSlug}:${watchlistSlug}`;
}

export function companyCacheTag(slug: string): string {
  return `company:${slug}`;
}

/**
 * Per-company-id cache tag for the per-company derived caches migrated
 * to `'use cache'` in #2884 (bucket 4): `getCompanyPostings`,
 * `getCompanyTopLocations`, `getCompanyLocationsGrouped`,
 * `getSimilarCompanies` (filtered + unfiltered). These functions are
 * keyed by company UUID (not slug) since the slug isn't an input — so
 * the tag mirrors that shape. A future invalidation hook can fire BOTH
 * `companyCacheTag(slug)` and `companyByIdCacheTag(id)` to drop every
 * cached fragment for a given company.
 */
export function companyByIdCacheTag(companyId: string): string {
  return `company-id:${companyId}`;
}

/**
 * Shared "all CSV-driven company data" tag — fired by
 * `/api/internal/invalidate-typeahead` after `crawler sync` to evict
 * the migrated `getCompanyBySlug` and `getSimilarCompanies` slots that
 * shift when a CSV row changes (rename, industry move). Mirrors the
 * `company-slug:` + `company-similar:` Redis-prefix sweep the legacy
 * `cached()` path used (#2715).
 *
 * Posting-derived per-company slots (`getCompanyPostings`,
 * `getCompanyTopLocations`, `getCompanyLocationsGrouped`) intentionally
 * do NOT carry this tag — they key off `job_posting` data, which a
 * CSV sync doesn't touch (matches the legacy comment in the route
 * handler). They drop on `companyByIdCacheTag(id)` only.
 */
export function companyCsvDataCacheTag(): string {
  return "company-csv-data";
}

export function blogPostCacheTag(slug: string): string {
  return `blog-post:${slug}`;
}

export function blogIndexCacheTag(): string {
  return "blog-index";
}

/**
 * Per-typeahead cache tags for the 5 typeahead suggestion functions
 * migrated to `'use cache'` in #2884 (typeaheads slice). Invalidated by
 * the crawler after `crawler sync` via the
 * `/api/internal/invalidate-typeahead` route — see that route handler
 * for the full sweep.
 *
 * Per-prefix granularity (rather than a single shared tag) mirrors the
 * `TYPEAHEAD_PREFIXES` list in the route 1:1, keeping the door open for
 * targeted future invalidation while preserving today's blanket-sweep
 * semantics.
 */
export function typeaheadLocationsCacheTag(): string {
  return "typeahead:locations";
}
export function typeaheadOccupationsCacheTag(): string {
  return "typeahead:occupations";
}
export function typeaheadSenioritiesCacheTag(): string {
  return "typeahead:seniorities";
}
export function typeaheadTechnologiesCacheTag(): string {
  return "typeahead:technologies";
}
export function typeaheadCompaniesCacheTag(): string {
  return "typeahead:companies";
}
