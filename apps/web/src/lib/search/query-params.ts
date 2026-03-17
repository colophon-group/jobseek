/**
 * Shared query-parameter helpers for filter state in URLs.
 *
 * Both the search page and the company page use the same `q` / `loc` / `occ` / `sen` format,
 * so links between them can carry filters across.
 */

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
 * Build a query string (without leading `?`) from keywords + locations + occupations + seniorities.
 * Returns an empty string when there are no filters.
 */
export function buildFilterQuery(
  keywords: string[],
  locations: SerializableLocation[],
  occupations?: SerializableOccupation[],
  seniorities?: SerializableSeniority[],
  technologies?: SerializableTechnology[],
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
  if (extra) {
    for (const [k, v] of Object.entries(extra)) {
      if (v) params.set(k, v);
    }
  }
  const qs = params.toString();
  return basePath + (qs ? `?${qs}` : "");
}
