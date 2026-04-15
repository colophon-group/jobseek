import type { HistogramFilters } from "./types";

/**
 * Build a Typesense filter_by string from user-specified filter dimensions.
 *
 * Does NOT inject `is_active:true` — callers prepend it explicitly.
 * Returns an empty string when no filters are active.
 */
export function buildFilterString(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  filters: any,
): string {
  if (!filters) return "";
  const parts: string[] = [];

  if (filters.companyId) {
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

/**
 * Build a filter string from HistogramFilters (subset of SearchFilters).
 * `keywords` is the search query, not a filter clause — buildFilterString
 * silently ignores it.
 */
export function buildHistogramFilterString(filters: HistogramFilters): string {
  return buildFilterString(filters);
}
