/**
 * Regression tests for `usePaginatedLoadMore` (#3333).
 *
 * The "0 active jobs" symptom from #3333 traced back to one bug here:
 * `loadMore` previously did `setTotal(result.total)` unconditionally.
 * When an anon viewer's infinite-scroll sentinel auto-fired past the
 * `ANON_MAX_WATCHLIST_POSTINGS` cap, `runGetWatchlistPostings` and
 * `getWatchlistPostings` short-circuit to `{ postings: [], total: 0,
 * truncated: true }` — a UI boundary, not a real total. The unconditional
 * setter clobbered the legit `initialTotal` (e.g. 38,717) to 0.
 *
 * The fix uses `setTotal((prev) => Math.max(prev, result.total,
 * items.length))` so an anon-cap shortcut never makes the badge shrink
 * below what the page already shows.
 */
import { render, act } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useEffect } from "react";
import { usePaginatedLoadMore } from "../use-paginated-load-more";

interface Item {
  id: string;
  label: string;
}

function makeItems(count: number, offset = 0): Item[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `i-${i + offset}`,
    label: `item ${i + offset}`,
  }));
}

function Harness({
  initialItems,
  initialTotal,
  fetcher,
  onState,
  trigger,
}: {
  initialItems: Item[];
  initialTotal: number;
  fetcher: (params: { offset: number; limit: number }) => Promise<{
    postings: Item[];
    total: number;
    truncated?: boolean;
  }>;
  onState: (s: { total: number; hasMore: boolean; items: Item[]; truncated: boolean }) => void;
  trigger?: { loadMore: () => Promise<void> };
}) {
  const state = usePaginatedLoadMore<Item>({
    initialItems,
    initialTotal,
    batchSize: 20,
    itemKey: (it) => it.id,
    fetcher,
  });
  useEffect(() => {
    onState({
      total: state.total,
      hasMore: state.hasMore,
      items: state.items,
      truncated: state.truncated,
    });
  });
  if (trigger) trigger.loadMore = state.loadMore;
  return null;
}

describe("usePaginatedLoadMore — #3333 anon-cap regression", () => {
  it("does NOT shrink `total` when loadMore returns the anon-cap shortcut", async () => {
    const initialItems = makeItems(20);
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce({ postings: [], total: 0, truncated: true });
    let snapshot = { total: -1, hasMore: false, items: [] as Item[], truncated: false };
    const trigger = { loadMore: async () => {} };
    render(
      <Harness
        initialItems={initialItems}
        initialTotal={38717}
        fetcher={fetcher}
        onState={(s) => {
          snapshot = s;
        }}
        trigger={trigger}
      />,
    );

    // Trigger the sentinel-style load.
    await act(async () => {
      await trigger.loadMore();
    });

    expect(fetcher).toHaveBeenCalledTimes(1);
    // The legit total survives.
    expect(snapshot.total).toBe(38717);
    // The page reports truncated=true so the load-more affordance hides.
    expect(snapshot.truncated).toBe(true);
    expect(snapshot.hasMore).toBe(false);
    // No items added (the anon-cap shortcut returned an empty page).
    expect(snapshot.items).toHaveLength(20);
  });

  it("still updates `total` when a real next page comes back with a different (legit) total", async () => {
    // The server may revise `total` between pages (e.g. a posting was
    // marked inactive between calls). The hook should pick the larger
    // of {prev, server-reported total, committed items length} so the
    // badge never lies about how many items already exist.
    const initialItems = makeItems(20);
    const nextPage = makeItems(20, 20);
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce({ postings: nextPage, total: 41, truncated: false });
    let snapshot = { total: -1, hasMore: false, items: [] as Item[], truncated: false };
    const trigger = { loadMore: async () => {} };
    render(
      <Harness
        initialItems={initialItems}
        initialTotal={40}
        fetcher={fetcher}
        onState={(s) => {
          snapshot = s;
        }}
        trigger={trigger}
      />,
    );

    await act(async () => {
      await trigger.loadMore();
    });

    expect(fetcher).toHaveBeenCalledTimes(1);
    // 41 > 40 (prev) > 40 (items.length) → pick 41.
    expect(snapshot.total).toBe(41);
    expect(snapshot.items).toHaveLength(40);
  });

  it("clamps `total` to committed items length when the server underreports", async () => {
    // Pathological server response: it reports a `total` smaller than
    // what we already have committed (a stale snapshot). The hook
    // keeps the larger value so the badge never undercounts visible
    // rows.
    const initialItems = makeItems(20);
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce({ postings: [], total: 5, truncated: false });
    let snapshot = { total: -1, hasMore: false, items: [] as Item[], truncated: false };
    const trigger = { loadMore: async () => {} };
    render(
      <Harness
        initialItems={initialItems}
        initialTotal={40}
        fetcher={fetcher}
        onState={(s) => {
          snapshot = s;
        }}
        trigger={trigger}
      />,
    );

    await act(async () => {
      await trigger.loadMore();
    });

    // Floor is max(40, 5, 20) = 40 — the previous total holds.
    expect(snapshot.total).toBe(40);
  });
});
