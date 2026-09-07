import type { WatchlistCandidateFilters } from "@/lib/watchlist-matcher-contract";
import { buildFilterString, POSTING_BASE_FILTER } from "@/lib/search/typesense-filters";

/** Adjacent delivery windows compose without gaps or duplicate boundary jobs. */
export const WATCHLIST_CANDIDATE_WINDOW_BOUNDARY =
  "[windowStart, windowEnd)" as const;

export type WatchlistCandidateWindow = {
  /** Inclusive UTC instant. Must align to the index's whole-second precision. */
  windowStart: Date;
  /** Exclusive UTC instant. Must align to the index's whole-second precision. */
  windowEnd: Date;
};

export type WatchlistCandidateOrder = "interactive" | "newest";

export type WatchlistCandidateSearchParams = {
  q: string;
  query_by: "title";
  filter_by: string;
  sort_by: string;
  per_page: number;
  page: number;
};

function unixSecond(value: Date, name: "windowStart" | "windowEnd"): number {
  if (!(value instanceof Date) || !Number.isFinite(value.getTime())) {
    throw new TypeError(`${name} must be a valid UTC Date`);
  }
  if (value.getUTCMilliseconds() !== 0) {
    throw new RangeError(
      `${name} must align to whole-second Typesense precision`,
    );
  }
  return value.getTime() / 1_000;
}

/**
 * Compile the explicit UTC window to Typesense's numeric timestamp grammar.
 * The interval is start-inclusive and end-exclusive: `[start, end)`.
 */
export function buildWatchlistCandidateWindowFilter(
  window: WatchlistCandidateWindow,
): string {
  const start = unixSecond(window.windowStart, "windowStart");
  const end = unixSecond(window.windowEnd, "windowEnd");
  if (start >= end) {
    throw new RangeError("windowStart must be earlier than windowEnd");
  }
  return `first_seen_at:>=${start} && first_seen_at:<${end}`;
}

export function hasWatchlistCandidateScope(
  filters: WatchlistCandidateFilters,
): boolean {
  return filters.anyCompany === true || filters.companyIds.length > 0;
}

function safeCompanyIds(values: readonly string[]): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    // Company IDs are UUIDs today, but keep the contract compatible with a
    // future safe Typesense identifier while rejecting filter grammar.
    if (!/^[0-9a-z_-]{8,64}$/i.test(value)) {
      throw new TypeError("companyIds contains an invalid Typesense identifier");
    }
    if (!seen.has(value)) {
      seen.add(value);
      result.push(value);
    }
  }
  return result;
}

/**
 * One canonical compiler for every current-watchlist candidate search.
 * Interactive reads omit `window`; background notification/AI reads pass an
 * explicit window and choose deterministic newest-first ordering.
 */
export function buildWatchlistCandidateSearchParams(params: {
  filters: WatchlistCandidateFilters;
  offset: number;
  limit: number;
  window?: WatchlistCandidateWindow;
  order?: WatchlistCandidateOrder;
}): WatchlistCandidateSearchParams {
  if (!Number.isInteger(params.offset) || params.offset < 0) {
    throw new RangeError("offset must be a non-negative integer");
  }
  if (!Number.isInteger(params.limit) || params.limit < 0) {
    throw new RangeError("limit must be a non-negative integer");
  }

  const filters = params.filters;
  const structuredFilter = buildFilterString({
    locationIds: filters.locationIds,
    occupationIds: filters.occupationIds,
    seniorityIds: filters.seniorityIds,
    technologyIds: filters.technologyIds,
    workMode: filters.workMode?.length ? filters.workMode : undefined,
    employmentTypes: filters.employmentType?.length
      ? filters.employmentType
      : undefined,
    salaryMinEur: filters.salaryMin,
    salaryMaxEur: filters.salaryMax,
    experienceMin: filters.experienceMin,
    experienceMax: filters.experienceMax,
    languages: filters.languages,
  });
  const companyIds = filters.anyCompany ? [] : safeCompanyIds(filters.companyIds);
  const filterParts = [POSTING_BASE_FILTER];
  if (params.window) {
    filterParts.push(buildWatchlistCandidateWindowFilter(params.window));
  }
  if (companyIds.length > 0) {
    filterParts.push(`company_id:[${companyIds.join(",")}]`);
  }
  if (structuredFilter) filterParts.push(structuredFilter);

  const hasKeywords = Boolean(filters.keywords?.length);
  const order = params.order ?? "interactive";
  return {
    q: hasKeywords ? filters.keywords!.join(" ") : "*",
    query_by: "title",
    filter_by: filterParts.join(" && "),
    sort_by:
      order === "interactive" && hasKeywords
        ? "_text_match:desc,first_seen_at:desc"
        : "first_seen_at:desc",
    per_page: params.limit,
    page:
      params.limit === 0 ? 1 : Math.floor(params.offset / params.limit) + 1,
  };
}
