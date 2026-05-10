/**
 * Base filter applied to **every** `job_posting` snapshot query — hides
 * postings the crawler couldn't extract usable content for.
 *
 * - `is_active:true` — only currently-listed roles
 * - `has_content:!=false` — exclude postings with empty title or no
 *   description blob in R2 (issue #2917). The exporter stamps
 *   `has_content` (true|false) on every upsert; the `!=false` form keeps
 *   docs that haven't been backfilled yet visible (the field is absent on
 *   them, which `!=false` matches), and only excludes docs the exporter
 *   has explicitly marked as `false`. After
 *   `crawler backfill-typesense` runs, every doc has the field set and
 *   the filter is fully active.
 *
 * Use this constant — never the bare `is_active:true` string — when
 * composing `filter_by` for any search/listing surface that displays
 * the **current** state (active counts, listing pages, watchlist hits).
 *
 * For **flow** queries (year-count badges, "X in the last year"), use
 * {@link POSTING_FLOW_FILTER} instead — those should include delisted
 * postings to measure actual posting activity over time.
 */
export const POSTING_BASE_FILTER = "is_active:true && has_content:!=false";

/**
 * Filter for **flow** queries that count postings first seen in a time
 * window, regardless of current `is_active` state — i.e. measuring
 * activity over time, not the live snapshot.
 *
 * Drops `is_active:true` (vs {@link POSTING_BASE_FILTER}) because the
 * "in the last year" badge should include delisted postings — otherwise
 * year-count collapses to active-count whenever delistings happen at the
 * same rate as new listings (issue #2965). Empirically on 2026-05-09:
 *
 *   active only                       =>   709,051
 *   active && first_seen_at:>1y (bug) =>   709,051  (BUG: same as active)
 *   first_seen_at:>1y (correct)       => 1,400,449
 *
 * Retains `has_content:!=false` to keep parity with the active filter on
 * the content-quality dimension (don't surface broken postings even in
 * historical counts).
 */
export const POSTING_FLOW_FILTER = "has_content:!=false";

/**
 * Build a Typesense filter_by string from user-specified filter dimensions.
 *
 * Does NOT inject the base filter — callers prepend
 * {@link POSTING_BASE_FILTER} (or its parts) explicitly. Returns an empty
 * string when no filters are active.
 */
export function buildFilterString(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  filters: any,
): string {
  if (!filters) return "";
  const parts: string[] = [];

  // companyId reaches here from "use server" actions that clients can call
  // directly with arbitrary strings. Shape-validate before raw interpolation
  // so a hostile caller can't break out of the filter clause. Bad input is
  // dropped silently — a missing company scope is safer than an injection.
  if (filters.companyId && /^[0-9a-z_-]{8,64}$/i.test(filters.companyId)) {
    parts.push(`company_id:=${filters.companyId}`);
  }

  if (filters.locationIds?.length) {
    parts.push(`location_ids:[${filters.locationIds.join(",")}]`);
  }

  if (filters.occupationIds?.length) {
    parts.push(`occupation_ids:[${filters.occupationIds.join(",")}]`);
  }

  if (filters.seniorityIds?.length) {
    parts.push(`seniority_id:[${filters.seniorityIds.join(",")}]`);
  }

  if (filters.technologyIds?.length) {
    parts.push(`technology_ids:[${filters.technologyIds.join(",")}]`);
  }

  if (filters.employmentTypes?.length) {
    parts.push(`employment_type:[${filters.employmentTypes.join(",")}]`);
  }

  // Work-mode filter (issue #2983). Reuses the existing `location_types`
  // multi-array field on `job_posting`. Typesense `field:[a,b]` is OR
  // semantics across values — selecting multiple modes returns docs that
  // declare any of them. Postings with empty `location_types` (~0.9% of
  // active postings on 2026-05-09) drop out silently when this filter
  // is active — no sentinel-OR; treating unknown as bookable would
  // produce too many false positives (per issue Q4).
  if (filters.workMode?.length) {
    parts.push(`location_types:[${filters.workMode.join(",")}]`);
  }

  // salary_eur is optional — only apply when user has set a meaningful salary filter (> 0)
  const hasSalaryFilter =
    (filters.salaryMinEur != null && filters.salaryMinEur > 0) ||
    (filters.salaryMaxEur != null && filters.salaryMaxEur > 0);
  if (hasSalaryFilter) {
    const min = filters.salaryMinEur ?? 0;
    const max = filters.salaryMaxEur ?? 999999;
    parts.push(`salary_eur:[${min}..${max}]`);
  }

  // experience_min uses sentinel -1 for "not specified" (NULL in Postgres).
  // Parentheses are CRITICAL — without them, OR has lower precedence than &&
  // and the sentinel clause would match ALL -1 docs regardless of other filters.
  // Must match Postgres semantics: NULL experience is always included in
  // range filters (jobs without stated requirements shouldn't be excluded).
  if (filters.experienceMin != null && filters.experienceMax != null) {
    parts.push(
      `(experience_min:[${filters.experienceMin}..${filters.experienceMax}] || experience_min:=-1)`,
    );
  } else if (filters.experienceMin != null) {
    parts.push(
      `(experience_min:>=${filters.experienceMin} || experience_min:=-1)`,
    );
  } else if (filters.experienceMax != null) {
    parts.push(
      `(experience_min:<=${filters.experienceMax} || experience_min:=-1)`,
    );
  }

  // locales uses sentinel "_none" for jobs with no detected language.
  // Include it so those jobs match any language filter.
  if (filters.languages?.length) {
    parts.push(`locales:[${[...filters.languages, "_none"].join(",")}]`);
  }

  return parts.join(" && ");
}
