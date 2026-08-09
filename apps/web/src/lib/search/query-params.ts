/**
 * Shared query-parameter helpers for filter state in URLs.
 *
 * Both the search page and the company page use the same `q` / `loc` / `occ` / `sen` format,
 * so links between them can carry filters across.
 */

import { isEmploymentType, isWorkMode, type EmploymentType, type WorkMode } from "./types";

/**
 * Query parameters that change the search result set. UI-only state such as
 * ``show`` and attribution parameters are intentionally not included.
 */
export const SEARCH_FILTER_PARAM_KEYS = [
  "q",
  "loc",
  "occ",
  "sen",
  "tech",
  "wm",
  "etype",
  "sal",
  "salcur",
  "exp",
] as const;

type SearchParamsReader = {
  getAll(name: string): string[];
};

/** Select only result-bearing search parameters in a stable order. */
export function selectSearchFilterParams(
  searchParams: SearchParamsReader,
): URLSearchParams {
  const selected = new URLSearchParams();
  for (const key of SEARCH_FILTER_PARAM_KEYS) {
    for (const value of searchParams.getAll(key)) {
      selected.append(key, value);
    }
  }
  return selected;
}

/** Serialize only result-bearing search parameters, without a leading ``?``. */
export function serializeSearchFilterParams(
  searchParams: SearchParamsReader,
): string {
  return selectSearchFilterParams(searchParams).toString();
}

/** Whether the URL contains at least one result-bearing search parameter. */
export function hasSearchFilterParams(
  searchParams: SearchParamsReader,
): boolean {
  return SEARCH_FILTER_PARAM_KEYS.some(
    (key) => searchParams.getAll(key).length > 0,
  );
}

/** Convert result-bearing search parameters to the server-action input shape. */
export function searchFilterParamsToObject(
  searchParams: SearchParamsReader,
): Record<string, string | string[]> {
  const out: Record<string, string | string[]> = {};
  for (const key of SEARCH_FILTER_PARAM_KEYS) {
    const values = searchParams.getAll(key);
    if (values.length === 0) continue;
    out[key] = values.length > 1 ? values : values[0];
  }
  return out;
}

export interface SerializableLocation {
  id: number;
  slug: string;
  name: string;
  type: string;
  parentName?: string | null;
}

export interface SerializableOccupation {
  id: number;
  slug: string;
  name: string;
}

export interface SerializableSeniority {
  id: number;
  slug: string;
  name: string;
}

export interface SerializableTechnology {
  id: number;
  slug: string;
  name: string;
}

/** Optional salary/experience filter state for URL serialization. */
export interface SalaryExperienceFilters {
  salaryMin?: number;
  salaryMax?: number;
  salaryCurrency?: string;
  experienceMin?: number;
  experienceMax?: number;
}

/**
 * Parse the `wm` URL parameter into a list of valid {@link WorkMode}
 * values. Drops any tokens that are not one of the canonical
 * `onsite | hybrid | remote` values, deduplicates, and preserves input
 * order. Returns an empty array for missing/empty input. (issue #2983)
 */
export function parseWorkModeParam(raw: string | null | undefined): WorkMode[] {
  if (!raw) return [];
  const seen = new Set<WorkMode>();
  const out: WorkMode[] = [];
  for (const token of raw.split(",")) {
    const trimmed = token.trim().toLowerCase();
    if (!trimmed) continue;
    if (!isWorkMode(trimmed)) continue;
    if (seen.has(trimmed)) continue;
    seen.add(trimmed);
    out.push(trimmed);
  }
  return out;
}

/**
 * Parse the `etype` URL parameter into public employment-type filter
 * values. Invalid tokens are dropped to match the existing `wm` behavior:
 * callers can append unknown future values without breaking the endpoint,
 * while search only receives canonical values it can safely filter on.
 */
export function parseEmploymentTypeParam(
  raw: string | null | undefined,
): EmploymentType[] {
  if (!raw) return [];
  const seen = new Set<EmploymentType>();
  const out: EmploymentType[] = [];
  for (const token of raw.split(",")) {
    const trimmed = token.trim().toLowerCase();
    if (!trimmed) continue;
    if (!isEmploymentType(trimmed)) continue;
    if (seen.has(trimmed)) continue;
    seen.add(trimmed);
    out.push(trimmed);
  }
  return out;
}

/**
 * Build a query string (without leading `?`) from keywords + locations + occupations + seniorities.
 * Returns an empty string when there are no filters.
 */
export function buildFilterQuery(
  keywords: string[],
  locations: SerializableLocation[],
  occupations?: SerializableOccupation[],
  seniorities?: SerializableSeniority[],
  technologies?: SerializableTechnology[],
  workMode?: WorkMode[],
): string {
  const params = new URLSearchParams();
  if (keywords.length > 0) params.set("q", keywords.join(","));
  if (locations.length > 0) {
    params.set("loc", locations.map((l) => l.slug).join(","));
  }
  if (occupations && occupations.length > 0) {
    params.set("occ", occupations.map((o) => o.slug).join(","));
  }
  if (seniorities && seniorities.length > 0) {
    params.set("sen", seniorities.map((s) => s.slug).join(","));
  }
  if (technologies && technologies.length > 0) {
    params.set("tech", technologies.map((t) => t.slug).join(","));
  }
  if (workMode && workMode.length > 0) {
    params.set("wm", workMode.join(","));
  }
  return params.toString();
}

/**
 * Build a full path with filters appended as query string.
 */
export function buildFilteredPath(
  basePath: string,
  keywords: string[],
  locations: SerializableLocation[],
  extra?: Record<string, string>,
  occupations?: SerializableOccupation[],
  seniorities?: SerializableSeniority[],
  technologies?: SerializableTechnology[],
  workMode?: WorkMode[],
): string {
  const params = new URLSearchParams();
  if (keywords.length > 0) params.set("q", keywords.join(","));
  if (locations.length > 0) {
    params.set("loc", locations.map((l) => l.slug).join(","));
  }
  if (occupations && occupations.length > 0) {
    params.set("occ", occupations.map((o) => o.slug).join(","));
  }
  if (seniorities && seniorities.length > 0) {
    params.set("sen", seniorities.map((s) => s.slug).join(","));
  }
  if (technologies && technologies.length > 0) {
    params.set("tech", technologies.map((t) => t.slug).join(","));
  }
  if (workMode && workMode.length > 0) {
    params.set("wm", workMode.join(","));
  }
  if (extra) {
    for (const [k, v] of Object.entries(extra)) {
      if (v) params.set(k, v);
    }
  }
  const qs = params.toString();
  return basePath + (qs ? `?${qs}` : "");
}
