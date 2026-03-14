/**
 * Shared query-parameter helpers for filter state in URLs.
 *
 * Both the search page and the company page use the same `q` / `loc` format,
 * so links between them can carry filters across.
 */

export interface SerializableLocation {
  id: number;
  slug: string;
  name: string;
  type: string;
  parentName?: string | null;
}

/**
 * Build a query string (without leading `?`) from keywords + locations.
 * Returns an empty string when there are no filters.
 */
export function buildFilterQuery(
  keywords: string[],
  locations: SerializableLocation[],
): string {
  const params = new URLSearchParams();
  if (keywords.length > 0) params.set("q", keywords.join(","));
  if (locations.length > 0) {
    params.set("loc", locations.map((l) => l.slug).join(","));
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
): string {
  const params = new URLSearchParams();
  if (keywords.length > 0) params.set("q", keywords.join(","));
  if (locations.length > 0) {
    params.set("loc", locations.map((l) => l.slug).join(","));
  }
  if (extra) {
    for (const [k, v] of Object.entries(extra)) {
      if (v) params.set(k, v);
    }
  }
  const qs = params.toString();
  return basePath + (qs ? `?${qs}` : "");
}
