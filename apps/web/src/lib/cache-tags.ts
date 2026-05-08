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
