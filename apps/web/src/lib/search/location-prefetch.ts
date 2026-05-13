/**
 * Client-side prefetch + memoization for the LocationSearchModal's
 * cursor=0 page (#3031).
 *
 * Two pain points the modal hit before this layer existed:
 *
 *   1. **Cold open** — even with the paged action (#2982), the very first
 *      `getGlobalLocationsPage(locale, 0, filters)` call from the modal's
 *      first-page useEffect blocked on a Typesense facet query (~1 s) plus
 *      Postgres hierarchy + macro members (~500 ms). Users saw a 2–4 s
 *      spinner.
 *   2. **Warm reopen** — the modal `setPages([])`s on close (#3000) so the
 *      next open re-fetches. Even with the Redis cache warm, the server
 *      action round-trip is ~500 ms because Upstash sits cross-region from
 *      Vercel and the payload is ~150 KB of JSON. Reopens shouldn't pay any
 *      round-trip.
 *
 * This module is a small module-scoped client cache keyed by
 * `(locale, sortedFiltersJSON, cursor)`. It stores either an in-flight
 * promise (so a click after a hover-prefetch reuses the same call) or a
 * resolved page (so a reopen returns synchronously). The cache is bounded
 * (LRU-ish via a Map insertion-order eviction) so it cannot grow without
 * bound over a long-lived session.
 *
 * The modal first consults {@link getCachedLocationsFirstPage}; if the
 * cache has an entry it awaits the cached promise/value instead of firing a
 * fresh server action. AdvancedSearchPanel kicks off
 * {@link prefetchLocationsFirstPage} when the Filters panel expands AND on
 * Location-button hover, shifting the latency off the click path.
 */

import type { GlobalLocationsPage } from "@/lib/actions/locations";

/** Filter shape mirrors `LocationSearchModalProps['filters']`. */
export type LocationModalFilters = {
  companyId?: string;
  keywords?: string[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  languages?: string[];
};

/**
 * Soft TTL on resolved entries. Tracks the server-side Redis TTL (3600 s)
 * but capped lower because the client may sit on a stale snapshot across
 * a long-running tab. After this window the next open re-fetches; in the
 * meantime reopens are instant.
 */
const RESOLVED_TTL_MS = 5 * 60 * 1000;

/**
 * Max number of entries kept in memory. A single tab will rarely exceed
 * one or two entries (one per active filter shape), but defensively
 * bounded to keep the cache from unbounded growth across page navigations
 * within an SPA session.
 */
const MAX_ENTRIES = 16;

type ResolvedEntry = {
  kind: "resolved";
  value: GlobalLocationsPage;
  cachedAt: number;
};

type InflightEntry = {
  kind: "inflight";
  promise: Promise<GlobalLocationsPage>;
  startedAt: number;
};

type CacheEntry = ResolvedEntry | InflightEntry;

const _cache = new Map<string, CacheEntry>();

function _stableFilterKey(filters: LocationModalFilters | undefined): string {
  if (!filters) return "";
  // Sort keys so two callers passing the same logical filter with
  // different property iteration orders hit the same cache slot.
  const sortedKeys = Object.keys(filters).sort() as (keyof LocationModalFilters)[];
  const normalized: Record<string, unknown> = {};
  for (const k of sortedKeys) {
    const v = filters[k];
    if (v === undefined || v === null) continue;
    if (Array.isArray(v) && v.length === 0) continue;
    // Sort array values too — e.g. [1,2] and [2,1] should share a slot.
    normalized[k] = Array.isArray(v) ? [...v].sort() : v;
  }
  return JSON.stringify(normalized);
}

function _key(locale: string, cursor: number, filters: LocationModalFilters | undefined): string {
  return `${locale}|${cursor}|${_stableFilterKey(filters)}`;
}

/**
 * LRU-ish eviction: when the cache is at capacity, drop the
 * oldest-inserted entry (Map iteration order = insertion order).
 */
function _evictIfFull(): void {
  while (_cache.size >= MAX_ENTRIES) {
    const oldestKey = _cache.keys().next().value;
    if (oldestKey === undefined) break;
    _cache.delete(oldestKey);
  }
}

/**
 * Read the cached first-page entry (cursor=0) for the given locale +
 * filters. Returns either a resolved value, a pending promise, or `null`
 * if there's no cache entry / the resolved entry has expired.
 *
 * Callers receive a `Promise<GlobalLocationsPage>` they can `await`
 * uniformly — for resolved entries we wrap the value in `Promise.resolve`.
 */
export function getCachedLocationsFirstPage(
  locale: string,
  filters: LocationModalFilters | undefined,
): Promise<GlobalLocationsPage> | null {
  const key = _key(locale, 0, filters);
  const entry = _cache.get(key);
  if (!entry) return null;
  if (entry.kind === "inflight") return entry.promise;
  // Resolved — check TTL
  if (Date.now() - entry.cachedAt > RESOLVED_TTL_MS) {
    _cache.delete(key);
    return null;
  }
  return Promise.resolve(entry.value);
}

/**
 * Synchronous variant of {@link getCachedLocationsFirstPage}. Returns the
 * cached value only if the entry is already *resolved* and not expired —
 * returns `null` for inflight entries or empty slots. Used by the modal to
 * pre-seed its initial state so a warm reopen renders content in the first
 * commit without a setState round-trip through useEffect.
 */
export function getCachedLocationsFirstPageSync(
  locale: string,
  filters: LocationModalFilters | undefined,
): GlobalLocationsPage | null {
  const key = _key(locale, 0, filters);
  const entry = _cache.get(key);
  if (!entry || entry.kind !== "resolved") return null;
  if (Date.now() - entry.cachedAt > RESOLVED_TTL_MS) {
    _cache.delete(key);
    return null;
  }
  return entry.value;
}

/**
 * Kick off (or reuse) a first-page fetch for the given locale + filters.
 * The returned promise resolves to the page. Calling this multiple times
 * before resolution returns the same in-flight promise (no fan-out to the
 * server). Calling after resolution returns the cached value (within TTL).
 *
 * The `fetcher` argument is the server-action function passed in by the
 * caller so this module doesn't directly import the `"use server"` file
 * (which would bind it at module level and surface a server-only module
 * to client code paths that don't need it).
 */
export function prefetchLocationsFirstPage(
  locale: string,
  filters: LocationModalFilters | undefined,
  fetcher: (locale: string, cursor: number, filters: LocationModalFilters | undefined) => Promise<GlobalLocationsPage>,
): Promise<GlobalLocationsPage> {
  const cached = getCachedLocationsFirstPage(locale, filters);
  if (cached) return cached;

  _evictIfFull();

  const key = _key(locale, 0, filters);
  const promise = fetcher(locale, 0, filters).then(
    (value) => {
      // Replace the inflight entry with a resolved entry — even if the
      // same key was overwritten with a fresher inflight in the
      // meantime, we leave that one alone (it's newer).
      const current = _cache.get(key);
      if (current?.kind === "inflight" && current.promise === promise) {
        _cache.set(key, { kind: "resolved", value, cachedAt: Date.now() });
      }
      return value;
    },
    (err) => {
      // Drop the failed slot so the next caller retries the upstream
      // instead of waiting on a rejected promise.
      const current = _cache.get(key);
      if (current?.kind === "inflight" && current.promise === promise) {
        _cache.delete(key);
      }
      throw err;
    },
  );

  _cache.set(key, { kind: "inflight", promise, startedAt: Date.now() });
  return promise;
}

/**
 * Test-only / dev hatch: wipe the entire cache. Used by the modal's
 * vitest unit tests to keep cases independent.
 */
export function _clearLocationsPrefetchCache(): void {
  _cache.clear();
}
