import type { WatchlistFilters } from "@/lib/actions/watchlists";
import { canonicalStringCompare } from "@/lib/sort";


// A watchlist is "trivial" when it carries no meaningful filters and tracks
// no companies — effectively a blank shell. We exclude these from public
// listings and search engines so they don't dilute the index.
// `anyCompany` and `salaryCurrency` alone don't count (they're defaults/prefs).
//
// Mirror of the `nonTrivialWatchlistPredicate` SQL fragment in
// `@/lib/actions/watchlists`. Keep the two in sync — see the drift-guard
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

const WATCHLIST_INDEXABLE_MIN_AGE_MS = 7 * 24 * 60 * 60 * 1000;
const WATCHLIST_INDEXABLE_MIN_TITLE_LENGTH = 4;
const WATCHLIST_INDEXABLE_DEFAULT_TITLE = "new watchlist";

/**
 * Stricter quality check than {@link isTrivialWatchlist}. Mirrors the SQL
 * predicate in `apps/web/src/lib/sitemap.ts::fetchSitemapWatchlists`
 * (#2823) so the page's `<meta robots>` and the sitemap inclusion stay
 * aligned: a watchlist that wouldn't survive the sitemap quality gate
 * also gets `noindex,follow` if discovered via direct link.
 *
 * Qualifies when ALL of:
 *   - title is substantive (≥4 chars after trim, not the default
 *     "New watchlist") so users who never edited the auto-created
 *     watchlist don't enter the index.
 *   - watchlist is at least 7 days old (lets the user populate it
 *     before the page hits the index).
 *   - tracks ≥3 companies, OR carries ≥1 keyword, OR carries ≥2
 *     taxonomy filters across location/occupation/seniority/technology.
 */
export function isQualifyingWatchlist(args: {
  title: string;
  filters: WatchlistFilters | null | undefined;
  companyCount: number;
  createdAt: string | Date;
}): boolean {
  const trimmedTitle = (args.title ?? "").trim();
  if (trimmedTitle.length < WATCHLIST_INDEXABLE_MIN_TITLE_LENGTH) return false;
  if (trimmedTitle.toLowerCase() === WATCHLIST_INDEXABLE_DEFAULT_TITLE) return false;

  const createdAtMs = args.createdAt instanceof Date
    ? args.createdAt.getTime()
    : new Date(args.createdAt).getTime();
  if (Number.isNaN(createdAtMs)) return false;
  if (Date.now() - createdAtMs < WATCHLIST_INDEXABLE_MIN_AGE_MS) return false;

  if (args.companyCount >= 3) return true;
  const f = args.filters ?? {};
  if (f.keywords?.length) return true;
  const taxonomyCount =
    (f.locationSlugs?.length ?? 0) +
    (f.occupationSlugs?.length ?? 0) +
    (f.senioritySlugs?.length ?? 0) +
    (f.technologySlugs?.length ?? 0);
  return taxonomyCount >= 2;
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
