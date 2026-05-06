import type { WatchlistFilters } from "@/lib/actions/watchlists";


// A watchlist is "trivial" when it carries no meaningful filters and tracks
// no companies — effectively a blank shell. We exclude these from public
// listings and search engines so they don't dilute the index.
// `anyCompany` and `salaryCurrency` alone don't count (they're defaults/prefs).
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
