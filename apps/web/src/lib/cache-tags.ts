/**
 * Centralized `cacheTag` namers for `'use cache'` boundaries that need
 * targeted invalidation. Mutation server actions call `revalidateTag()`
 * with the same string to bust the corresponding cached pages on
 * Vercel's per-region runtime cache.
 *
 * Tag naming convention: `<resource>:<identifier-parts>`. Keep prefixes
 * stable across deploys — tags persist across builds (only build-id keys
 * inside `'use cache'` invalidate on deploy).
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
