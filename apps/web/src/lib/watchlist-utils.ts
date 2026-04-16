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
