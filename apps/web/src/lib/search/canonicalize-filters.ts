import { canonicalStringCompare } from "@/lib/sort";

/**
 * Superset of every filter shape used by the taxonomy/locations server
 * actions whose Redis cache keys derive from `JSON.stringify(filters)`.
 * Each consumer threads through a strict subset (e.g.
 * `getGlobalLocationsGrouped` omits `locationIds`; `getAllOccupationsGrouped`
 * omits `occupationIds`); we accept the union so a single helper covers
 * every site without duplicating the per-field sort dance.
 *
 * Fields that are not arrays (e.g. `companyId`) pass through unchanged —
 * `JSON.stringify` already produces a deterministic key for scalars, and
 * V8 preserves object insertion order for non-numeric keys.
 */
export interface CanonicalizableFilters {
  companyId?: string;
  keywords?: string[];
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  languages?: string[];
}

/**
 * Return a new filter object whose array fields are sorted into canonical
 * order. The output is shaped so that
 * `JSON.stringify(canonicalizeFilters(f))` is stable across permutations of
 * the input arrays — `{locationIds:[42,7]}` and `{locationIds:[7,42]}`
 * collapse to the same key.
 *
 * String arrays sort with `canonicalStringCompare` (locale-independent
 * `Intl.Collator("en", { sensitivity: "base" })`) — the raw `Array#sort()`
 * uses UTF-16 code unit order, where `"ü"` (U+00FC) sorts after `"z"`
 * (U+007A). That would produce different cache keys for
 * `["python","übung","zoom"]` depending on the caller's input
 * permutation. See #3221.
 *
 * Numeric ID arrays sort with the standard numeric comparator. Bare
 * `.sort()` would coerce them to strings (so `[10, 2]` sorts to `[10, 2]`
 * not `[2, 10]`), splitting the cache further. See #3187.
 *
 * Pure function: the input is not mutated. Empty arrays survive as
 * empty arrays (not collapsed to `undefined`) so the caller's downstream
 * `buildFilterString` continues to see the same shape, and the cache key
 * for `{keywords: []}` stays distinguishable from `{}` (matches the prior
 * `JSON.stringify(filters)` behaviour).
 *
 * Closes #3187 — the bug fires whenever two callers produce semantically
 * identical filters in different array order, splitting the Redis cache
 * across `n!` permutations per dimension.
 */
export function canonicalizeFilters<T extends CanonicalizableFilters>(filters: T): T {
  // Build the output in a fixed key order. Object insertion order is the
  // tertiary stability axis (V8 preserves it for `JSON.stringify`), so
  // even if a caller passes `{technologyIds, keywords}` and another
  // passes `{keywords, technologyIds}`, both produce the same JSON.
  const out: CanonicalizableFilters = {};
  if (filters.companyId !== undefined) out.companyId = filters.companyId;
  if (filters.keywords !== undefined) {
    out.keywords = [...filters.keywords].sort(canonicalStringCompare);
  }
  if (filters.locationIds !== undefined) {
    out.locationIds = [...filters.locationIds].sort((a, b) => a - b);
  }
  if (filters.occupationIds !== undefined) {
    out.occupationIds = [...filters.occupationIds].sort((a, b) => a - b);
  }
  if (filters.seniorityIds !== undefined) {
    out.seniorityIds = [...filters.seniorityIds].sort((a, b) => a - b);
  }
  if (filters.technologyIds !== undefined) {
    out.technologyIds = [...filters.technologyIds].sort((a, b) => a - b);
  }
  if (filters.languages !== undefined) {
    out.languages = [...filters.languages].sort(canonicalStringCompare);
  }
  return out as T;
}
