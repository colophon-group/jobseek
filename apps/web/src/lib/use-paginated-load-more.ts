import { useEffect, useRef, useState } from "react";

export interface PaginatedFetchResult<T> {
  postings: T[];
  total: number;
  truncated?: boolean;
}

export interface UsePaginatedLoadMoreOptions<T> {
  /** Initial page returned by the server (typically rendered SSR). */
  initialItems: T[];
  /** Server-reported total matching the current filter set. */
  initialTotal: number;
  /** Page size requested per fetch. */
  batchSize: number;
  /**
   * Stable function returning a unique key per item — used by
   * client-side dedup so a race between filter-change and load-more
   * cannot insert duplicates.
   */
  itemKey: (item: T) => string;
  /**
   * Fetches the next page. The hook computes `offset` from the
   * currently-committed item list — callers should NOT track offset
   * themselves.
   */
  fetcher: (params: {
    offset: number;
    limit: number;
  }) => Promise<PaginatedFetchResult<T>>;
  /**
   * Bumped to force a full reset (e.g. when filters change). The hook
   * re-fetches page 1 and replaces the local state.
   */
  resetKey?: unknown;
}

/**
 * Generic infinite-scroll pagination state machine, factored out of
 * `watchlist-job-list.tsx` so the algorithm can be unit-tested in
 * isolation and reused by other callers.
 *
 * Terminal conditions (any one suffices) — the loop stops here:
 *   - the server returned fewer items than the batch size,
 *   - every returned id was already in the committed list
 *     (dedup yielded zero growth — used to be the gap that caused
 *     issue #3038's runaway loop),
 *   - the post-update length reaches or exceeds the server-reported
 *     `total`,
 *   - the server reports `truncated: true` (anon cap).
 */
export function usePaginatedLoadMore<T>({
  initialItems,
  initialTotal,
  batchSize,
  itemKey,
  fetcher,
  resetKey,
}: UsePaginatedLoadMoreOptions<T>) {
  const [items, setItems] = useState(initialItems);
  const [total, setTotal] = useState(initialTotal);
  const [exhausted, setExhausted] = useState(
    initialItems.length >= initialTotal,
  );
  const [truncated, setTruncated] = useState(false);

  // The fetcher closure can be redefined every render — keep a ref
  // so the stable `loadMore` returned below always calls the latest.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const itemKeyRef = useRef(itemKey);
  itemKeyRef.current = itemKey;

  // Snapshot of the initial reset key so we can skip the
  // reset-on-mount fetch (the server-provided initialItems already
  // cover page 1).
  const initialResetKeyRef = useRef(resetKey);

  // Capture the latest committed `items` for the load-more offset.
  // Keeping the ref in sync with state avoids a stale closure inside
  // an in-flight fetch.
  const itemsRef = useRef(items);
  itemsRef.current = items;

  // `fetcherRef` is intentionally read via ref (not listed in deps)
  // so callers can pass a fresh closure each render without triggering
  // a reset. The reset is keyed on `resetKey` only.
  useEffect(() => {
    if (resetKey === initialResetKeyRef.current) return;
    let cancelled = false;
    fetcherRef.current({ offset: 0, limit: batchSize }).then((result) => {
      if (cancelled) return;
      setItems(result.postings);
      setTotal(result.total);
      setExhausted(result.postings.length >= result.total);
      setTruncated(result.truncated ?? false);
    });
    return () => {
      cancelled = true;
    };
  }, [resetKey, batchSize]);

  async function loadMore() {
    const baseLength = itemsRef.current.length;
    const result = await fetcherRef.current({
      offset: baseLength,
      limit: batchSize,
    });
    if (result.truncated) setTruncated(true);

    // Never let the server-reported `total` shrink below what we
    // already have committed locally. The anon-cap shortcut in
    // `runGetWatchlistPostings` / `getWatchlistPostings` returns
    // `{ postings: [], total: 0, truncated: true }` once `offset >=
    // ANON_MAX_WATCHLIST_POSTINGS` — a UI boundary, not a real total.
    // Without this guard the badge collapsed to "0 active" the moment
    // an anon viewer's sentinel scrolled past the cap on a short page
    // (#3333). Taking the max also no-ops the (legit) case where a
    // later fetch returns the same `result.total` we already have.
    setTotal((prev) => Math.max(prev, result.total, itemsRef.current.length));

    // Compute dedup against the latest `items` snapshot upfront so
    // the terminal-condition checks below use a deterministic count.
    // The `setItems` updater runs lazily under React's scheduler so
    // we can't safely capture state inside it.
    const seen = new Set(itemsRef.current.map((it) => itemKeyRef.current(it)));
    const fresh = result.postings.filter(
      (p) => !seen.has(itemKeyRef.current(p)),
    );

    if (fresh.length > 0) {
      setItems((prev) => {
        // Re-dedupe against the latest committed state in case a
        // concurrent filter-change fetch raced this one. Cheap and
        // keeps the invariant "no duplicate keys in the list".
        const seenInPrev = new Set(prev.map((it) => itemKeyRef.current(it)));
        const stillFresh = fresh.filter(
          (p) => !seenInPrev.has(itemKeyRef.current(p)),
        );
        if (stillFresh.length === 0) return prev;
        return [...prev, ...stillFresh];
      });
    }

    // Terminal conditions. The duplicate-page case (`fresh.length === 0`)
    // is the one that used to cause issue #3038 — without it, a fetch
    // returning `batchSize` items that all collide with the current
    // list left `items.length` unchanged → next offset identical →
    // same server response → infinite refetch loop.
    //
    // The `projectedLength >= result.total` check uses the raw
    // server-reported `result.total` (not the floor-guarded
    // committed-state `total`) so the anon-cap shortcut (total: 0)
    // still flips us terminal — we already set `truncated: true`
    // above which alone is enough for `hasMore`, but keeping the
    // exhausted flag in sync prevents a stale "load more" affordance
    // from re-appearing if `truncated` ever gets reset.
    const projectedLength = baseLength + fresh.length;
    if (
      result.postings.length < batchSize ||
      fresh.length === 0 ||
      projectedLength >= result.total
    ) {
      setExhausted(true);
    }
  }

  const hasMore = !exhausted && !truncated;

  return {
    items,
    total,
    exhausted,
    truncated,
    hasMore,
    loadMore,
    setItems,
    setTotal,
    setExhausted,
    setTruncated,
  };
}
