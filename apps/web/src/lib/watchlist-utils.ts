import type { WatchlistFilters } from "@/lib/services/watchlists";
import { canonicalStringCompare } from "@/lib/sort";

type SlugItem = { slug: string };

/**
 * Preserve stored taxonomy slugs that could not be resolved for the current
 * render. This is especially important during a Typesense outage: the owner
 * UI may receive no resolved chips, and an unrelated edit must not rewrite
 * the saved filters as empty.
 */
export function mergeWatchlistTaxonomySlugs(
  storedSlugs: readonly string[] | undefined,
  initiallyResolved: readonly SlugItem[],
  nextResolved: readonly SlugItem[],
): string[] | undefined {
  const resolvedAtLoad = new Set(initiallyResolved.map((item) => item.slug));
  const unresolved = (storedSlugs ?? []).filter(
    (slug) => !resolvedAtLoad.has(slug),
  );
  const merged = [
    ...new Set([...nextResolved.map((item) => item.slug), ...unresolved]),
  ];
  return merged.length > 0 ? merged : undefined;
}


// A watchlist is "trivial" when it carries no meaningful filters and tracks
// no companies — effectively a blank shell. We exclude these from public
// listings and search engines so they don't dilute the index.
// `anyCompany` and `salaryCurrency` alone don't count (they're defaults/prefs).
//
// Mirror of the `nonTrivialWatchlistPredicate` SQL fragment in
// `@/lib/services/watchlists`. Keep the two in sync — see the drift-guard
// test in `__tests__/watchlist-utils.test.ts` which fails if a new key
// is added to `WatchlistFilters` without being checked here.
export function isTrivialWatchlist(
  filters: WatchlistFilters | null | undefined,
  companyCount: number,
): boolean {
  if (companyCount > 0) return false;
  const f = filters ?? {};
  return !(
    f.keywords?.length ||
    f.locationSlugs?.length ||
    f.occupationSlugs?.length ||
    f.senioritySlugs?.length ||
    f.technologySlugs?.length ||
    f.workMode?.length ||
    f.employmentType?.length ||
    f.salaryMin != null ||
    f.salaryMax != null ||
    f.experienceMin != null ||
    f.experienceMax != null
  );
}

/**
 * Build the cache-key fragment used by `getWatchlistMatchingCompanyCount`
 * (and friends) keyed off a watchlist's filter set.
 *
 * Slug-bearing dimensions sort with `canonicalStringCompare`
 * (locale-independent `Intl.Collator("en", { sensitivity: "base" })`) so
 * accented keywords / slugs (e.g. `"übung"`) collapse onto the same cache
 * slot as their permutation siblings. Bare `.sort()` uses UTF-16 code unit
 * order, where `"ü"` (U+00FC) sorts after `"z"` (U+007A) — that splits the
 * cache for the same logical filter set. See #3276 (follow-up to #3221).
 *
 * `companyIds` are numeric-looking strings (no accents), so raw `.sort()`
 * is fine and keeps the legacy lexicographic ordering — the same caveat
 * noted in the original issue.
 *
 * Lives here (not in `actions/watchlists.ts`) because `"use server"`
 * modules can only export async functions; co-located unit tests need a
 * sync import. The action file re-uses this helper internally.
 */
export function buildFilterCacheKey(f: WatchlistFilters, companyIds: string[]): string {
  const parts: string[] = [];
  if (f.anyCompany) parts.push("any");
  if (companyIds.length) parts.push(`c:${[...companyIds].sort().join(",")}`);
  if (f.keywords?.length) parts.push(`kw:${[...f.keywords].sort(canonicalStringCompare).join(",")}`);
  if (f.locationSlugs?.length) parts.push(`loc:${[...f.locationSlugs].sort(canonicalStringCompare).join(",")}`);
  if (f.occupationSlugs?.length) parts.push(`occ:${[...f.occupationSlugs].sort(canonicalStringCompare).join(",")}`);
  if (f.senioritySlugs?.length) parts.push(`sen:${[...f.senioritySlugs].sort(canonicalStringCompare).join(",")}`);
  if (f.technologySlugs?.length) parts.push(`tech:${[...f.technologySlugs].sort(canonicalStringCompare).join(",")}`);
  if (f.workMode?.length) parts.push(`wm:${[...f.workMode].sort(canonicalStringCompare).join(",")}`);
  if (f.employmentType?.length) parts.push(`et:${[...f.employmentType].sort(canonicalStringCompare).join(",")}`);
  if (f.salaryMin != null) parts.push(`smin:${f.salaryMin}`);
  if (f.salaryMax != null) parts.push(`smax:${f.salaryMax}`);
  if (f.experienceMin != null) parts.push(`emin:${f.experienceMin}`);
  if (f.experienceMax != null) parts.push(`emax:${f.experienceMax}`);
  return parts.join("|");
}
